import type { AvatarController } from "@chatwaifu/avatar-sdk";
import type {
  AiriMotionDriver,
  AiriMotionProfile,
} from "@chatwaifu/avatar-sdk/experimental/airi-motion";
import { useEffect, useRef, useState, type RefObject } from "react";
import type { RendererKind } from "./useAvatarLab";

type MotionMode = "original" | AiriMotionProfile;

export function AiriMotionPanel({
  controllerRef,
  rendererKind,
  ready,
}: {
  controllerRef: RefObject<AvatarController | null>;
  rendererKind: RendererKind;
  ready: boolean;
}) {
  const [mode, setMode] = useState<MotionMode>(() =>
    new URLSearchParams(window.location.search).get("motion") === "airi"
      ? "idle-calm"
      : "original",
  );
  const [intensity, setIntensity] = useState(0.65);
  const [result, setResult] = useState<{ key: string; error: string } | null>(
    null,
  );
  const selectionKey = `${rendererKind}:${mode}:${intensity}`;
  const loading =
    mode !== "original" && (!ready || result?.key !== selectionKey);
  const error = result?.key === selectionKey ? result.error : "";
  const driverRef = useRef<AiriMotionDriver | null>(null);
  const profileRef = useRef<AiriMotionProfile>("idle-calm");

  useEffect(() => {
    let cancelled = false;
    const controller = controllerRef.current;
    if (!controller || !ready) return;
    if (mode === "original") {
      const driver = driverRef.current;
      driver?.configure({ profile: profileRef.current, intensity: 0 });
      controller.setProceduralMotionSource(driver);
      return;
    }
    void import("@chatwaifu/avatar-sdk/experimental/airi-motion")
      .then(({ AiriMotionDriver }) => {
        if (cancelled || controllerRef.current !== controller) return;
        const options = { profile: mode, intensity };
        const driver = driverRef.current ?? new AiriMotionDriver(options);
        driver.configure(options);
        driverRef.current = driver;
        profileRef.current = mode;
        controller.setProceduralMotionSource(driver);
        setResult({ key: selectionKey, error: "" });
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        controller.setProceduralMotionSource(null);
        setResult({
          key: selectionKey,
          error: cause instanceof Error ? cause.message : "动作样本加载失败",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [controllerRef, rendererKind, ready, mode, intensity, selectionKey]);

  return (
    <section className="lab-panel airi-motion-panel" aria-label="AIRI 动作实验">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">AIRI motion experiment</p>
          <h2>宁宁的动作试验</h2>
        </div>
        <button type="button" onClick={() => controllerRef.current?.reset()}>
          从头播放
        </button>
      </div>
      <label className="airi-motion-field">
        动作风格
        <select
          value={mode}
          onChange={(event) => setMode(event.target.value as MotionMode)}
        >
          <option value="original">CW2 原有动作</option>
          <option value="idle-calm">AIRI · 安静陪伴</option>
          <option value="speaking-excited">AIRI · 活泼交谈</option>
        </select>
      </label>
      <label className="airi-motion-field">
        动作幅度 · {Math.round(intensity * 100)}%
        <input
          aria-label="动作幅度"
          type="range"
          min="0"
          max="100"
          step="5"
          value={Math.round(intensity * 100)}
          disabled={mode === "original"}
          onChange={(event) => setIntensity(Number(event.target.value) / 100)}
        />
      </label>
      <p role="status">
        {error
          ? "动作未启用"
          : loading
            ? "正在准备动作…"
            : mode === "original"
              ? "正在使用原有动作"
              : "动作已启用 · 可切换风格或调整幅度"}
      </p>
      <p className="airi-motion-note">
        下方可试说话、表情和手势。手势与打断会让动作生成暂时让位。
      </p>
      {error ? <p role="alert">{error}</p> : null}
    </section>
  );
}
