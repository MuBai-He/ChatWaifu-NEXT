import { z } from "zod";
import {
  requestRuntime,
  RuntimeRequestError,
  type RuntimeResponseParser,
} from "../chat/runtime-client/http";

export const citationSchema = z.object({
  record_id: z.string(),
  revision: z.number().int(),
});
export type Citation = z.infer<typeof citationSchema>;
export const nodeSchema = z.object({
  node_id: z.string(),
  kind: z.string(),
  label: z.string(),
  room_id: z.string(),
  stable_identity: z.string().nullable().optional(),
});
export const edgeSchema = z.object({
  change_proposed: z.boolean().optional(),
  relation_id: z.string(),
  source: z.string(),
  target: z.string(),
  predicate: z.string(),
  description: z.string(),
  revision: z.number().int(),
  status: z.string(),
  valid_from: z.number().nullable(),
  valid_to: z.number().nullable(),
  source_refs: z.array(citationSchema),
});
export const graphSchema = z.object({
  nodes: z.array(nodeSchema).max(500),
  edges: z.array(edgeSchema).max(1500),
  revision: z.number(),
  policy_epoch: z.number(),
  truncated: z.boolean(),
});
export type KnowledgeGraph = z.infer<typeof graphSchema>;
export type KnowledgeNode = z.infer<typeof nodeSchema>;
export type KnowledgeEdge = z.infer<typeof edgeSchema>;
export const recordSchema = z.object({
  sync: z
    .object({
      applied_devices: z.number(),
      last_applied_at: z.number().nullable(),
    })
    .optional(),
  record_id: z.string(),
  kind: z.string(),
  room_id: z.string(),
  title: z.string(),
  text: z.string(),
  state: z.string(),
  confirmed: z.boolean(),
  occurred_at: z.number(),
  due_at: z.number().nullable(),
  starts_at: z.number().nullable(),
  ends_at: z.number().nullable(),
  remind_at: z.number().nullable(),
  timezone: z.string(),
  source_refs: z.array(citationSchema),
  sender_id: z.string().nullable(),
  sender_name: z.string(),
  sent_by_owner: z.boolean(),
  origin: z.record(z.string(), z.unknown()),
  revision: z.number(),
  deleted: z.boolean(),
  updated_at: z.number(),
});
export type KnowledgeRecord = z.infer<typeof recordSchema>;
export const statusSchema = z.object({
  indexed_records: z.number().optional(),
  records: z.number(),
  policy_epoch: z.number(),
  index: z.record(z.string(), z.number()),
  devices: z.array(
    z.object({
      device_id: z.string(),
      acknowledged: z.number(),
      last_seen: z.number(),
    }),
  ),
  enabled: z.boolean().optional(),
});
export const answerSchema = z.object({
  claims: z.array(
    z.object({
      text: z.string(),
      tentative: z.boolean(),
      sources: z.array(z.string()),
    }),
  ),
  limitations: z.string(),
  degraded: z.boolean(),
  sources: z.array(
    citationSchema.extend({
      key: z.string(),
      title: z.string(),
      text: z.string(),
      confirmed: z.boolean(),
      kind: z.string(),
    }),
  ),
});
export const conflictsSchema = z.object({
  items: z.array(
    z.object({
      conflict_id: z.string(),
      record_id: z.string(),
      current_revision: z.number(),
      proposed: z
        .object({
          record: recordSchema.omit({
            revision: true,
            deleted: true,
            updated_at: true,
          }),
          action: z.string(),
        })
        .passthrough(),
    }),
  ),
});
export type KnowledgeAnswer = z.infer<typeof answerSchema>;
const receiptSchema = z.object({
  receipts: z.array(
    z.object({ status: z.string(), operation_id: z.string() }).passthrough(),
  ),
});

export class KnowledgeClient {
  constructor(
    readonly session: string,
    readonly embedded: boolean,
    readonly signal: AbortSignal,
  ) {}
  async request<T>(
    path: string,
    parser: RuntimeResponseParser<T>,
    body?: unknown,
    method?: string,
  ): Promise<T> {
    const route = `/v1/knowledge${path}${path.includes("?") ? "&" : "?"}session_id=${encodeURIComponent(this.session)}`;
    const init = {
      method: method ?? (body ? "POST" : "GET"),
      body: body ? JSON.stringify(body) : undefined,
      signal: this.signal,
    };
    if (!this.embedded) {
      try {
        return await requestRuntime(route, parser, {
          ...init,
          timeoutMs: 150_000,
        });
      } catch (error) {
        if (error instanceof RuntimeRequestError && error.status === 404)
          throw new Error(
            "当前服务器尚未启用知识图谱，请更新服务器或开启知识库服务。",
            { cause: error },
          );
        if (error instanceof RuntimeRequestError && error.status === 409)
          throw new Error("资料正在同步或已在另一端更新，请刷新后重试。", {
            cause: error,
          });
        throw error;
      }
    }
    // The native client provisions an HttpOnly cookie, scoped to this API. No host token enters JS.
    const response = await fetch(route, {
      ...init,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.any([this.signal, AbortSignal.timeout(150_000)]),
    });
    if (!response.ok)
      throw new Error(
        response.status === 401
          ? "连接已过期，请返回 App 后重新打开知识地图。"
          : response.status === 409
            ? "资料已在另一端更新，请刷新后核对。"
            : `请求未完成（${response.status}）`,
      );
    return parser.parse(await response.json());
  }
  async save(
    record: KnowledgeRecord,
    epoch: number,
    device: string,
    operation: string,
    action = "upsert",
  ) {
    const revision = record.revision;
    const draft = recordSchema
      .omit({ revision: true, deleted: true, updated_at: true, sync: true })
      .parse(record);
    return this.request("/commands", receiptSchema, {
      session_id: this.session,
      device_id: device,
      policy_epoch: epoch,
      mutations: [
        {
          operation_id: operation,
          expected_revision: revision,
          action,
          record: draft,
        },
      ],
    });
  }
}

export const kindLabels: Record<string, string> = {
  person: "人物",
  project: "项目",
  organization: "组织",
  todo: "事项",
  event: "日程",
  memory: "记忆",
  source: "资料",
  reference: "参考",
};
export const predicateLabels: Record<string, string> = {
  participates: "参与",
  responsible: "负责",
  belongs_to: "属于",
  depends_on: "依赖",
  waiting_for: "等待",
  occurs_at: "发生于",
  derived_from: "来源于",
  related_to: "关联",
};
export const stamp = (value: number | null) =>
  value === null ? "未设置" : new Date(value).toLocaleString("zh-CN");
