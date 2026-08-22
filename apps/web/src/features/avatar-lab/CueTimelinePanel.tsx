import type { AvatarControllerSnapshot } from "@chatwaifu/avatar-sdk";
import type { AvatarInteractionEvent } from "@chatwaifu/protocol";

interface CueTimelinePanelProps {
  snapshot: AvatarControllerSnapshot | null;
  interactions: AvatarInteractionEvent[];
}

export function CueTimelinePanel({
  snapshot,
  interactions,
}: CueTimelinePanelProps) {
  const active = Object.entries(snapshot?.scheduler.active ?? {});
  const telemetry = snapshot?.telemetry;
  return (
    <section className="lab-panel timeline-panel">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">Reducer output</p>
          <h2>Cue timeline</h2>
        </div>
        <span className="source-chip">
          rev {snapshot?.scheduler.revision ?? 0}
        </span>
      </div>
      <div className="timeline-layers">
        {active.length ? (
          active.map(([layer, scheduled]) => (
            <div className="timeline-row" key={layer}>
              <span>{layer}</span>
              <strong>{scheduled?.cue.name}</strong>
              <small>p{scheduled?.cue.priority ?? 50}</small>
            </div>
          ))
        ) : (
          <p className="empty-state">No active cues · neutral fallback</p>
        )}
      </div>
      <div className="telemetry-grid">
        <Metric label="FPS" value={telemetry?.fps.toFixed(1) ?? "0.0"} />
        <Metric
          label="Frame"
          value={`${telemetry?.frameTimeMs.toFixed(2) ?? "0.00"} ms`}
        />
        <Metric label="Dropped" value={String(telemetry?.droppedFrames ?? 0)} />
        <Metric
          label="Resources"
          value={String(telemetry?.rendererResources ?? 0)}
        />
        <Metric
          label="Audio nodes"
          value={String(telemetry?.activeAudioNodes ?? 0)}
        />
        <Metric
          label="Context loss"
          value={String(telemetry?.contextLosses ?? 0)}
        />
      </div>
      <div className="interaction-log">
        <p className="panel-kicker">Semantic interaction</p>
        <strong data-testid="last-interaction">
          {interactions[0]?.target ?? "touch the avatar"}
        </strong>
      </div>
      {snapshot?.warnings.length ? (
        <ul className="warning-list">
          {snapshot.warnings.slice(-3).map((warning, index) => (
            <li key={`${warning.code}-${index}`}>{warning.code}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
