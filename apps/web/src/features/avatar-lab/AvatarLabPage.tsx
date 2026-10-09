import { useState } from "react";

import { ProductIcon } from "../../components/ProductIcon";
import { AudioDebugPanel } from "./AudioDebugPanel";
import { AiriMotionPanel } from "./AiriMotionPanel";
import { AvatarDebugPanel } from "./AvatarDebugPanel";
import { AvatarViewport } from "./AvatarViewport";
import { CueTimelinePanel } from "./CueTimelinePanel";
import { useAvatarLab, type RendererKind } from "./useAvatarLab";
import "./avatar-lab.css";

export function AvatarLabPage() {
  const experiment =
    new URLSearchParams(window.location.search).get("motion") === "airi";
  const [rendererKind, setRendererKind] = useState<RendererKind>(() =>
    new URLSearchParams(window.location.search).get("motion") === "airi"
      ? "live2d"
      : "fake",
  );
  const lab = useAvatarLab(rendererKind);

  return (
    <main
      className={`avatar-lab-page${experiment ? " avatar-lab-experiment" : ""}`}
    >
      <header className="lab-hero">
        <div>
          <a href="/" className="back-link">
            <ProductIcon name="back" />
            ChatWaifu NEXT
          </a>
          <p className="eyebrow">
            {experiment
              ? "CW2 × AIRI · Motion Lab"
              : "Phase 2 · isolated renderer laboratory"}
          </p>
          <h1>{experiment ? "宁宁 · 动作实验" : "Live2D Avatar Lab"}</h1>
        </div>
        <p>
          {experiment
            ? "切换安静与活泼的动作，看看哪一种更适合宁宁。也可以随时回到原有动作。"
            : "High-level AvatarCue in, renderer state out. No Runtime, model, Pipecat, or Tauri process is involved."}
        </p>
      </header>

      {lab.error ? (
        <aside className="renderer-error" data-testid="renderer-error">
          <strong>{lab.error.code}</strong>
          <span>{lab.error.message}</span>
          {lab.error.action ? <code>{lab.error.action}</code> : null}
        </aside>
      ) : null}

      <div className="lab-layout">
        <AvatarViewport
          key={rendererKind}
          canvasRef={lab.canvasRef}
          snapshot={lab.snapshot}
          onResize={lab.resize}
          onPointer={lab.handlePointer}
        />
        <div className="lab-controls">
          <AiriMotionPanel
            controllerRef={lab.controllerRef}
            rendererKind={rendererKind}
            ready={lab.snapshot?.status === "ready"}
          />
          <AvatarDebugPanel
            rendererKind={rendererKind}
            onRendererChange={setRendererKind}
            onCue={lab.sendCue}
            onReset={lab.reset}
          />
          <AudioDebugPanel
            onStart={lab.startSpeaking}
            onStop={lab.stopSpeaking}
          />
          <CueTimelinePanel
            snapshot={lab.snapshot}
            interactions={lab.interactions}
          />
        </div>
      </div>
    </main>
  );
}
