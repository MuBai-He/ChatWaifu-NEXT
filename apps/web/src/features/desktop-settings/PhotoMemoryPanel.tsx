import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  deleteSavedPhoto,
  fetchPhotoImageUrl,
  getPhotoMemory,
  updatePhotoMemorySettings,
  type SavedPhoto,
  type PhotoMemorySnapshot,
} from "../chat/runtimeClient";
import { SettingsToggle } from "./SettingsPrimitives";

interface PhotoMemoryPanelProps {
  characterId: string;
  runtimeOnline: boolean;
}

type QueueItem = {
  task: () => Promise<void>;
  signal?: AbortSignal;
};

class AsyncConcurrencyPool {
  private active = 0;
  private queue: QueueItem[] = [];

  constructor(private readonly maxConcurrency: number = 6) {}

  enqueue(task: () => Promise<void>, signal?: AbortSignal): Promise<void> {
    if (signal?.aborted) {
      return Promise.reject(asError(signal.reason));
    }

    return new Promise<void>((resolve, reject) => {
      let executed = false;
      const wrappedTask = async () => {
        if (signal?.aborted) {
          reject(asError(signal.reason));
          return;
        }
        executed = true;
        try {
          await task();
          resolve();
        } catch (err: unknown) {
          reject(asError(err));
        } finally {
          signal?.removeEventListener("abort", onAbort);
        }
      };

      const item: QueueItem = { task: wrappedTask, signal };
      const onAbort = () => {
        const index = this.queue.indexOf(item);
        if (index !== -1) {
          this.queue.splice(index, 1);
        }
        if (!executed) {
          reject(asError(signal?.reason));
        }
      };

      if (signal) {
        signal.addEventListener("abort", onAbort, { once: true });
      }

      this.queue.push(item);
      this.pump();
    });
  }

  private pump() {
    while (this.active < this.maxConcurrency && this.queue.length > 0) {
      const item = this.queue.shift();
      if (!item) break;
      if (item.signal?.aborted) continue;

      this.active++;
      void item.task().finally(() => {
        this.active--;
        this.pump();
      });
    }
  }
}

export function PhotoMemoryPanel(props: PhotoMemoryPanelProps) {
  return <ScopedPhotoMemoryPanel key={props.characterId} {...props} />;
}

function ScopedPhotoMemoryPanel({
  characterId,
  runtimeOnline,
}: PhotoMemoryPanelProps) {
  const [snapshot, setSnapshot] = useState<PhotoMemorySnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const previewDownloadPool = useMemo(() => new AsyncConcurrencyPool(6), []);

  const lifecycle = useRef({
    mounted: false,
    request: 0,
    mutating: false,
    controller: null as AbortController | null,
  });

  useEffect(() => {
    const life = lifecycle.current;
    life.mounted = true;
    const request = ++life.request;
    const controller = new AbortController();
    life.controller = controller;
    void getPhotoMemory(characterId, controller.signal)
      .then((data) => {
        if (life.mounted && request === life.request) setSnapshot(data);
      })
      .catch((err: unknown) => {
        if (life.mounted && request === life.request && !isAbortError(err))
          setError(getErrorMessage(err, "读取照片记忆失败"));
      })
      .finally(() => {
        if (life.mounted && request === life.request) setLoading(false);
      });
    return () => {
      life.mounted = false;
      ++life.request;
      life.controller?.abort();
    };
  }, [characterId, runtimeOnline]);

  const loadMemory = useCallback(
    async (retainedError: string | null = null) => {
      const life = lifecycle.current;
      if (!life.mounted) return;
      life.controller?.abort();
      const controller = new AbortController();
      life.controller = controller;
      const request = ++life.request;
      setLoading(true);
      setError(retainedError);
      try {
        const data = await getPhotoMemory(characterId, controller.signal);
        if (life.mounted && request === life.request) setSnapshot(data);
      } catch (err: unknown) {
        if (life.mounted && request === life.request && !isAbortError(err))
          setError(getErrorMessage(err, "读取照片记忆失败"));
      } finally {
        if (life.mounted && request === life.request) setLoading(false);
      }
    },
    [characterId],
  );

  const handleManualRefresh = () => {
    if (!lifecycle.current.mutating && !loading && runtimeOnline)
      void loadMemory();
  };

  const handleToggleRetention = async (enabled: boolean) => {
    const life = lifecycle.current;
    if (!snapshot || life.mutating || !runtimeOnline) return;
    life.mutating = true;
    ++life.request;
    life.controller?.abort();
    const controller = new AbortController();
    life.controller = controller;
    setSavingSettings(true);
    setError(null);
    try {
      const updated = await updatePhotoMemorySettings(
        {
          schema_version: "1.0",
          retention_enabled: enabled,
          expected_revision: snapshot.settings.revision ?? 0,
        },
        characterId,
        controller.signal,
      );
      if (life.mounted) {
        setSnapshot((prev) => (prev ? { ...prev, settings: updated } : prev));
        await loadMemory();
      }
    } catch (err: unknown) {
      if (life.mounted && !isAbortError(err)) {
        const message = getErrorMessage(err, "保存照片记忆设置失败");
        setError(message);
        if (isConflictError(err)) await loadMemory(message);
      }
    } finally {
      life.mutating = false;
      if (life.mounted) {
        setSavingSettings(false);
        setLoading(false);
      }
    }
  };

  const handleDelete = async (photoId: string) => {
    const life = lifecycle.current;
    if (life.mutating || !runtimeOnline) return;
    life.mutating = true;
    ++life.request;
    life.controller?.abort();
    const controller = new AbortController();
    life.controller = controller;
    setDeletingId(photoId);
    setError(null);
    try {
      await deleteSavedPhoto(photoId, characterId, controller.signal);
      if (life.mounted) {
        setSnapshot((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            items: (prev.items ?? []).filter((p) => p.photo_id !== photoId),
            total_bytes: (prev.items ?? [])
              .filter((p) => p.photo_id !== photoId)
              .reduce((acc, p) => acc + p.byte_size, 0),
          };
        });
        setDeletingId(null);
        await loadMemory();
      }
    } catch (err: unknown) {
      if (life.mounted && !isAbortError(err)) {
        setError(getErrorMessage(err, "删除照片失败"));
        setDeletingId(null);
        setLoading(false);
      }
    } finally {
      life.mutating = false;
      if (life.mounted) {
        setDeletingId(null);
        setLoading(false);
      }
    }
  };

  const retentionEnabled = snapshot?.settings.retention_enabled ?? false;
  const items = snapshot?.items ?? [];
  const count = items.length;
  const capacity = snapshot?.capacity ?? 200;
  const totalBytes = snapshot?.total_bytes ?? 0;
  const isBusy = savingSettings || deletingId !== null;

  return (
    <div className="photo-memory-panel" data-testid="photo-memory-panel">
      <div className="photo-memory-toggle-section">
        <SettingsToggle
          label="记住我发的照片"
          description="开启后保存照片副本和内容描述，便于以后回忆。关闭后停止保存新照片，已有照片可单独删除。"
          checked={retentionEnabled}
          disabled={isBusy || !runtimeOnline || !snapshot}
          onChange={(enabled) => void handleToggleRetention(enabled)}
        />
      </div>

      <div className="photo-memory-header">
        <div className="photo-memory-count">
          <h4>已保存的照片</h4>
          <span>
            {count} / {capacity} ({(totalBytes / 1024 / 1024).toFixed(1)} / 500
            MiB)
          </span>
        </div>
        <button
          type="button"
          className="photo-memory-refresh-button"
          disabled={isBusy || !runtimeOnline}
          onClick={handleManualRefresh}
          aria-label="刷新照片记忆"
        >
          {loading ? "正在刷新…" : "刷新"}
        </button>
      </div>

      {error ? (
        <div className="photo-memory-error" role="alert">
          {error}
        </div>
      ) : null}

      {loading && !snapshot ? (
        <div className="photo-memory-loading" role="status">
          正在加载照片记忆…
        </div>
      ) : items.length === 0 ? (
        <div className="photo-memory-empty">
          <p>暂无已保存的照片</p>
        </div>
      ) : (
        <div className="photo-memory-grid" role="list">
          {items.map((photo) => (
            <PhotoTile
              key={photo.photo_id}
              photo={photo}
              characterId={characterId}
              isDeleting={deletingId === photo.photo_id}
              disabled={!runtimeOnline || isBusy}
              onDelete={() => void handleDelete(photo.photo_id)}
              previewDownloadPool={previewDownloadPool}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface PhotoTileProps {
  photo: SavedPhoto;
  characterId: string;
  isDeleting: boolean;
  disabled: boolean;
  onDelete: () => void;
  previewDownloadPool: AsyncConcurrencyPool;
}

function PhotoTile({
  photo,
  characterId,
  isDeleting,
  disabled,
  onDelete,
  previewDownloadPool,
}: PhotoTileProps) {
  const [imageState, setImageState] = useState<{
    photoId: string;
    url: string | null;
    loading: boolean;
    error: boolean;
  }>({
    photoId: photo.photo_id,
    url: null,
    loading: true,
    error: false,
  });
  const currentUrlRef = useRef<string | null>(null);
  const [isVisible, setIsVisible] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const currentLoading =
    imageState.photoId === photo.photo_id ? imageState.loading : true;
  const currentError =
    imageState.photoId === photo.photo_id ? imageState.error : false;
  const currentUrl =
    imageState.photoId === photo.photo_id ? imageState.url : null;

  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setIsVisible(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setIsVisible(true);
            observer.disconnect();
            break;
          }
        }
      },
      { rootMargin: "100px" },
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!isVisible) return;

    const controller = new AbortController();

    void previewDownloadPool
      .enqueue(async () => {
        const url = await fetchPhotoImageUrl(photo.photo_id, {
          characterId,
          signal: controller.signal,
          timeoutMs: 8_000,
        });

        if (!controller.signal.aborted) {
          if (currentUrlRef.current) {
            URL.revokeObjectURL(currentUrlRef.current);
          }
          currentUrlRef.current = url;
          setImageState({
            photoId: photo.photo_id,
            url,
            loading: false,
            error: false,
          });
        } else {
          URL.revokeObjectURL(url);
        }
      }, controller.signal)
      .catch((err: unknown) => {
        if (!controller.signal.aborted && !isAbortError(err)) {
          setImageState({
            photoId: photo.photo_id,
            url: null,
            loading: false,
            error: true,
          });
        }
      });

    return () => {
      controller.abort();
      if (currentUrlRef.current) {
        URL.revokeObjectURL(currentUrlRef.current);
        currentUrlRef.current = null;
      }
    };
  }, [photo.photo_id, characterId, isVisible, previewDownloadPool]);

  return (
    <>
      <div
        ref={containerRef}
        className="photo-memory-tile"
        role="listitem"
        data-testid={`photo-tile-${photo.photo_id}`}
      >
        <div className="photo-memory-thumbnail-wrap">
          {currentLoading ? (
            <div className="photo-memory-thumbnail-placeholder" role="status">
              加载中…
            </div>
          ) : currentError || !currentUrl ? (
            <div className="photo-memory-thumbnail-placeholder error">
              图片加载失败
            </div>
          ) : (
            <img
              src={currentUrl}
              alt={photo.title}
              className="photo-memory-thumbnail"
              onClick={() => setDialogOpen(true)}
            />
          )}
        </div>

        <div className="photo-memory-tile-info">
          <strong className="photo-memory-tile-title" title={photo.title}>
            {photo.title}
          </strong>
          <span className="photo-memory-tile-desc" title={photo.description}>
            {photo.description}
          </span>
          {photo.caption && (
            <span className="photo-memory-tile-caption">"{photo.caption}"</span>
          )}
          <div className="photo-memory-tile-meta">
            <span className="photo-memory-tile-date">
              {formatDate(photo.received_at)}
            </span>
            <span
              className="photo-memory-source"
              title={`来自微信收到于 ${formatDate(photo.received_at)}`}
            >
              微信
            </span>
          </div>
        </div>

        <div className="photo-memory-actions">
          <button
            type="button"
            className="photo-memory-delete-button"
            disabled={disabled || isDeleting}
            onClick={() => {
              setDialogOpen(false);
              onDelete();
            }}
            aria-label={`删除照片 ${photo.title}`}
          >
            {isDeleting ? "删除中…" : "删除"}
          </button>
        </div>
      </div>
      {dialogOpen && currentUrl && (
        <div
          className="photo-memory-dialog-overlay"
          onClick={() => setDialogOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={`照片：${photo.title}`}
            className="photo-memory-dialog-content"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              aria-label="关闭照片预览"
              className="photo-memory-dialog-close"
              onClick={() => setDialogOpen(false)}
            >
              ×
            </button>
            <img
              src={currentUrl}
              alt={photo.title}
              className="photo-memory-dialog-image"
            />
          </div>
        </div>
      )}
    </>
  );
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function isConflictError(error: unknown): boolean {
  if (error instanceof Error) {
    return (
      error.message.includes("409") ||
      error.message.toLowerCase().includes("conflict")
    );
  }
  return false;
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof TypeError) return fallback;
  return error instanceof Error ? error.message : fallback;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function asError(reason: unknown): Error {
  return reason instanceof Error
    ? reason
    : new DOMException("Aborted", "AbortError");
}
