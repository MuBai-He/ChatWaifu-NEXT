import "../settings/settings-glass.css";
import "../settings/settings-controls.css";
import { useEffect, useId, useRef, useState } from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentProxy,
  type RenderTask,
} from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { ModalPortal } from "../chat/ModalPortal";
import { useDialogNavigation } from "../connection/useDialogNavigation";
import "./agent-preview.css";

GlobalWorkerOptions.workerSrc = workerUrl;

export default function PdfArtifactPreview({
  blob,
  name,
  opener,
  onClose,
}: {
  blob: Blob;
  name: string;
  opener: HTMLElement;
  onClose: () => void;
}) {
  const titleId = useId();
  const returnFocus = useRef(opener);
  const { ref, onKeyDown } = useDialogNavigation<HTMLElement>(
    true,
    onClose,
    returnFocus,
  );
  const canvas = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [renderedPage, setRenderedPage] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    let loading: ReturnType<typeof getDocument> | undefined;
    void blob
      .arrayBuffer()
      .then(async (data) => {
        if (!active) return;
        loading = getDocument({ data, stopAtErrors: true });
        const document = await loading.promise;
        if (document.numPages < 1 || document.numPages > 200)
          throw new Error("文档页数超出预览范围，请下载查看");
        if (active) setPdf(document);
      })
      .catch((cause: unknown) => {
        if (active)
          setError(cause instanceof Error ? cause.message : "预览加载失败");
      });
    return () => {
      active = false;
      void loading?.destroy().catch(() => undefined);
    };
  }, [blob]);

  useEffect(() => {
    if (!pdf || !canvas.current) return;
    const target = canvas.current;
    let active = true;
    let rendering: RenderTask | undefined;
    void pdf
      .getPage(pageNumber)
      .then(async (page) => {
        if (!active) return;
        const original = page.getViewport({ scale: 1 });
        if (
          !Number.isFinite(original.width) ||
          !Number.isFinite(original.height) ||
          original.width <= 0 ||
          original.height <= 0
        )
          throw new Error("文档页面尺寸无效");
        // Bound both dimensions and pixels before allocating a canvas.
        const scale = Math.min(
          (1000 / original.width) * Math.min(window.devicePixelRatio || 1, 2),
          4000 / original.width,
          4000 / original.height,
          Math.sqrt(12_000_000 / (original.width * original.height)),
        );
        const viewport = page.getViewport({ scale });
        target.width = Math.ceil(viewport.width);
        target.height = Math.ceil(viewport.height);
        rendering = page.render({ canvas: target, viewport });
        await rendering.promise;
        if (active) setRenderedPage(pageNumber);
      })
      .catch((cause: unknown) => {
        if (active)
          setError(cause instanceof Error ? cause.message : "页面渲染失败");
      });
    return () => {
      active = false;
      rendering?.cancel();
    };
  }, [pdf, pageNumber]);

  const ready = renderedPage === pageNumber;
  return (
    <ModalPortal
      className="agent-preview-overlay"
      data-native-interactive="true"
    >
      <section
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="agent-preview-dialog settings-material settings-surface settings-controls"
        onKeyDown={onKeyDown}
      >
        <header>
          <h2 id={titleId}>{name}</h2>
          <button data-dialog-close onClick={onClose}>
            关闭预览
          </button>
        </header>
        <nav aria-label="预览分页">
          <button
            disabled={!pdf || pageNumber === 1}
            onClick={() => setPageNumber((number) => number - 1)}
          >
            上一页
          </button>
          <span role="status">
            {error
              ? "预览失败"
              : ready && pdf
                ? `第 ${pageNumber} 页，共 ${pdf.numPages} 页`
                : "正在渲染文档…"}
          </span>
          <button
            disabled={!pdf || pageNumber === pdf.numPages}
            onClick={() => setPageNumber((number) => number + 1)}
          >
            下一页
          </button>
        </nav>
        <div className="agent-preview-pages">
          {error && <p role="alert">{error}。可以关闭预览后下载文件。</p>}
          <canvas
            ref={canvas}
            aria-label={`文档预览，第 ${pageNumber} 页`}
            style={{ visibility: ready && !error ? "visible" : "hidden" }}
          />
        </div>
      </section>
    </ModalPortal>
  );
}
