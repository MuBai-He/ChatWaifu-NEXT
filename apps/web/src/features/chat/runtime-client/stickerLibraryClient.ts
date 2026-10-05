import {
  parseStickerLibraryDeleteResult,
  parseStickerLibrarySettings,
  parseStickerLibrarySnapshot,
  parseStickerUsageHistory,
  type StickerUsageHistory,
  type StickerUsageRecord,
  type LearnedSticker,
  type StickerLibraryDeleteResult,
  type StickerLibrarySettings,
  type StickerLibrarySettingsUpdate,
  type StickerLibrarySnapshot,
} from "@chatwaifu/protocol";

import { requestRuntime, runtimeParser } from "./http";
import {
  assertRuntimeRequestContext,
  readRuntimeRequestContext,
} from "../runtimeEndpoint";

export type {
  StickerUsageHistory,
  StickerUsageRecord,
  LearnedSticker,
  StickerLibraryDeleteResult,
  StickerLibrarySettings,
  StickerLibrarySettingsUpdate,
  StickerLibrarySnapshot,
};

export type StickerGroupScope = { routeId: string; sceneId: string };

function libraryQuery(characterId: string, groupScope?: StickerGroupScope) {
  const query = new URLSearchParams({ character_id: characterId });
  if (groupScope) {
    query.set("group_route_id", groupScope.routeId);
    query.set("group_scene_id", groupScope.sceneId);
  }
  return query;
}

const stickerLibrarySnapshotParser = runtimeParser(parseStickerLibrarySnapshot);
const stickerLibrarySettingsParser = runtimeParser(parseStickerLibrarySettings);
const stickerLibraryDeleteResultParser = runtimeParser(
  parseStickerLibraryDeleteResult,
);

export async function getStickerLibrary(
  characterId = "default",
  signal?: AbortSignal,
  groupScope?: StickerGroupScope,
): Promise<StickerLibrarySnapshot> {
  const query = libraryQuery(characterId, groupScope);
  const expectedContext = await readRuntimeRequestContext();
  return requestRuntime(
    `/v1/sticker-library?${query.toString()}`,
    stickerLibrarySnapshotParser,
    { signal, expectedContext },
  );
}

export async function updateStickerLibrarySettings(
  update: StickerLibrarySettingsUpdate,
  characterId = "default",
  signal?: AbortSignal,
  groupScope?: StickerGroupScope,
): Promise<StickerLibrarySettings> {
  const query = libraryQuery(characterId, groupScope);
  const expectedContext = await readRuntimeRequestContext();
  return requestRuntime(
    `/v1/sticker-library/settings?${query.toString()}`,
    stickerLibrarySettingsParser,
    {
      method: "PUT",
      body: JSON.stringify(update),
      signal,
      expectedContext,
    },
  );
}

export async function deleteLearnedSticker(
  stickerId: string,
  characterId = "default",
  signal?: AbortSignal,
  groupScope?: StickerGroupScope,
): Promise<StickerLibraryDeleteResult> {
  const query = libraryQuery(characterId, groupScope);
  const expectedContext = await readRuntimeRequestContext();
  return requestRuntime(
    `/v1/sticker-library/${encodeURIComponent(stickerId)}?${query.toString()}`,
    stickerLibraryDeleteResultParser,
    {
      method: "DELETE",
      signal,
      expectedContext,
    },
  );
}

export interface FetchStickerImageOptions {
  characterId?: string;
  groupScope?: StickerGroupScope;
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
    groupScope,
    signal: callerSignal,
    timeoutMs = 8_000,
  } = options;

  if (callerSignal?.aborted) {
    throw callerSignal.reason ?? new DOMException("Aborted", "AbortError");
  }

  const expectedContext = await readRuntimeRequestContext();
  const connection = expectedContext.connection;

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

  const query = libraryQuery(characterId, groupScope);
  const url = `${connection.baseUrl}/v1/sticker-library/${encodeURIComponent(stickerId)}/image?${query.toString()}`;

  const headers: Record<string, string> = {};
  if (connection.token) {
    headers["Authorization"] = `Bearer ${connection.token}`;
  }

  try {
    assertRuntimeRequestContext(expectedContext);
    const response = await fetch(url, {
      method: "GET",
      headers,
      cache: "no-store",
      signal: controller.signal,
    });

    assertRuntimeRequestContext(expectedContext);
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

    assertRuntimeRequestContext(expectedContext);
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

export async function getStickerUsage(
  characterId = "default",
  signal?: AbortSignal,
  groupScope?: StickerGroupScope,
): Promise<StickerUsageHistory> {
  const query = libraryQuery(characterId, groupScope);
  const expectedContext = await readRuntimeRequestContext();
  return requestRuntime(
    `/v1/sticker-library/usage?${query.toString()}`,
    runtimeParser(parseStickerUsageHistory),
    { signal, expectedContext },
  );
}
