import {
  parseStickerLibraryDeleteResult,
  parseStickerLibrarySettings,
  parseStickerLibrarySnapshot,
  type LearnedSticker,
  type StickerLibraryDeleteResult,
  type StickerLibrarySettings,
  type StickerLibrarySettingsUpdate,
  type StickerLibrarySnapshot,
} from "@chatwaifu/protocol";

import { requestRuntime, runtimeParser } from "./http";
import { resolveRuntimeConnection } from "../runtimeEndpoint";

export type {
  LearnedSticker,
  StickerLibraryDeleteResult,
  StickerLibrarySettings,
  StickerLibrarySettingsUpdate,
  StickerLibrarySnapshot,
};

const stickerLibrarySnapshotParser = runtimeParser(parseStickerLibrarySnapshot);
const stickerLibrarySettingsParser = runtimeParser(parseStickerLibrarySettings);
const stickerLibraryDeleteResultParser = runtimeParser(
  parseStickerLibraryDeleteResult,
);

export async function getStickerLibrary(
  characterId = "default",
  signal?: AbortSignal,
): Promise<StickerLibrarySnapshot> {
  const query = new URLSearchParams({ character_id: characterId });
  return requestRuntime(
    `/v1/sticker-library?${query.toString()}`,
    stickerLibrarySnapshotParser,
    { signal },
  );
}

export async function updateStickerLibrarySettings(
  update: StickerLibrarySettingsUpdate,
  characterId = "default",
  signal?: AbortSignal,
): Promise<StickerLibrarySettings> {
  const query = new URLSearchParams({ character_id: characterId });
  return requestRuntime(
    `/v1/sticker-library/settings?${query.toString()}`,
    stickerLibrarySettingsParser,
    {
      method: "PUT",
      body: JSON.stringify(update),
      signal,
    },
  );
}

export async function deleteLearnedSticker(
  stickerId: string,
  characterId = "default",
  signal?: AbortSignal,
): Promise<StickerLibraryDeleteResult> {
  const query = new URLSearchParams({ character_id: characterId });
  return requestRuntime(
    `/v1/sticker-library/${encodeURIComponent(stickerId)}?${query.toString()}`,
    stickerLibraryDeleteResultParser,
    {
      method: "DELETE",
      signal,
    },
  );
}

export interface FetchStickerImageOptions {
  characterId?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export const MAX_STICKER_IMAGE_BYTE_SIZE = 5 * 1024 * 1024; // 5 MiB

/**
 * Fetches the binary PNG for a learned sticker using authenticated Bearer token (never query params)
 * and returns an object URL. The caller is responsible for revoking the returned URL.
 */
export async function fetchStickerImageUrl(
  stickerId: string,
  options: FetchStickerImageOptions = {},
): Promise<string> {
  const {
    characterId = "default",
    signal: callerSignal,
    timeoutMs = 8_000,
  } = options;

  if (callerSignal?.aborted) {
    throw callerSignal.reason ?? new DOMException("Aborted", "AbortError");
  }

  const connection = await resolveRuntimeConnection();

  // callerSignal can abort during awaited resolveRuntimeConnection; recheck immediately
  if (callerSignal?.aborted) {
    throw callerSignal.reason ?? new DOMException("Aborted", "AbortError");
  }

  const controller = new AbortController();
  let timedOut = false;

  const onCallerAbort = () => controller.abort(callerSignal?.reason);
  if (callerSignal) {
    callerSignal.addEventListener("abort", onCallerAbort, { once: true });
  }

  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort(
      new DOMException("Sticker image request timed out", "TimeoutError"),
    );
  }, timeoutMs);

  const query = new URLSearchParams({ character_id: characterId });
  const url = `${connection.baseUrl}/v1/sticker-library/${encodeURIComponent(stickerId)}/image?${query.toString()}`;

  const headers: Record<string, string> = {};
  if (connection.token) {
    headers["Authorization"] = `Bearer ${connection.token}`;
  }

  try {
    const response = await fetch(url, {
      method: "GET",
      headers,
      cache: "no-store",
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`获取表情图片失败 (${response.status})`);
    }

    const contentType = response.headers
      .get("Content-Type")
      ?.split(";")[0]
      ?.trim();
    if (contentType && contentType !== "image/png") {
      throw new Error(`表情图片类型错误 (${contentType})，仅支持 PNG`);
    }

    const contentLengthHeader = response.headers.get("Content-Length");
    if (contentLengthHeader) {
      const parsedLength = parseInt(contentLengthHeader, 10);
      if (
        Number.isFinite(parsedLength) &&
        parsedLength > MAX_STICKER_IMAGE_BYTE_SIZE
      ) {
        throw new Error("表情图片体积超出上限（最大支持 5MB）");
      }
    }

    const blob = await response.blob();

    // Post-body abort check
    if (callerSignal?.aborted) {
      throw callerSignal.reason ?? new DOMException("Aborted", "AbortError");
    }
    if (controller.signal.aborted) {
      throw (
        controller.signal.reason ?? new DOMException("Aborted", "AbortError")
      );
    }

    if (blob.type && blob.type !== "image/png") {
      throw new Error(`表情图片格式不匹配 (${blob.type})`);
    }
    if (blob.size > MAX_STICKER_IMAGE_BYTE_SIZE) {
      throw new Error("表情图片体积超出上限（最大支持 5MB）");
    }

    return URL.createObjectURL(blob);
  } catch (error: unknown) {
    if (timedOut) {
      throw new Error("表情图片请求超时", { cause: error });
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
    if (callerSignal) {
      callerSignal.removeEventListener("abort", onCallerAbort);
    }
  }
}
