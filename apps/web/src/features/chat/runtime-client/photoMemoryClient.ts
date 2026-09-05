import {
  parsePhotoMemoryDeleteResult,
  parsePhotoMemorySettings,
  parsePhotoMemorySnapshot,
  type SavedPhoto,
  type PhotoMemoryDeleteResult,
  type PhotoMemorySettings,
  type PhotoMemorySettingsUpdate,
  type PhotoMemorySnapshot,
} from "@chatwaifu/protocol";

import { requestRuntime, runtimeParser } from "./http";
import { resolveRuntimeConnection } from "../runtimeEndpoint";

export type {
  SavedPhoto,
  PhotoMemoryDeleteResult,
  PhotoMemorySettings,
  PhotoMemorySettingsUpdate,
  PhotoMemorySnapshot,
};

const photoMemorySnapshotParser = runtimeParser(parsePhotoMemorySnapshot);
const photoMemorySettingsParser = runtimeParser(parsePhotoMemorySettings);
const photoMemoryDeleteResultParser = runtimeParser(
  parsePhotoMemoryDeleteResult,
);

export async function getPhotoMemory(
  characterId = "default",
  signal?: AbortSignal,
): Promise<PhotoMemorySnapshot> {
  const query = new URLSearchParams({ character_id: characterId });
  return requestRuntime(
    `/v1/photo-memory?${query.toString()}`,
    photoMemorySnapshotParser,
    { signal },
  );
}

export async function updatePhotoMemorySettings(
  update: PhotoMemorySettingsUpdate,
  characterId = "default",
  signal?: AbortSignal,
): Promise<PhotoMemorySettings> {
  const query = new URLSearchParams({ character_id: characterId });
  return requestRuntime(
    `/v1/photo-memory/settings?${query.toString()}`,
    photoMemorySettingsParser,
    {
      method: "PUT",
      body: JSON.stringify(update),
      signal,
    },
  );
}

export async function deleteSavedPhoto(
  photoId: string,
  characterId = "default",
  signal?: AbortSignal,
): Promise<PhotoMemoryDeleteResult> {
  const query = new URLSearchParams({ character_id: characterId });
  return requestRuntime(
    `/v1/photo-memory/${encodeURIComponent(photoId)}?${query.toString()}`,
    photoMemoryDeleteResultParser,
    {
      method: "DELETE",
      signal,
    },
  );
}

export interface FetchPhotoImageOptions {
  characterId?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export const MAX_PHOTO_IMAGE_BYTE_SIZE = 5 * 1024 * 1024; // 5 MiB

/**
 * Fetches the binary image for a saved photo using authenticated Bearer token (never query params)
 * and returns an object URL. The caller is responsible for revoking the returned URL.
 */
export async function fetchPhotoImageUrl(
  photoId: string,
  options: FetchPhotoImageOptions = {},
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
      new DOMException("Photo image request timed out", "TimeoutError"),
    );
  }, timeoutMs);

  const query = new URLSearchParams({ character_id: characterId });
  const url = `${connection.baseUrl}/v1/photo-memory/${encodeURIComponent(photoId)}/image?${query.toString()}`;

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
      throw new Error(`获取照片失败 (${response.status})`);
    }

    const contentType = response.headers
      .get("Content-Type")
      ?.split(";")[0]
      ?.trim();
    if (contentType !== "image/png" && contentType !== "image/jpeg") {
      throw new Error(
        `照片格式错误 (${contentType || "缺失"})，仅支持 PNG/JPEG`,
      );
    }

    const contentLengthHeader = response.headers.get("Content-Length");
    if (contentLengthHeader) {
      const parsedLength = parseInt(contentLengthHeader, 10);
      if (
        Number.isFinite(parsedLength) &&
        parsedLength > MAX_PHOTO_IMAGE_BYTE_SIZE
      ) {
        throw new Error("照片体积超出上限（最大支持 5MB）");
      }
    }

    if (!response.body) {
      throw new Error("响应体为空");
    }

    const reader = response.body.getReader();
    const chunks: ArrayBuffer[] = [];
    let bytesReceived = 0;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        if (value) {
          bytesReceived += value.length;
          if (bytesReceived > MAX_PHOTO_IMAGE_BYTE_SIZE) {
            throw new Error("照片体积超出上限（最大支持 5MB）");
          }
          chunks.push(new Uint8Array(value).buffer);
        }
      }
    } catch (e) {
      await reader.cancel(e).catch(() => undefined);
      throw e;
    } finally {
      reader.releaseLock();
    }

    if (callerSignal?.aborted) {
      throw callerSignal.reason ?? new DOMException("Aborted", "AbortError");
    }
    if (controller.signal.aborted) {
      throw (
        controller.signal.reason ?? new DOMException("Aborted", "AbortError")
      );
    }

    if (bytesReceived === 0) {
      throw new Error("照片为空");
    }

    const blob = new Blob(chunks, { type: contentType });

    return URL.createObjectURL(blob);
  } catch (error: unknown) {
    if (timedOut) {
      throw new Error("照片请求超时", { cause: error });
    }
    throw error;
  } finally {
    controller.abort();
    window.clearTimeout(timer);
    if (callerSignal) {
      callerSignal.removeEventListener("abort", onCallerAbort);
    }
  }
}
