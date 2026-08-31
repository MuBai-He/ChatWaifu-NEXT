import type {
  MemoryProposal,
  MemoryRecord,
  MemorySource,
} from "@chatwaifu/protocol";
import { useState } from "react";
import { ProductIcon } from "../../components/ProductIcon";
import { ModalPortal } from "./ModalPortal";
import {
  correctMemory,
  decideMemoryProposal,
  forgetMemory,
  getMemoryProposals,
  getMemoryRecords,
  getMemorySources,
  setMemoryPinned,
} from "./runtimeClient";
import type { MemoryItem } from "./types";

interface MemoryControlCenterProps {
  sessionId: string | null;
  onChanged: () => Promise<void>;
}

interface EditState {
  memoryId: string;
  text: string;
}

export function MemoryControlCenter({
  sessionId,
  onChanged,
}: MemoryControlCenterProps) {
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState<MemoryItem[]>([]);
  const [proposals, setProposals] = useState<MemoryProposal[]>([]);
  const [sources, setSources] = useState<Record<string, MemorySource[]>>({});
  const [kind, setKind] = useState("");
  const [sensitivity, setSensitivity] = useState("");
  const [editing, setEditing] = useState<EditState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = async (
    filters: { kind?: string; sensitivity?: string } = {},
  ) => {
    const [nextRecords, nextProposals] = await Promise.all([
      getMemoryRecords({
        kind: (filters.kind ?? kind) || undefined,
        sensitivity: (filters.sensitivity ?? sensitivity) || undefined,
      }),
      getMemoryProposals(),
    ]);
    setRecords(nextRecords);
    setProposals(nextProposals);
  };

  const openCenter = () => {
    setOpen(true);
    setNotice(null);
    void refresh().catch((error: unknown) => setNotice(errorMessage(error)));
  };

  const act = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key);
    setNotice(null);
    try {
      await action();
      await Promise.all([refresh(), onChanged()]);
    } catch (error: unknown) {
      setNotice(errorMessage(error));
    } finally {
      setBusy(null);
    }
  };

  const decide = async (
    proposal: MemoryProposal,
    decision: "accept" | "reject",
  ) => {
    if (!sessionId) return;
    if (
      decision === "accept" &&
      proposal.candidate?.sensitivity === "sensitive" &&
      !window.confirm("这条建议包含敏感信息。确定允许保存到本地记忆吗？")
    )
      return;
    await act(`proposal:${proposal.proposal_id}`, () =>
      decideMemoryProposal(sessionId, proposal.proposal_id, decision),
    );
  };

  const loadSources = async (memoryId: string) => {
    if (sources[memoryId]) {
      setSources((current) => {
        const next = { ...current };
        delete next[memoryId];
        return next;
      });
      return;
    }
    await act(`sources:${memoryId}`, async () => {
      const items = await getMemorySources(memoryId);
      setSources((current) => ({ ...current, [memoryId]: items }));
    });
  };

  const saveCorrection = async () => {
    if (!sessionId || !editing?.text.trim()) return;
    await act(`edit:${editing.memoryId}`, () =>
      correctMemory(sessionId, editing.memoryId, editing.text.trim()),
    );
    setEditing(null);
  };

  const togglePinned = async (record: MemoryRecord) => {
    if (!sessionId) return;
    await act(`pin:${record.memory_id}`, () =>
      setMemoryPinned(sessionId, record.memory_id, !record.pinned),
    );
  };

  const remove = async (record: MemoryRecord) => {
    if (!sessionId) return;
    if (
      !window.confirm("确定忘记这条记忆吗？它会保留审计墓碑，但不会再被召回。")
    )
      return;
    await act(`forget:${record.memory_id}`, () =>
      forgetMemory(sessionId, record.memory_id),
    );
  };

  return (
    <>
      <button type="button" onClick={openCenter} disabled={!sessionId}>
        <ProductIcon name="memory" />
        记忆中心
        {proposals.length ? (
          <span className="memory-proposal-badge">{proposals.length}</span>
        ) : null}
      </button>
      {open ? (
        <ModalPortal
          className="memory-center-overlay"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <section
            className="memory-center"
            role="dialog"
            aria-modal="true"
            aria-label="结构化记忆中心"
          >
            <header>
              <div>
                <p>MEMORY SCHEME A</p>
                <h2>结构化记忆</h2>
                <span>普通对话先建议；明确记住立即提交；敏感内容逐条确认</span>
              </div>
              <button
                type="button"
                aria-label="关闭记忆中心"
                onClick={() => setOpen(false)}
              >
                <ProductIcon name="close" />
              </button>
            </header>

            {notice ? (
              <div className="memory-notice" role="alert">
                {notice}
              </div>
            ) : null}

            <div className="memory-center-grid">
              <div className="memory-column">
                <div className="memory-section-title">
                  <strong>待审核建议</strong>
                  <small>{proposals.length}</small>
                </div>
                {proposals.length ? (
                  proposals.map((proposal) => (
                    <article
                      className="memory-proposal-card"
                      key={proposal.proposal_id}
                    >
                      <div>
                        <span>{proposal.operation}</span>
                        <code>{proposal.candidate?.kind ?? "unknown"}</code>
                      </div>
                      <p>{proposal.candidate?.text ?? "无候选内容"}</p>
                      <small>
                        {proposal.rationale} ·{" "}
                        {Math.round(proposal.confidence * 100)}%
                      </small>
                      {proposal.candidate?.sensitivity === "sensitive" ? (
                        <strong className="sensitive-label">敏感信息</strong>
                      ) : null}
                      <div className="memory-card-actions">
                        <button
                          type="button"
                          disabled={busy === `proposal:${proposal.proposal_id}`}
                          onClick={() => void decide(proposal, "accept")}
                        >
                          接受
                        </button>
                        <button
                          type="button"
                          className="danger"
                          disabled={busy === `proposal:${proposal.proposal_id}`}
                          onClick={() => void decide(proposal, "reject")}
                        >
                          拒绝
                        </button>
                      </div>
                    </article>
                  ))
                ) : (
                  <p className="memory-empty">没有待审核建议。</p>
                )}
              </div>

              <div className="memory-column">
                <div className="memory-filters">
                  <label>
                    类型
                    <select
                      value={kind}
                      onChange={(event) => {
                        const nextKind = event.target.value;
                        setKind(nextKind);
                        void refresh({ kind: nextKind }).catch(
                          (error: unknown) => setNotice(errorMessage(error)),
                        );
                      }}
                    >
                      <option value="">全部</option>
                      <option value="semantic.fact">事实</option>
                      <option value="semantic.preference">偏好</option>
                      <option value="procedural.preference">交互方式</option>
                      <option value="prospective.commitment">承诺</option>
                    </select>
                  </label>
                  <label>
                    隐私
                    <select
                      value={sensitivity}
                      onChange={(event) => {
                        const nextSensitivity = event.target.value;
                        setSensitivity(nextSensitivity);
                        void refresh({ sensitivity: nextSensitivity }).catch(
                          (error: unknown) => setNotice(errorMessage(error)),
                        );
                      }}
                    >
                      <option value="">全部</option>
                      <option value="private">私有</option>
                      <option value="sensitive">敏感</option>
                    </select>
                  </label>
                  <small>{records.length} 条有效记忆</small>
                </div>

                {records.map((record) => (
                  <article
                    className="memory-record-card"
                    key={record.memory_id}
                  >
                    <div className="memory-record-heading">
                      <div>
                        <span>{record.pinned ? "核心" : record.kind}</span>
                        <code>{record.sensitivity}</code>
                      </div>
                      <small>{Math.round(record.importance * 100)}%</small>
                    </div>
                    {editing?.memoryId === record.memory_id ? (
                      <div className="memory-edit">
                        <textarea
                          rows={3}
                          value={editing.text}
                          aria-label="修正记忆内容"
                          onChange={(event) =>
                            setEditing({
                              memoryId: record.memory_id,
                              text: event.target.value,
                            })
                          }
                        />
                        <button
                          type="button"
                          onClick={() => void saveCorrection()}
                        >
                          保存修正
                        </button>
                        <button type="button" onClick={() => setEditing(null)}>
                          取消
                        </button>
                      </div>
                    ) : (
                      <p>{record.text}</p>
                    )}
                    <div className="memory-card-actions">
                      <button
                        type="button"
                        onClick={() =>
                          setEditing({
                            memoryId: record.memory_id,
                            text: record.text,
                          })
                        }
                      >
                        修正
                      </button>
                      <button
                        type="button"
                        onClick={() => void togglePinned(record)}
                      >
                        {record.pinned ? "取消核心" : "设为核心"}
                      </button>
                      <button
                        type="button"
                        onClick={() => void loadSources(record.memory_id)}
                      >
                        {sources[record.memory_id] ? "收起来源" : "查看来源"}
                      </button>
                      <button
                        className="danger"
                        type="button"
                        onClick={() => void remove(record)}
                      >
                        忘记
                      </button>
                    </div>
                    {sources[record.memory_id] ? (
                      <ul className="memory-sources">
                        {sources[record.memory_id]?.map((source) => (
                          <li key={source.source_id}>
                            {source.source_kind} · event{" "}
                            {source.source_event_id.slice(0, 8)} · turn{` `}
                            {source.turn_id?.slice(0, 8) ?? "management"}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </article>
                ))}
                {!records.length ? (
                  <p className="memory-empty">当前筛选下没有记忆。</p>
                ) : null}
              </div>
            </div>
          </section>
        </ModalPortal>
      ) : null}
    </>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "记忆操作失败";
}
