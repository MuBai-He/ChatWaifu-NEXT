import type { AvatarCue } from "@chatwaifu/protocol";

import type { RendererKind } from "./useAvatarLab";

interface AvatarDebugPanelProps {
  rendererKind: RendererKind;
  onRendererChange: (kind: RendererKind) => void;
  onCue: (
    kind: AvatarCue["kind"],
    name: string,
    options?: Partial<AvatarCue>,
  ) => void;
  onReset: () => void;
}

const CUES: Array<{ kind: AvatarCue["kind"]; name: string }> = [
  { kind: "state", name: "listening" },
  { kind: "state", name: "thinking" },
  { kind: "state", name: "speaking" },
  { kind: "expression", name: "happy" },
  { kind: "expression", name: "curious" },
  { kind: "expression", name: "shy" },
  { kind: "expression", name: "sad" },
  { kind: "expression", name: "angry" },
  { kind: "expression", name: "surprised" },
  { kind: "motion", name: "headpat" },
  { kind: "motion", name: "stare" },
  { kind: "motion", name: "flustered" },
  { kind: "motion", name: "sing" },
  { kind: "gaze", name: "pointer" },
];

export function AvatarDebugPanel({
  rendererKind,
  onRendererChange,
  onCue,
  onReset,
}: AvatarDebugPanelProps) {
  return (
    <section className="lab-panel debug-panel">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">Semantic input</p>
          <h2>Cue console</h2>
        </div>
        <label>
          Renderer
          <select
            value={rendererKind}
            onChange={(event) =>
              onRendererChange(event.target.value as RendererKind)
            }
          >
            <option value="fake">Fake / CI</option>
            <option value="live2d">Official Live2D bridge</option>
          </select>
        </label>
      </div>
      <div className="cue-grid">
        {CUES.map((cue) => (
          <button
            key={`${cue.kind}:${cue.name}`}
            type="button"
            className={`cue-button cue-${cue.kind}`}
            aria-label={cue.name}
            onClick={() => {
              onCue(cue.kind, cue.name);
              if (cue.name === "speaking") onCue("speech", "speaking");
            }}
          >
            <small>{cue.kind}</small>
            {cue.name}
          </button>
        ))}
        <button
          type="button"
          className="cue-button cue-override"
          aria-label="interrupt"
          onClick={() =>
            onCue("override", "interrupt", {
              priority: 100,
              duration_ms: 500,
            })
          }
        >
          <small>override</small>
          interrupt
        </button>
        <button
          type="button"
          className="cue-button cue-reset"
          onClick={onReset}
        >
          <small>scheduler</small>
          reset
        </button>
      </div>
    </section>
  );
}
