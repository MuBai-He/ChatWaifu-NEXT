import { z } from "zod";

import { resolveRuntimeUrl } from "../runtimeEndpoint";

export type RuntimeResponseParser<Result> = {
  parse(input: unknown): Result;
};

export function runtimeParser<Result>(
  parse: (input: unknown) => Result,
): RuntimeResponseParser<Result> {
  return { parse };
}

const runtimeErrorSchema = z
  .object({ detail: z.string().optional() })
  .passthrough();

export const mutationReceiptSchema = z.object({}).passthrough();

export interface RuntimeRequestInit extends RequestInit {
  timeoutMs?: number;
}

export async function requestRuntime<Result>(
  path: string,
  parser: RuntimeResponseParser<Result>,
  init?: RuntimeRequestInit,
): Promise<Result> {
  const {
    timeoutMs = 8_000,
    signal: callerSignal,
    ...requestInit
  } = init ?? {};
  const runtimeUrl = await waitForRuntimeUrl(callerSignal);
  const controller = new AbortController();
  let timedOut = false;
  const aborted = new Promise<never>((_resolve, reject) => {
    controller.signal.addEventListener(
      "abort",
      () => reject(abortReason(controller.signal)),
      { once: true },
    );
  });
  const abortFromCaller = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) abortFromCaller();
  else callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort(
      new DOMException("Runtime request timed out", "TimeoutError"),
    );
  }, timeoutMs);
  try {
    return await Promise.race([
      performRuntimeRequest(
        runtimeUrl,
        path,
        parser,
        requestInit,
        controller.signal,
      ),
      aborted,
    ]);
  } catch (error: unknown) {
    if (timedOut)
      throw new Error(`Runtime 请求超时：${path}`, { cause: error });
    throw error;
  } finally {
    window.clearTimeout(timer);
    callerSignal?.removeEventListener("abort", abortFromCaller);
  }
}

async function waitForRuntimeUrl(
  signal: AbortSignal | null | undefined,
): Promise<string> {
  if (signal?.aborted) throw abortReason(signal);
  const resolution = resolveRuntimeUrl();
  if (!signal) return resolution;

  let abortListener: (() => void) | undefined;
  const cancelled = new Promise<never>((_resolve, reject) => {
    abortListener = () => reject(abortReason(signal));
    signal.addEventListener("abort", abortListener, { once: true });
  });
  try {
    return await Promise.race([resolution, cancelled]);
  } finally {
    if (abortListener) signal.removeEventListener("abort", abortListener);
  }
}

async function performRuntimeRequest<Result>(
  runtimeUrl: string,
  path: string,
  parser: RuntimeResponseParser<Result>,
  init: RequestInit,
  signal: AbortSignal,
): Promise<Result> {
  if (signal.aborted) throw abortReason(signal);
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  const response = await fetch(`${runtimeUrl}${path}`, {
    ...init,
    signal,
    headers,
  });
  const payload = await readJson(response);
  if (!response.ok) {
    const detail = runtimeErrorSchema.safeParse(payload);
    throw new Error(
      detail.success && detail.data.detail
        ? detail.data.detail
        : `Runtime request failed (${response.status})`,
    );
  }
  try {
    return parser.parse(payload);
  } catch (error: unknown) {
    throw new Error(`Runtime 返回了无效响应：${path}`, { cause: error });
  }
}

function abortReason(signal: AbortSignal): Error {
  const reason: unknown = signal.reason;
  if (reason instanceof DOMException || reason instanceof Error) return reason;
  if (reason && typeof reason === "object") {
    const candidate = reason as { message?: unknown; name?: unknown };
    if (
      typeof candidate.message === "string" &&
      typeof candidate.name === "string"
    )
      return new DOMException(candidate.message, candidate.name);
  }
  return new DOMException("Runtime request aborted", "AbortError");
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text) as unknown;
  } catch (error: unknown) {
    throw new Error(`Runtime 返回了非 JSON 响应 (${response.status})`, {
      cause: error,
    });
  }
}
