import type { AvatarRenderer } from "./renderer";
import type {
  AvatarHitResult,
  AvatarManifest,
  AvatarRendererDiagnostics,
  AvatarRuntimeState,
} from "./types";

export class FakeAvatarRenderer implements AvatarRenderer {
  readonly kind = "fake" as const;
  private status: AvatarRendererDiagnostics["status"] = "idle";
  private manifest: AvatarManifest | null = null;
  private width = 640;
  private height = 640;
  private dpr = 1;
  private resourceCount = 0;
  private lastState: AvatarRuntimeState | null = null;
  private readonly context: CanvasRenderingContext2D | null;

  constructor(private readonly canvas?: HTMLCanvasElement) {
    this.context = safeCanvasContext(canvas);
  }

  async load(manifest: AvatarManifest): Promise<void> {
    this.status = "loading";
    await Promise.resolve();
    this.manifest = manifest;
    this.resourceCount = 1;
    this.status = "ready";
  }

  async unload(): Promise<void> {
    this.manifest = null;
    this.lastState = null;
    this.resourceCount = 0;
    this.status = "idle";
    this.context?.clearRect(0, 0, this.width, this.height);
  }

  render(state: AvatarRuntimeState): void {
    if (this.status !== "ready") return;
    this.lastState = state;
    if (!this.context || !this.canvas) return;
    this.draw(state);
  }

  hitTest(x: number, y: number): AvatarHitResult[] {
    if (this.status !== "ready" || !this.manifest) return [];
    const normalizedX = x / Math.max(1, this.width);
    const normalizedY = y / Math.max(1, this.height);
    const modelX = normalizedX * 2 - 1;
    const modelY = 1 - normalizedY * 2;
    if (
      normalizedX >= 0.24 &&
      normalizedX <= 0.76 &&
      normalizedY >= 0.1 &&
      normalizedY <= 0.52
    ) {
      return [this.hit("head", "touched_head", modelX, modelY)];
    }
    if (
      normalizedX >= 0.2 &&
      normalizedX <= 0.8 &&
      normalizedY > 0.52 &&
      normalizedY <= 0.94
    ) {
      return [this.hit("body", "touched_body", modelX, modelY)];
    }
    return [];
  }

  resize(width: number, height: number, dpr: number): void {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
    this.dpr = Math.max(1, dpr);
    if (!this.canvas || !this.context) return;
    this.canvas.width = Math.round(this.width * this.dpr);
    this.canvas.height = Math.round(this.height * this.dpr);
    this.canvas.style.width = `${this.width}px`;
    this.canvas.style.height = `${this.height}px`;
    this.context.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }

  diagnostics(): AvatarRendererDiagnostics {
    return {
      status: this.status,
      resourceCount: this.resourceCount,
      contextLosses: 0,
    };
  }

  dispose(): void {
    this.manifest = null;
    this.lastState = null;
    this.resourceCount = 0;
    this.status = "disposed";
  }

  getLastState(): AvatarRuntimeState | null {
    return this.lastState;
  }

  private hit(
    areaId: string,
    defaultTarget: string,
    modelX: number,
    modelY: number,
  ): AvatarHitResult {
    const mapped = this.manifest?.hitAreas?.find((area) => area.id === areaId);
    return {
      areaId,
      semanticTarget: mapped?.semanticTarget ?? defaultTarget,
      confidence: 1,
      modelX,
      modelY,
    };
  }

  private draw(state: AvatarRuntimeState): void {
    const context = this.context;
    if (!context) return;
    const centerX = this.width / 2;
    const centerY = this.height * 0.46;
    const radius = Math.min(this.width, this.height) * 0.27;
    const breathe = state.procedural.breath * 3;
    context.clearRect(0, 0, this.width, this.height);

    const glow = context.createRadialGradient(
      centerX,
      centerY,
      radius * 0.2,
      centerX,
      centerY,
      radius * 1.8,
    );
    glow.addColorStop(
      0,
      state.interrupted ? "rgba(255,109,138,.42)" : "rgba(184,135,255,.34)",
    );
    glow.addColorStop(1, "rgba(44,31,62,0)");
    context.fillStyle = glow;
    context.fillRect(0, 0, this.width, this.height);

    context.save();
    context.translate(
      state.procedural.bodyYaw * radius * 0.06,
      breathe + state.procedural.bodyPitch * radius * 0.04,
    );
    context.rotate(state.procedural.bodyRoll * 0.08);
    context.fillStyle = state.expression === "happy" ? "#f9d7ea" : "#eadcf6";
    context.beginPath();
    context.arc(centerX, centerY, radius, 0, Math.PI * 2);
    context.fill();

    const gazeOffsetX = state.procedural.eyeX * radius * 0.08;
    const gazeOffsetY = state.procedural.eyeY * radius * 0.055;
    const eyeRadius = radius * 0.07 * state.procedural.eyeOpen;
    context.fillStyle = "#2a1c37";
    context.beginPath();
    context.arc(
      centerX - radius * 0.35 + gazeOffsetX,
      centerY - radius * 0.12 - gazeOffsetY,
      eyeRadius,
      0,
      Math.PI * 2,
    );
    context.arc(
      centerX + radius * 0.35 + gazeOffsetX,
      centerY - radius * 0.12 - gazeOffsetY,
      eyeRadius,
      0,
      Math.PI * 2,
    );
    context.fill();

    const mouthHeight = 4 + state.mouthOpen * radius * 0.24;
    context.fillStyle = "#7b355f";
    context.beginPath();
    context.ellipse(
      centerX,
      centerY + radius * 0.32,
      radius * (0.18 + state.procedural.mouthForm * 0.025),
      mouthHeight,
      0,
      0,
      Math.PI * 2,
    );
    context.fill();
    context.restore();

    context.fillStyle = "rgba(255,255,255,.72)";
    context.font = "600 13px ui-monospace, monospace";
    context.textAlign = "center";
    context.fillText(
      `${state.state} · ${state.expression}`,
      centerX,
      this.height - 28,
    );
  }
}

function safeCanvasContext(
  canvas: HTMLCanvasElement | undefined,
): CanvasRenderingContext2D | null {
  if (!canvas) return null;
  try {
    return canvas.getContext("2d");
  } catch {
    return null;
  }
}
