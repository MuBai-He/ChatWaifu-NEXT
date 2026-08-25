import type { AvatarBehaviorMode, AvatarProceduralFrame } from "./types";

export interface AvatarBehaviorInput {
  state: string;
  expression: string;
  gaze: string;
  speaking: boolean;
  interrupted: boolean;
  speechEnergy: number;
}

interface SpringState {
  value: number;
  velocity: number;
}

type SpringChannel = Exclude<keyof AvatarProceduralFrame, "mode">;

interface ChannelTarget {
  target: number;
  response: number;
}

const SPRING_CHANNELS: SpringChannel[] = [
  "headYaw",
  "headPitch",
  "headRoll",
  "bodyYaw",
  "bodyPitch",
  "bodyRoll",
  "eyeX",
  "eyeY",
  "eyeOpen",
  "browLift",
  "mouthForm",
  "breath",
];

const RESPONSE: Record<SpringChannel, number> = {
  headYaw: 4.2,
  headPitch: 4.6,
  headRoll: 4,
  bodyYaw: 2.2,
  bodyPitch: 2.4,
  bodyRoll: 2.1,
  eyeX: 12,
  eyeY: 12,
  eyeOpen: 18,
  browLift: 7,
  mouthForm: 8,
  breath: 2.1,
};

export function neutralProceduralFrame(
  mode: AvatarBehaviorMode = "idle",
): AvatarProceduralFrame {
  return {
    mode,
    headYaw: 0,
    headPitch: 0,
    headRoll: 0,
    bodyYaw: 0,
    bodyPitch: 0,
    bodyRoll: 0,
    eyeX: 0,
    eyeY: 0,
    eyeOpen: 1,
    browLift: 0,
    mouthForm: 0,
    breath: 0,
  };
}

/**
 * Turns semantic character state into a renderer-neutral continuous pose.
 * Live2D parameter identifiers deliberately stay in the renderer adapter.
 */
export class AvatarBehaviorStateMachine {
  private readonly springs = new Map<SpringChannel, SpringState>();
  private readonly phases: [number, number, number, number];
  private readonly eventSeedState: number;
  private randomState: number;
  private lastNowMs: number | null = null;
  private mode: AvatarBehaviorMode = "idle";
  private blinkStartedAtMs: number | null = null;
  private nextBlinkAtMs: number | null = null;
  private nextSaccadeAtMs: number | null = null;
  private saccadeX = 0;
  private saccadeY = 0;

  constructor(seed = 0x4e454e45) {
    this.randomState = seed >>> 0;
    this.phases = [
      this.random() * Math.PI * 2,
      this.random() * Math.PI * 2,
      this.random() * Math.PI * 2,
      this.random() * Math.PI * 2,
    ];
    this.eventSeedState = this.randomState;
    for (const channel of SPRING_CHANNELS) {
      this.springs.set(channel, {
        value: channel === "eyeOpen" ? 1 : 0,
        velocity: 0,
      });
    }
  }

  step(input: AvatarBehaviorInput, nowMs: number): AvatarProceduralFrame {
    const resolvedMode = resolveMode(input);
    if (resolvedMode !== this.mode) this.enterMode(resolvedMode, nowMs);
    this.mode = resolvedMode;

    const deltaSeconds =
      this.lastNowMs === null
        ? 0
        : clamp((nowMs - this.lastNowMs) / 1_000, 0, 0.1);
    this.lastNowMs = nowMs;
    this.updateEvents(nowMs);

    const targets = this.mixTargets(input, nowMs);
    const frame = neutralProceduralFrame(this.mode);
    for (const channel of SPRING_CHANNELS) {
      const spring = this.springs.get(channel);
      if (!spring) continue;
      integrateSpring(
        spring,
        targets[channel].target,
        targets[channel].response,
        deltaSeconds,
      );
      frame[channel] = boundedChannel(channel, spring.value);
    }
    return frame;
  }

  reset(): void {
    this.randomState = this.eventSeedState;
    this.lastNowMs = null;
    this.mode = "idle";
    this.blinkStartedAtMs = null;
    this.nextBlinkAtMs = null;
    this.nextSaccadeAtMs = null;
    this.saccadeX = 0;
    this.saccadeY = 0;
    for (const [channel, spring] of this.springs) {
      spring.value = channel === "eyeOpen" ? 1 : 0;
      spring.velocity = 0;
    }
  }

  private enterMode(mode: AvatarBehaviorMode, nowMs: number): void {
    this.nextSaccadeAtMs = nowMs;
    if (mode === "interrupted") {
      this.blinkStartedAtMs = null;
      this.nextBlinkAtMs = nowMs + 1_200;
      this.saccadeX = 0;
      this.saccadeY = 0;
    }
  }

  private updateEvents(nowMs: number): void {
    if (this.nextBlinkAtMs === null) {
      this.nextBlinkAtMs = nowMs + this.randomBetween(1_800, 4_200);
    }
    if (
      this.mode !== "interrupted" &&
      this.blinkStartedAtMs === null &&
      nowMs >= this.nextBlinkAtMs
    ) {
      this.blinkStartedAtMs = nowMs;
    }
    if (
      this.blinkStartedAtMs !== null &&
      nowMs - this.blinkStartedAtMs >= 180
    ) {
      this.blinkStartedAtMs = null;
      this.nextBlinkAtMs = nowMs + this.randomBetween(1_800, 4_200);
    }

    if (this.nextSaccadeAtMs === null) this.nextSaccadeAtMs = nowMs;
    if (nowMs >= this.nextSaccadeAtMs) {
      const strength = this.mode === "thinking" ? 0.18 : 0.09;
      this.saccadeX = this.randomBetween(-strength, strength);
      this.saccadeY = this.randomBetween(-strength * 0.45, strength * 0.45);
      const interval =
        this.mode === "thinking"
          ? this.randomBetween(550, 1_200)
          : this.randomBetween(1_100, 2_700);
      this.nextSaccadeAtMs = nowMs + interval;
    }
  }

  private mixTargets(
    input: AvatarBehaviorInput,
    nowMs: number,
  ): Record<SpringChannel, ChannelTarget> {
    const seconds = nowMs / 1_000;
    const micro =
      Math.sin(seconds * 0.71 + this.phases[0]) * 0.55 +
      Math.sin(seconds * 1.13 + this.phases[1]) * 0.3 +
      Math.sin(seconds * 0.37 + this.phases[2]) * 0.15;
    const drift =
      Math.sin(seconds * 0.43 + this.phases[2]) * 0.65 +
      Math.sin(seconds * 0.83 + this.phases[3]) * 0.35;
    const speechEnergy = clamp(input.speechEnergy, 0, 1);
    const speechPulse =
      Math.sin(seconds * Math.PI * 3.2 + this.phases[3]) * speechEnergy;
    const mode = modeBias(this.mode, micro, drift, speechPulse);
    const expression = expressionBias(input.expression);
    const pointerX = input.gaze === "pointer" ? 0.38 : 0;
    const blink = blinkTarget(this.blinkStartedAtMs, nowMs);

    const raw: Record<SpringChannel, number> = {
      headYaw: mode.headYaw + expression.headYaw,
      headPitch: mode.headPitch + expression.headPitch,
      headRoll: mode.headRoll + expression.headRoll,
      bodyYaw: mode.bodyYaw,
      bodyPitch: mode.bodyPitch,
      bodyRoll: mode.bodyRoll + expression.bodyRoll,
      eyeX: mode.eyeX + this.saccadeX + pointerX,
      eyeY: mode.eyeY + this.saccadeY + expression.eyeY,
      eyeOpen:
        this.mode === "interrupted"
          ? 1
          : Math.min(mode.eyeOpen, expression.eyeOpen, blink),
      browLift: mode.browLift + expression.browLift,
      mouthForm: expression.mouthForm,
      breath: mode.breath,
    };

    return Object.fromEntries(
      SPRING_CHANNELS.map((channel) => [
        channel,
        {
          target: boundedChannel(channel, raw[channel]),
          response:
            this.mode === "interrupted"
              ? Math.max(RESPONSE[channel], 10)
              : RESPONSE[channel],
        },
      ]),
    ) as Record<SpringChannel, ChannelTarget>;
  }

  private random(): number {
    let value = this.randomState;
    value ^= value << 13;
    value ^= value >>> 17;
    value ^= value << 5;
    this.randomState = value >>> 0;
    return this.randomState / 0x1_0000_0000;
  }

  private randomBetween(minimum: number, maximum: number): number {
    return minimum + (maximum - minimum) * this.random();
  }
}

function resolveMode(input: AvatarBehaviorInput): AvatarBehaviorMode {
  if (input.interrupted) return "interrupted";
  if (input.speaking || input.state === "speaking") return "speaking";
  if (input.state === "listening") return "listening";
  if (input.state === "thinking") return "thinking";
  return "idle";
}

function modeBias(
  mode: AvatarBehaviorMode,
  micro: number,
  drift: number,
  speechPulse: number,
): Omit<AvatarProceduralFrame, "mode"> {
  const idle = {
    headYaw: micro * 0.08,
    headPitch: drift * 0.035,
    headRoll: micro * 0.03,
    bodyYaw: drift * 0.035,
    bodyPitch: micro * 0.02,
    bodyRoll: micro * 0.018,
    eyeX: 0,
    eyeY: 0,
    eyeOpen: 1,
    browLift: 0,
    mouthForm: 0,
    breath: drift * 0.42,
  };
  if (mode === "listening") {
    return {
      ...idle,
      headYaw: micro * 0.035,
      headPitch: 0.035 + drift * 0.02,
      bodyYaw: drift * 0.015,
      eyeOpen: 0.98,
      browLift: 0.08,
      breath: drift * 0.28,
    };
  }
  if (mode === "thinking") {
    return {
      ...idle,
      headYaw: 0.12 + micro * 0.045,
      headPitch: -0.08 + drift * 0.025,
      headRoll: 0.055 + micro * 0.02,
      bodyYaw: 0.04,
      eyeX: 0.24,
      eyeY: 0.22,
      browLift: 0.12,
      breath: drift * 0.25,
    };
  }
  if (mode === "speaking") {
    return {
      ...idle,
      headYaw: micro * 0.055,
      headPitch: speechPulse * 0.085 + drift * 0.025,
      headRoll: micro * 0.025,
      bodyYaw: drift * 0.025,
      bodyPitch: speechPulse * 0.025,
      eyeOpen: 0.97,
      browLift: speechPulse * 0.05,
      breath: drift * 0.3,
    };
  }
  if (mode === "interrupted") {
    return {
      ...idle,
      headYaw: 0,
      headPitch: 0,
      headRoll: 0,
      bodyYaw: 0,
      bodyPitch: 0,
      bodyRoll: 0,
      eyeX: 0,
      eyeY: 0,
      eyeOpen: 1,
      browLift: 0.18,
      breath: 0,
    };
  }
  return idle;
}

function expressionBias(expression: string): {
  headYaw: number;
  headPitch: number;
  headRoll: number;
  bodyRoll: number;
  eyeY: number;
  eyeOpen: number;
  browLift: number;
  mouthForm: number;
} {
  switch (expression) {
    case "happy":
      return bias({ eyeOpen: 0.93, browLift: 0.12, mouthForm: 0.3 });
    case "curious":
      return bias({ headRoll: 0.08, browLift: 0.28, eyeY: 0.08 });
    case "shy":
      return bias({
        headYaw: -0.06,
        headPitch: 0.1,
        bodyRoll: -0.035,
        eyeY: -0.14,
        eyeOpen: 0.91,
        mouthForm: 0.08,
      });
    case "sad":
      return bias({
        headPitch: 0.13,
        eyeY: -0.12,
        eyeOpen: 0.86,
        browLift: -0.18,
        mouthForm: -0.24,
      });
    case "angry":
      return bias({
        headPitch: -0.04,
        eyeOpen: 0.88,
        browLift: -0.3,
        mouthForm: -0.18,
      });
    case "surprised":
      return bias({
        headPitch: -0.08,
        eyeOpen: 1,
        browLift: 0.48,
        mouthForm: 0.32,
      });
    default:
      return bias({});
  }
}

function bias(
  overrides: Partial<ReturnType<typeof expressionBias>>,
): ReturnType<typeof expressionBias> {
  return {
    headYaw: 0,
    headPitch: 0,
    headRoll: 0,
    bodyRoll: 0,
    eyeY: 0,
    eyeOpen: 1,
    browLift: 0,
    mouthForm: 0,
    ...overrides,
  };
}

function blinkTarget(startedAtMs: number | null, nowMs: number): number {
  if (startedAtMs === null) return 1;
  const phase = clamp((nowMs - startedAtMs) / 180, 0, 1);
  if (phase < 0.42) return 1 - smoothstep(phase / 0.42);
  return smoothstep((phase - 0.42) / 0.58);
}

function integrateSpring(
  spring: SpringState,
  target: number,
  response: number,
  deltaSeconds: number,
): void {
  if (deltaSeconds <= 0) return;
  const steps = Math.max(1, Math.ceil(deltaSeconds / (1 / 120)));
  const stepSeconds = deltaSeconds / steps;
  const omega = Math.PI * 2 * response;
  for (let step = 0; step < steps; step += 1) {
    const acceleration =
      omega * omega * (target - spring.value) - 2 * omega * spring.velocity;
    spring.velocity += acceleration * stepSeconds;
    spring.value += spring.velocity * stepSeconds;
  }
}

function boundedChannel(channel: SpringChannel, value: number): number {
  if (channel === "eyeOpen") return clamp(value, 0, 1);
  return clamp(value, -1, 1);
}

function smoothstep(value: number): number {
  const bounded = clamp(value, 0, 1);
  return bounded * bounded * (3 - 2 * bounded);
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
