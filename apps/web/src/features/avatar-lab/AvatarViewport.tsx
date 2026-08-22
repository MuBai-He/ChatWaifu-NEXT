import type { AvatarControllerSnapshot } from "@chatwaifu/avatar-sdk";
import type { RefObject } from "react";
import { useEffect, useRef } from "react";

interface AvatarViewportProps {
  canvasRef: RefObject<HTMLCanvasElement | null>;
  snapshot: AvatarControllerSnapshot | null;
  onResize: (width: number, height: number, dpr: number) => void;
  onPointer: (x: number, y: number) => void;
}

export function AvatarViewport({
  canvasRef,
  snapshot,
  onResize,
  onPointer,
}: AvatarViewportProps) {
  const frameRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const update = () => {
      const bounds = frame.getBoundingClientRect();
      onResize(bounds.width, bounds.height, window.devicePixelRatio || 1);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(frame);
    return () => observer.disconnect();
  }, [onResize]);

  return (
    <section className="avatar-stage" aria-label="Avatar viewport">
      <div className="avatar-canvas-frame" ref={frameRef}>
        <canvas
          ref={canvasRef}
          data-testid="avatar-canvas"
          aria-label="Avatar rendering canvas"
          role="img"
          onPointerDown={(event) => {
            const bounds = event.currentTarget.getBoundingClientRect();
            onPointer(event.clientX - bounds.left, event.clientY - bounds.top);
          }}
        />
        <div className="render-badge" data-testid="renderer-status">
          renderer · {snapshot?.status ?? "initializing"}
        </div>
        <div className="semantic-readout" data-testid="semantic-state">
          <strong>{snapshot?.runtime.state ?? "idle"}</strong>
          <span>{snapshot?.runtime.expression ?? "neutral"}</span>
          <span>{snapshot?.runtime.motion ?? "no motion"}</span>
        </div>
      </div>
    </section>
  );
}
