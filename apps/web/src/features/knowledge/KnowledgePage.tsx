import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { bootstrapRuntimeSession } from "../chat/chatSessionBootstrap";
import { readConversationScope } from "../chat/conversationScope";
import { KnowledgeCanvas } from "./KnowledgeCanvas";
import {
  answerSchema,
  citationSchema,
  conflictsSchema,
  graphSchema,
  kindLabels,
  predicateLabels,
  KnowledgeClient,
  nodeSchema,
  recordSchema,
  stamp,
  statusSchema,
  type Citation,
  type KnowledgeAnswer,
  type KnowledgeEdge,
  type KnowledgeGraph,
  type KnowledgeNode,
  type KnowledgeRecord,
} from "./knowledgeClient";
import "./knowledge.css";

const emptyGraph: KnowledgeGraph = {
  nodes: [],
  edges: [],
  revision: 0,
  policy_epoch: 0,
  truncated: false,
};
const ok = z.object({}).passthrough();
const localTime = (ms: number | null) =>
  ms === null
    ? ""
    : new Date(ms - new Date(ms).getTimezoneOffset() * 60000)
        .toISOString()
        .slice(0, 16);
const timestamp = (text: string) => (text ? new Date(text).getTime() : null);
type Conflict = z.infer<typeof conflictsSchema>["items"][number];

export function KnowledgePage() {
  const embedded = new URLSearchParams(location.search).get("embedded") === "1";
  const [client, setClient] = useState<KnowledgeClient | null>(null);
  const [graph, setGraph] = useState(emptyGraph);
  const [status, setStatus] = useState<z.infer<typeof statusSchema> | null>(
    null,
  );
  const [records, setRecords] = useState<KnowledgeRecord[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [at, setAt] = useState("");
  const [center, setCenter] = useState("");
  const [tab, setTab] = useState("map");
  const [node, setNode] = useState<KnowledgeNode | null>(null);
  const [edge, setEdge] = useState<KnowledgeEdge | null>(null);
  const [refs, setRefs] = useState<Citation[]>([]);
  const [source, setSource] = useState<KnowledgeRecord | null>(null);
  const [draft, setDraft] = useState<KnowledgeRecord | null>(null);
  const [rename, setRename] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [lastMerge, setLastMerge] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<KnowledgeAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const refreshSeq = useRef(0);
  const policyEpoch = useRef<number | null>(null);
  const selectSeq = useRef(0);
  const editOperation = useRef(crypto.randomUUID());
  const device = useRef<string>("");

  useEffect(() => {
    const controller = new AbortController();
    const initialize = async () => {
      device.current =
        localStorage.getItem("cw2.knowledge.device") || crypto.randomUUID();
      localStorage.setItem("cw2.knowledge.device", device.current);
      let session = new URLSearchParams(location.search).get("session_id");
      if (embedded) session = z.string().uuid().parse(session);
      else {
        const scope = await readConversationScope();
        if (scope.participant_id !== "local" || scope.scene_id !== null)
          throw new Error(
            "知识地图仅用于本人私有会话，请先在聊天页切换会话。 ",
          );
        session = (
          await bootstrapRuntimeSession(localStorage, controller.signal)
        ).sessionId;
      }
      if (!controller.signal.aborted && session)
        setClient(new KnowledgeClient(session, embedded, controller.signal));
    };
    void initialize().catch((e: unknown) => {
      if (!controller.signal.aborted)
        setError(e instanceof Error ? e.message : "连接未完成");
    });
    return () => controller.abort();
  }, [embedded]);

  const refresh = useCallback(async () => {
    if (!client) return;
    const sequence = ++refreshSeq.current;
    const params = new URLSearchParams({ q: query, hops: "2", limit: "200" });
    if (kind) params.set("kind", kind);
    if (center) params.set("center", center);
    if (at) params.set("at", String(timestamp(at)));
    const [g, s, r, c] = await Promise.all([
      client.request(`/graph?${params}`, graphSchema),
      client.request("/status", statusSchema),
      client.request(
        `/records?q=${encodeURIComponent(query)}`,
        z.object({ items: z.array(recordSchema) }),
      ),
      client.request("/conflicts", conflictsSchema),
    ]);
    if (sequence !== refreshSeq.current || client.signal.aborted) return;
    if (
      policyEpoch.current !== null &&
      policyEpoch.current !== g.policy_epoch
    ) {
      ++selectSeq.current;
      setNode(null);
      setEdge(null);
      setSource(null);
      setAnswer(null);
      setSelected([]);
    }
    policyEpoch.current = g.policy_epoch;
    setGraph(g);
    setStatus(s);
    setRecords(r.items);
    setConflicts(c.items);
  }, [client, query, kind, center, at]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void refresh().catch((e: unknown) => {
        if (!client?.signal.aborted)
          setError(e instanceof Error ? e.message : "加载失败");
      });
    }, 250);
    return () => {
      clearTimeout(timer);
    };
  }, [refresh, client]);

  useEffect(() => {
    if (!client) return;
    const timer = setInterval(() => {
      if (document.hidden || busy) return;
      void refresh().catch((e: unknown) => {
        if (!client.signal.aborted)
          setError(e instanceof Error ? e.message : "同步状态暂不可用");
      });
    }, 30_000);
    return () => clearInterval(timer);
  }, [client, refresh, busy]);

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      if (!client?.signal.aborted)
        setError(e instanceof Error ? e.message : "操作未完成");
    } finally {
      if (!client?.signal.aborted) setBusy(false);
    }
  }
  async function openSource(ref: Citation) {
    if (!client) return;
    const current = ++selectSeq.current;
    const item = await client.request(
      `/records/${encodeURIComponent(ref.record_id)}?revision=${ref.revision}`,
      recordSchema,
    );
    if (current === selectSeq.current) setSource(item);
  }
  async function choose(id: string, isEdge: boolean) {
    if (!client) return;
    setSource(null);
    setDraft(null);
    const current = ++selectSeq.current;
    if (isEdge) {
      const found = graph.edges.find((e) => e.relation_id === id) ?? null;
      setEdge(found);
      setNode(null);
      setRefs(found?.source_refs ?? []);
      return;
    }
    const result = await client.request(
      `/entities/${encodeURIComponent(id)}`,
      z.object({ node: nodeSchema, sources: z.array(citationSchema) }),
    );
    if (current !== selectSeq.current) return;
    setNode(result.node);
    setRename(result.node.label);
    setRefs(result.sources);
    setEdge(null);
  }
  const sourceButtons = (citations: Citation[]) =>
    citations.map((ref) => (
      <button
        type="button"
        className="km-source-link"
        key={`${ref.record_id}:${ref.revision}`}
        onClick={() => void run(() => openSource(ref))}
      >
        查看原文 · 版本 {ref.revision}
      </button>
    ));
  async function saveDraft() {
    if (!client || !draft || !status) return;
    const receipt = await client.save(
      draft,
      status.policy_epoch,
      device.current,
      editOperation.current,
    );
    await refresh();
    // Conflict receipts are successful HTTP responses, but do not mean the edit was accepted.
    const outcome = receipt.receipts.find(
      (r) => r.operation_id === editOperation.current,
    )?.status;
    if (outcome === "conflict") {
      setNotice("另一端也修改了这项内容，双方版本已保留。请在冲突页核对。");
      setTab("conflicts");
      setDraft(null);
    } else if (outcome === "accepted") {
      setNotice("已保存。手机同步后会更新本地提醒。");
      setDraft(null);
    } else throw new Error("这项修改未被接受，请刷新后核对状态。");
    editOperation.current = crypto.randomUUID();
  }

  const detailRecord = source ?? draft;
  const possibleDuplicates =
    node?.kind === "person"
      ? graph.nodes.filter(
          (n) =>
            n.node_id !== node.node_id &&
            n.kind === "person" &&
            n.label.trim() === node.label.trim() &&
            (!n.stable_identity || !node.stable_identity),
        )
      : [];
  async function forgetRecord(record: KnowledgeRecord) {
    if (!client || !status) return;
    const receipt = await client.save(
      record,
      status.policy_epoch,
      device.current,
      crypto.randomUUID(),
      "forget",
    );
    if (receipt.receipts[0]?.status === "conflict") {
      setTab("conflicts");
      setNotice("资料已有新版本，请核对冲突后决定是否遗忘。");
    } else if (receipt.receipts[0]?.status === "accepted") {
      setNotice("已从知识库遗忘，本机微信原文保留。派生索引正在后台清理。");
    } else throw new Error("遗忘未被接受，请刷新后重试。");
    setSource(null);
    setDraft(null);
    setNode(null);
    setEdge(null);
    await refresh();
  }

  return (
    <main className="knowledge-page">
      <header className="km-header">
        <div>
          <span className="km-eyebrow">宁宁 · 个人资料</span>
          <h1>知识地图</h1>
          <p>把人物、项目与约定连起来，每个结论都能回到原文。</p>
        </div>
        {!embedded && <a href="/settings/knowledge">返回设置</a>}
      </header>
      <div className="km-status" aria-live="polite">
        <span>
          已同步 <strong>{status?.records ?? "—"}</strong> 条
        </span>
        <span>
          索引完成{" "}
          <strong>{status?.indexed_records ?? status?.index.done ?? 0}</strong>
        </span>
        <span>
          处理中{" "}
          <strong>
            {(status?.index.pending ?? 0) + (status?.index.leased ?? 0)}
          </strong>
        </span>
        {!!status?.index.failed && (
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                if (!client) return;
                await client.request("/index/retry", ok, {
                  session_id: client.session,
                });
                await refresh();
              })
            }
          >
            重试失败批次 ({status.index.failed})
          </button>
        )}
        <button disabled={busy || !client} onClick={() => void run(refresh)}>
          刷新
        </button>
      </div>
      {error && (
        <div className="km-alert" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="km-notice" role="status">
          {notice}
        </div>
      )}
      <nav className="km-tabs" aria-label="知识地图栏目">
        {[
          ["map", "关系地图"],
          ["records", "记忆与事项"],
          ["ask", "问宁宁"],
          [
            "conflicts",
            `修改冲突${conflicts.length ? ` (${conflicts.length})` : ""}`,
          ],
        ].map(([id, label]) => (
          <button
            key={id}
            aria-current={tab === id ? "page" : undefined}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      {(tab === "map" || tab === "records") && (
        <div className="km-filters">
          <label>
            搜索
            <input
              placeholder="人物、项目或关键词"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setCenter("");
              }}
            />
          </label>
          <label>
            类型
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">全部类型</option>
              {Object.entries(kindLabels).map(([id, label]) => (
                <option value={id} key={id}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {tab === "map" && (
            <label>
              查看当时的关系
              <input
                type="datetime-local"
                value={at}
                onChange={(e) => setAt(e.target.value)}
              />
            </label>
          )}
          {(center || at) && (
            <button
              onClick={() => {
                setCenter("");
                setAt("");
              }}
            >
              回到当前全景
            </button>
          )}
        </div>
      )}
      {tab === "map" && (
        <>
          <div className="km-legend">
            <span>● 人物</span>
            <span>◆ 项目与事项</span>
            <span>实线：已确认</span>
            <span>虚线：待核实</span>
          </div>
          <KnowledgeCanvas
            graph={graph}
            onSelect={(id, isEdge) => void run(() => choose(id, isEdge))}
          />
          {graph.truncated && (
            <p role="status">
              当前只显示部分关系，请搜索或点击节点展开。单次最多 500 个节点。
            </p>
          )}
          <details
            className="km-node-list"
            open={selected.length > 0 || undefined}
          >
            <summary>节点列表与合并（{graph.nodes.length}）</summary>
            <p>
              只有确认是同一人物或对象时才合并。勾选顺序中的第一个名称将保留，单次最多
              20 个。
            </p>
            <div className="km-actions">
              <button
                disabled={busy || selected.length < 2}
                onClick={() =>
                  void run(async () => {
                    if (!client) return;
                    const operation = crypto.randomUUID();
                    await client.request("/entities/merge", ok, {
                      session_id: client.session,
                      operation_id: operation,
                      source_ids: selected,
                      target_id: selected[0],
                      expected_graph_revision: graph.revision,
                    });
                    setLastMerge(operation);
                    setSelected([]);
                    await refresh();
                  })
                }
              >
                合并所选 ({selected.length})
              </button>
              {lastMerge && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      if (!client) return;
                      await client.request("/entities/split", ok, {
                        session_id: client.session,
                        operation_id: lastMerge,
                        expected_graph_revision: graph.revision,
                      });
                      setLastMerge(null);
                      await refresh();
                    })
                  }
                >
                  撤销这次合并
                </button>
              )}
            </div>
            <ul>
              {graph.nodes.map((n) => (
                <li key={n.node_id}>
                  <input
                    aria-label={`选择 ${n.label}`}
                    type="checkbox"
                    checked={selected.includes(n.node_id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, n.node_id]
                          : selected.filter((id) => id !== n.node_id),
                      )
                    }
                  />
                  <button
                    onClick={() => void run(() => choose(n.node_id, false))}
                  >
                    {n.label}
                  </button>
                  <small>{kindLabels[n.kind]}</small>
                </li>
              ))}
            </ul>
          </details>
          <details className="km-node-list km-edge-list">
            <summary>关系列表（{graph.edges.length}）</summary>
            <ul>
              {graph.edges.map((relation) => (
                <li key={relation.relation_id}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void run(() => choose(relation.relation_id, true))
                    }
                  >
                    {graph.nodes.find(
                      (item) => item.node_id === relation.source,
                    )?.label ?? relation.source}
                    {" → "}
                    {graph.nodes.find(
                      (item) => item.node_id === relation.target,
                    )?.label ?? relation.target}
                    {" · "}
                    {predicateLabels[relation.predicate] ?? relation.predicate}
                  </button>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
      {tab === "records" && (
        <section className="km-record-grid" aria-label="记忆与事项">
          {records
            .filter((r) => r.kind !== "source" && (!kind || r.kind === kind))
            .map((r) => (
              <article key={r.record_id}>
                <span className="km-badge">
                  {kindLabels[r.kind]} · {r.confirmed ? "已确认" : "待确认"}
                </span>
                <h2>{r.title}</h2>
                {r.origin.source_type === "formal_memory" && (
                  <small>来自宁宁长期记忆，请在原有记忆设置中修改。</small>
                )}
                <p>{r.text}</p>
                <small>
                  {{
                    active: "有效",
                    open: "待办",
                    waiting: "等待",
                    blocked: "受阻",
                    completed: "已完成",
                    cancelled: "已取消",
                  }[r.state] ?? r.state}{" "}
                  · 版本 {r.revision}
                  {r.sync?.applied_devices
                    ? " · 手机已应用"
                    : " · 等待手机同步"}
                </small>
                <div className="km-actions">
                  <button
                    disabled={r.origin.source_type === "formal_memory"}
                    onClick={() => {
                      editOperation.current = crypto.randomUUID();
                      setDraft(r);
                      setSource(null);
                      setNode(null);
                      setEdge(null);
                    }}
                  >
                    编辑
                  </button>
                  {sourceButtons(r.source_refs)}
                </div>
              </article>
            ))}
          {!records.some((r) => r.kind !== "source") && (
            <p>手机确认后的记忆、事项和日程会出现在这里。</p>
          )}
        </section>
      )}
      {tab === "ask" && (
        <section className="km-ask">
          <h2>从关系和原文里找答案</h2>
          <p>例如：青禾项目的交付时间改过几次？目前有哪些事项在等对方回复？</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                if (!client) return;
                setAnswer(null);
                setAnswer(
                  await client.request("/query", answerSchema, {
                    session_id: client.session,
                    question,
                    hops: 2,
                    answer: true,
                    at: timestamp(at),
                  }),
                );
              });
            }}
          >
            <label>
              想了解什么
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                maxLength={2000}
                required
              />
            </label>
            <button disabled={busy || !client || !question.trim()}>
              {busy ? "正在核对资料…" : "检索并回答"}
            </button>
          </form>
          {answer && (
            <div aria-live="polite">
              {answer.degraded && (
                <p className="km-notice">
                  图索引暂不可用，本次结果包含可用的关键词与原文检索。
                </p>
              )}
              {answer.claims.map((claim, index) => (
                <article key={index}>
                  <span className="km-badge">
                    {claim.tentative ? "含待核实信息" : "已确认资料"}
                  </span>
                  <p>{claim.text}</p>
                  {sourceButtons(
                    claim.sources.flatMap((key) => {
                      const ref = answer.sources.find((s) => s.key === key);
                      return ref ? [ref] : [];
                    }),
                  )}
                </article>
              ))}
              {answer.limitations && (
                <p className="km-notice">{answer.limitations}</p>
              )}
            </div>
          )}
        </section>
      )}
      {tab === "conflicts" && (
        <section className="km-conflicts">
          <h2>双方修改都已保留</h2>
          {!conflicts.length && <p>当前没有待处理冲突。</p>}
          {conflicts.map((c) => (
            <article key={c.conflict_id}>
              <h3>{c.proposed.record.title || "资料修订"}</h3>
              <p>待提交版本：{c.proposed.record.text}</p>
              <p>服务器版本：{c.current_revision}</p>
              <div className="km-actions">
                <button
                  onClick={() =>
                    void run(() =>
                      openSource({
                        record_id: c.record_id,
                        revision: c.current_revision,
                      }),
                    )
                  }
                >
                  查看服务器版本
                </button>
                {[
                  [true, "采用待提交版本"],
                  [false, "保留服务器版本"],
                ].map(([keep, label]) => (
                  <button
                    key={String(keep)}
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        if (!client) return;
                        await client.request(
                          `/conflicts/${c.conflict_id}/resolve`,
                          ok,
                          {
                            session_id: client.session,
                            keep_local: keep,
                            operation_id: crypto.randomUUID(),
                            expected_revision: c.current_revision,
                          },
                        );
                        await refresh();
                      })
                    }
                  >
                    {label}
                  </button>
                ))}
              </div>
            </article>
          ))}
        </section>
      )}
      {(node || edge || source || draft) && (
        <aside className="km-detail" aria-label="详情">
          <button
            className="km-close"
            onClick={() => {
              ++selectSeq.current;
              setNode(null);
              setEdge(null);
              setSource(null);
              setDraft(null);
            }}
          >
            关闭详情 ×
          </button>
          {source ? (
            <>
              <span className="km-badge">
                原始证据 · 版本 {source.revision}
              </span>
              <h2>{source.title || "消息原文"}</h2>
              <p>
                {stamp(source.occurred_at)} · {source.sender_name}
              </p>
              <pre>{source.text}</pre>
              {embedded &&
                source.kind === "source" &&
                source.origin.source_type === "wechat_root" && (
                  <a
                    href={`infoinbox://source/${encodeURIComponent(source.record_id)}?revision=${source.revision}`}
                  >
                    在本机收件箱查看
                  </a>
                )}
            </>
          ) : draft ? (
            <>
              <h2>编辑{kindLabels[draft.kind]}</h2>
              <p>基于版本 {draft.revision}；原始证据保留。</p>
              <label>
                标题
                <input
                  value={draft.title}
                  onChange={(e) => {
                    editOperation.current = crypto.randomUUID();
                    setDraft({ ...draft, title: e.target.value });
                  }}
                />
              </label>
              <label>
                内容
                <textarea
                  value={draft.text}
                  onChange={(e) => {
                    editOperation.current = crypto.randomUUID();
                    setDraft({ ...draft, text: e.target.value });
                  }}
                />
              </label>
              <label>
                状态
                <select
                  value={draft.state}
                  onChange={(e) => {
                    editOperation.current = crypto.randomUUID();
                    setDraft({ ...draft, state: e.target.value });
                  }}
                >
                  {[
                    ["active", "有效"],
                    ["open", "待办"],
                    ["waiting", "等待"],
                    ["blocked", "受阻"],
                    ["completed", "完成"],
                    ["cancelled", "取消"],
                  ].map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              {(["due_at", "starts_at", "ends_at", "remind_at"] as const).map(
                (field, i) => (
                  <label key={field}>
                    {["截止时间", "开始时间", "结束时间", "提醒时间"][i]}
                    <input
                      type="datetime-local"
                      value={localTime(draft[field])}
                      onChange={(e) => {
                        editOperation.current = crypto.randomUUID();
                        setDraft({
                          ...draft,
                          [field]: timestamp(e.target.value),
                        });
                      }}
                    />
                  </label>
                ),
              )}
              <small>时间按当前设备时区显示。手机同步后才会更新提醒。</small>
              <button
                disabled={busy || !draft.title.trim()}
                onClick={() => void run(saveDraft)}
              >
                保存修改
              </button>
            </>
          ) : (
            <>
              <h2>{node?.label ?? edge?.description}</h2>
              {node && (
                <>
                  <p>{kindLabels[node.kind]}</p>
                  {possibleDuplicates.length > 0 && (
                    <div className="km-notice">
                      <p>
                        发现 {possibleDuplicates.length}{" "}
                        条同名人物记录，身份尚未关联，需要你核对。
                      </p>
                      <button
                        onClick={() => {
                          setSelected([
                            node.node_id,
                            ...possibleDuplicates
                              .slice(0, 19)
                              .map((n) => n.node_id),
                          ]);
                          setNotice(
                            "已勾选同名记录，请在节点列表核对后决定是否合并。",
                          );
                        }}
                      >
                        选择同名记录核对
                      </button>
                    </div>
                  )}
                  <label>
                    显示名称
                    <input
                      value={rename}
                      onChange={(e) => setRename(e.target.value)}
                    />
                  </label>
                  <div className="km-actions">
                    <button
                      disabled={busy || !rename.trim()}
                      onClick={() =>
                        void run(async () => {
                          if (!client) return;
                          await client.request(
                            `/entities/${encodeURIComponent(node.node_id)}`,
                            ok,
                            {
                              session_id: client.session,
                              expected_graph_revision: graph.revision,
                              label: rename,
                            },
                            "PATCH",
                          );
                          setNode({ ...node, label: rename });
                          await refresh();
                        })
                      }
                    >
                      保存名称
                    </button>
                    <button onClick={() => setCenter(node.node_id)}>
                      展开关联
                    </button>
                  </div>
                </>
              )}
              {edge && (
                <>
                  <p>
                    {edge.change_proposed
                      ? "原关系已确认，但新资料提出了变更，需再次核对"
                      : edge.status === "confirmed"
                        ? "已确认"
                        : "待核实"}
                  </p>
                  <p>
                    {stamp(edge.valid_from)} →{" "}
                    {edge.valid_to ? stamp(edge.valid_to) : "持续有效"}
                  </p>
                  <div className="km-actions">
                    {[
                      ["confirm", "确认关系"],
                      ["reject", "驳回关系"],
                    ].map(([decision, label]) => (
                      <button
                        disabled={busy}
                        key={decision}
                        onClick={() =>
                          void run(async () => {
                            if (!client) return;
                            await client.request(
                              `/relations/${encodeURIComponent(edge.relation_id)}/review`,
                              ok,
                              {
                                session_id: client.session,
                                expected_revision: edge.revision,
                                decision,
                              },
                            );
                            setEdge(null);
                            await refresh();
                          })
                        }
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </>
              )}
              <h3>来源证据</h3>
              {sourceButtons(refs)}
            </>
          )}
          {detailRecord &&
            detailRecord.origin.source_type !== "formal_memory" && (
              <details className="km-forget">
                <summary>遗忘这项内容</summary>
                <p>
                  这会停止该内容的知识检索，并清理它的图谱与向量索引。原文的全部服务器版本都会遗忘，本机微信原始记录继续保留。
                </p>
                <button
                  disabled={busy}
                  onClick={() => void run(() => forgetRecord(detailRecord))}
                >
                  确认从知识库遗忘
                </button>
              </details>
            )}
        </aside>
      )}
    </main>
  );
}
