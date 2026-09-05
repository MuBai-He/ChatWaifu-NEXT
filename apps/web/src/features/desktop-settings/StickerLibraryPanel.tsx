import { useCallback, useEffect, useRef, useState } from "react";

import {
  deleteLearnedSticker,
  fetchStickerImageUrl,
  getStickerLibrary,
  updateStickerLibrarySettings,
  type LearnedSticker,
  type StickerLibrarySnapshot,
} from "../chat/runtimeClient";
import { SettingsToggle } from "./SettingsPrimitives";

const EXPRESSION_LABELS: Record<LearnedSticker["expression"], string> = {
  neutral: "平静",
  happy: "开心",
  sad: "难过",
  angry: "生气",
  surprised: "惊讶",
  shy: "害羞",
  curious: "好奇",
};

interface StickerLibraryPanelProps {
  characterId: string;
  runtimeOnline: boolean;
}

// Bounded concurrent download pool (e.g. at most 6 parallel image downloads)
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

const previewDownloadPool = new AsyncConcurrencyPool(6);

export function StickerLibraryPanel(props: StickerLibraryPanelProps) {
  return <ScopedStickerLibraryPanel key={props.characterId} {...props} />;
}

function ScopedStickerLibraryPanel({
  characterId,
  runtimeOnline,
}: StickerLibraryPanelProps) {
  const [snapshot, setSnapshot] = useState<StickerLibrarySnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingSettings, setSavingSettings] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
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
    void getStickerLibrary(characterId, controller.signal)
      .then((data) => {
        if (life.mounted && request === life.request) setSnapshot(data);
      })
      .catch((err: unknown) => {
        if (life.mounted && request === life.request && !isAbortError(err))
          setError(getErrorMessage(err, "读取表情库失败"));
      })
      .finally(() => {
        if (life.mounted && request === life.request) setLoading(false);
      });
    return () => {
      life.mounted = false;
      ++life.request;
      life.controller?.abort();
    };
  }, [characterId]);

  const loadLibrary = useCallback(
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
        const data = await getStickerLibrary(characterId, controller.signal);
        if (life.mounted && request === life.request) setSnapshot(data);
      } catch (err: unknown) {
        if (life.mounted && request === life.request && !isAbortError(err))
          setError(getErrorMessage(err, "读取表情库失败"));
      } finally {
        if (life.mounted && request === life.request) setLoading(false);
      }
    },
    [characterId],
  );

  const handleManualRefresh = () => {
    if (!lifecycle.current.mutating && !loading && runtimeOnline)
      void loadLibrary();
  };

  const handleToggleLearning = async (enabled: boolean) => {
    const life = lifecycle.current;
    if (!snapshot || life.mutating || !runtimeOnline) return;
    life.mutating = true;
    ++life.request;
    life.controller?.abort();
    setSavingSettings(true);
    setError(null);
    try {
      const updated = await updateStickerLibrarySettings(
        {
          schema_version: "1.0",
          learning_enabled: enabled,
          expected_revision: snapshot.settings.revision ?? 0,
        },
        characterId,
      );
      if (life.mounted) {
        setSnapshot((prev) => (prev ? { ...prev, settings: updated } : prev));
        await loadLibrary();
      }
    } catch (err: unknown) {
      if (life.mounted && !isAbortError(err)) {
        const message = getErrorMessage(err, "保存表情学习设置失败");
        setError(message);
        if (isConflictError(err)) await loadLibrary(message);
      }
    } finally {
      life.mutating = false;
      if (life.mounted) {
        setSavingSettings(false);
        setLoading(false);
      }
    }
  };

  const handleDelete = async (stickerId: string) => {
    const life = lifecycle.current;
    if (life.mutating || !runtimeOnline) return;
    life.mutating = true;
    ++life.request;
    life.controller?.abort();
    setDeletingId(stickerId);
    setError(null);
    try {
      await deleteLearnedSticker(stickerId, characterId);
      if (life.mounted) await loadLibrary();
    } catch (err: unknown) {
      if (life.mounted && !isAbortError(err))
        setError(getErrorMessage(err, "删除表情失败"));
    } finally {
      life.mutating = false;
      if (life.mounted) {
        setDeletingId(null);
        setLoading(false);
      }
    }
  };

  const learningEnabled = snapshot?.settings.learning_enabled ?? false;
  const items = snapshot?.items ?? [];
  const count = items.length;
  const capacity = snapshot?.capacity ?? 100;
  const isBusy = savingSettings || deletingId !== null;

  return (
    <div className="sticker-library-panel" data-testid="sticker-library-panel">
      <div className="sticker-library-toggle-section">
        <SettingsToggle
          label="学习我发来的表情"
          description="开启后自动筛选并保存适合作表情的图片，普通照片不进入表情库。"
          checked={learningEnabled}
          disabled={isBusy || !runtimeOnline || !snapshot}
          onChange={(enabled) => void handleToggleLearning(enabled)}
        />
        <div className="sticker-library-sub-notes">
          <small className="sticker-library-sub-copy">
            照片保存与回忆将在后续提供。
          </small>
          <small className="sticker-library-note-copy">
            已学习的表情会在开启“合适的时候发送表情”时由角色主动发出。
          </small>
        </div>
      </div>

      <div className="sticker-library-header">
        <div className="sticker-library-count">
          <h4>已学表情</h4>
          <span>
            {count} / {capacity}
          </span>
        </div>
        <button
          type="button"
          className="sticker-library-refresh-button"
          disabled={isBusy || !runtimeOnline}
          onClick={handleManualRefresh}
          aria-label="刷新表情库"
        >
          {loading ? "正在刷新…" : "刷新"}
        </button>
      </div>

      {error ? (
        <div className="sticker-library-error" role="alert">
          {error}
        </div>
      ) : null}

      {loading && !snapshot ? (
        <div className="sticker-library-loading" role="status">
          正在加载表情库…
        </div>
      ) : items.length === 0 ? (
        <div className="sticker-library-empty">
          <p>暂无已学习的表情</p>
          <small>
            {learningEnabled
              ? "在聊天中发送适合的表情图，宁宁会自动筛选并保存到这里。"
              : "开启“学习我发来的表情”后，发送的表情图经自动筛选后才会被收录。"}
          </small>
        </div>
      ) : (
        <div className="sticker-library-grid" role="list">
          {items.map((sticker) => (
            <StickerTile
              key={sticker.sticker_id}
              sticker={sticker}
              characterId={characterId}
              isDeleting={deletingId === sticker.sticker_id}
              disabled={!runtimeOnline || isBusy}
              onDelete={() => void handleDelete(sticker.sticker_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface StickerTileProps {
  sticker: LearnedSticker;
  characterId: string;
  isDeleting: boolean;
  disabled: boolean;
  onDelete: () => void;
}

function StickerTile({
  sticker,
  characterId,
  isDeleting,
  disabled,
  onDelete,
}: StickerTileProps) {
  const [imageState, setImageState] = useState<{
    stickerId: string;
    url: string | null;
    loading: boolean;
    error: boolean;
  }>({
    stickerId: sticker.sticker_id,
    url: null,
    loading: true,
    error: false,
  });
  const currentUrlRef = useRef<string | null>(null);
  const [isVisible, setIsVisible] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const currentLoading =
    imageState.stickerId === sticker.sticker_id ? imageState.loading : true;
  const currentError =
    imageState.stickerId === sticker.sticker_id ? imageState.error : false;
  const currentUrl =
    imageState.stickerId === sticker.sticker_id ? imageState.url : null;

  // Lazy thumbnail trigger: activate download only when near/in viewport
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
        const url = await fetchStickerImageUrl(sticker.sticker_id, {
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
            stickerId: sticker.sticker_id,
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
            stickerId: sticker.sticker_id,
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
  }, [sticker.sticker_id, characterId, isVisible]);

  return (
    <div
      ref={containerRef}
      className="sticker-library-tile"
      role="listitem"
      data-testid={`sticker-tile-${sticker.sticker_id}`}
    >
      <div className="sticker-library-thumbnail-wrap">
        {currentLoading ? (
          <div className="sticker-library-thumbnail-placeholder" role="status">
            加载中…
          </div>
        ) : currentError || !currentUrl ? (
          <div className="sticker-library-thumbnail-placeholder error">
            图片加载失败
          </div>
        ) : (
          <img
            src={currentUrl}
            alt={sticker.label}
            className="sticker-library-thumbnail"
          />
        )}
      </div>

      <div className="sticker-library-tile-info">
        <strong className="sticker-library-tile-label" title={sticker.label}>
          {sticker.label}
        </strong>
        <span className="sticker-library-tile-desc" title={sticker.description}>
          {sticker.description}
        </span>
        <div className="sticker-library-tile-meta">
          <span className="sticker-library-tile-expression">
            {EXPRESSION_LABELS[sticker.expression] ?? sticker.expression}
          </span>
          <span className="sticker-library-tile-date">
            {formatDate(sticker.learned_at)}
          </span>
        </div>
      </div>

      <button
        type="button"
        className="sticker-library-delete-button"
        disabled={disabled || isDeleting}
        onClick={onDelete}
        aria-label={`删除表情 ${sticker.label}`}
      >
        {isDeleting ? "删除中…" : "删除"}
      </button>
    </div>
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
