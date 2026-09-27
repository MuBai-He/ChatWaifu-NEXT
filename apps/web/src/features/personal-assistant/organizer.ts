import { invoke } from "@tauri-apps/api/core";
import {
  resolveRuntimeConnection,
  runtimeFetchWithConnection,
  type RuntimeConnection,
} from "../chat/runtimeEndpoint";

export interface AppleSource {
  id: string;
  title: string;
  resource: "calendar" | "reminder";
  writable: boolean;
}
export interface DeviceBinding {
  device_id: string;
  secret: string;
  sources: AppleSource[];
  source_revision: number;
  results: Record<string, Record<string, unknown>>;
}
export interface AssistantDevice {
  device_id: string;
  name: string;
  last_seen: number;
  sources: AppleSource[];
}
export interface AssistantTask {
  request_id: string;
  device_id: string;
  title: string;
  kind: "reminder" | "alarm";
  due_at: string;
  next_due: number;
  revision: number;
  timezone: string;
  repeat: "none" | "daily" | "weekdays";
  state: string;
}
export interface AppleItem {
  id: string;
  title: string;
  modified: number;
  calendar_id: string;
  completed?: boolean;
  start?: number;
  end?: number;
  due?: number;
}
export interface AppleResult {
  error?: string;
  items?: AppleItem[];
  item?: AppleItem;
  deleted?: boolean;
  truncated?: boolean;
}
export interface Operation {
  operation_id: string;
  state: string;
  result: AppleResult;
}
export interface Organizer {
  devices: AssistantDevice[];
  tasks: AssistantTask[];
  operations: Operation[];
  scheduler_error: string | null;
  history?: {
    delivery_id: string;
    task_id: string;
    due: number;
    state: string;
  }[];
}
export interface Delivery extends AssistantTask {
  delivery_id: string;
  expires: number;
}

export async function organizerRequest<T>(
  path: string,
  body?: unknown,
  connection?: RuntimeConnection,
  signal?: AbortSignal,
): Promise<T> {
  const c = connection ?? (await resolveRuntimeConnection());
  const response = await runtimeFetchWithConnection(
    c,
    `/v1/personal-assistant${path}`,
    {
      method: body === undefined ? "GET" : "POST",
      headers:
        body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(15000)])
        : AbortSignal.timeout(15000),
    },
  );
  if (!response.ok) {
    const value = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new Error(
      typeof value?.detail === "string"
        ? value.detail
        : `请求失败 (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}
export async function deviceCall<T>(
  server: string,
  action: string,
  payload: unknown = null,
): Promise<T> {
  return invoke<T>("assistant_device", { call: { server, action, payload } });
}
export function errorText(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  const messages: Record<string, string> = {
    apple_permission_required:
      "尚未获得系统权限，请点击授权；已拒绝时需在系统设置中允许。",
    apple_not_supported:
      "Apple 日历和提醒事项需要已配对的 Mac；本机仍可接收定时提醒。",
    personal_assistant_disabled:
      "服务器尚未启用个人助理，请开启 personal_assistant.enabled。",
    device_requires_https_or_localhost:
      "设备配对需要 HTTPS 服务器地址或本机回环地址。",
    apple_saved_readback_unavailable:
      "Apple 已接受保存但回读未确认，请在 Apple 应用核对，不要重复创建。",
    apple_write_outcome_uncertain:
      "写入结果不确定，请先在 Apple 应用中核对，不要重复创建。",
    item_changed_refresh_before_editing:
      "事项已被其他设备修改，请重新读取后再编辑。",
    device_not_authorized: "此设备配对已撤销，请重新配对。",
  };
  return messages[raw] ?? raw;
}
