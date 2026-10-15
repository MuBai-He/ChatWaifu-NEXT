import { useRef, useState } from "react";
import { z } from "zod";
import {
  KnowledgeClient,
  predicateLabels,
  stamp,
  type KnowledgeEdge,
  type KnowledgeNode,
} from "./knowledgeClient";

const historySchema = z.object({
  items: z.array(
    z.object({
      action: z.string(),
      at: z.number(),
      previous: z.object({ description: z.string().optional() }).passthrough(),
    }),
  ),
});
const inputTime = (value: number | null) =>
  value === null
    ? ""
    : new Date(value - new Date(value).getTimezoneOffset() * 60000)
        .toISOString()
        .slice(0, 16);

export function RelationEditor({
  client,
  edge,
  nodes,
  busy,
  run,
  onSaved,
}: {
  client: KnowledgeClient;
  edge: KnowledgeEdge;
  nodes: KnowledgeNode[];
  busy: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
  onSaved: () => Promise<void>;
}) {
  const [source, setSource] = useState(edge.source);
  const [target, setTarget] = useState(edge.target);
  const [predicate, setPredicate] = useState(edge.predicate);
  const [description, setDescription] = useState(edge.description);
  const [from, setFrom] = useState(inputTime(edge.valid_from));
  const [to, setTo] = useState(inputTime(edge.valid_to));
  const [history, setHistory] = useState<z.infer<typeof historySchema> | null>(
    null,
  );
  const operation = useRef({ body: "", id: "" });
  return (
    <>
      <details>
        <summary>更正这条关系</summary>
        <p>更正会记录为你的确认内容，保留原文和修改历史。</p>
        <label>
          起点
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            {nodes.map((n) => (
              <option key={n.node_id} value={n.node_id}>
                {n.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          关系
          <select
            value={predicate}
            onChange={(e) => setPredicate(e.target.value)}
          >
            {Object.entries(predicateLabels).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          终点
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            {nodes.map((n) => (
              <option key={n.node_id} value={n.node_id}>
                {n.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          更正内容
          <textarea
            aria-label="更正内容"
            value={description}
            maxLength={1000}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
        <label>
          生效时间
          <input
            type="datetime-local"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label>
          失效时间（留空表示持续有效）
          <input
            type="datetime-local"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
        <button
          disabled={busy || !description.trim()}
          onClick={() =>
            void run(async () => {
              const fields = {
                session_id: client.session,
                expected_revision: edge.revision,
                source,
                target,
                predicate,
                description,
                valid_from: from ? new Date(from).getTime() : null,
                valid_to: to ? new Date(to).getTime() : null,
              };
              const body = JSON.stringify(fields);
              if (operation.current.body !== body)
                operation.current = { body, id: crypto.randomUUID() };
              await client.request(
                `/relations/${encodeURIComponent(edge.relation_id)}/correct`,
                z.object({ updated: z.boolean() }),
                { ...fields, operation_id: operation.current.id },
              );
              await onSaved();
            })
          }
        >
          确认并保存更正
        </button>
      </details>
      <button
        disabled={busy}
        onClick={() =>
          void run(async () =>
            setHistory(
              await client.request(
                `/relations/${encodeURIComponent(edge.relation_id)}/history`,
                historySchema,
              ),
            ),
          )
        }
      >
        查看修改历史
      </button>
      {history && (
        <div>
          {history.items.length === 0 ? (
            <p>没有可用的修改历史。</p>
          ) : (
            history.items.map((item, i) => (
              <article key={i}>
                <small>
                  {stamp(item.at)} ·{" "}
                  {{
                    confirm: "确认",
                    reject: "驳回",
                    correct: "更正",
                    invalidation_proposed: "新资料提出变更",
                    forget: "遗忘",
                  }[item.action] ?? item.action}
                </small>
                <p>{item.previous.description ?? "请结合来源核对变更。"}</p>
              </article>
            ))
          )}
        </div>
      )}
    </>
  );
}
