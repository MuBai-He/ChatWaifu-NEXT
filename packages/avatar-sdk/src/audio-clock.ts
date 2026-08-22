export interface AnimationClock {
  now(): number;
  requestFrame(callback: FrameRequestCallback): number;
  cancelFrame(handle: number): void;
}

export class BrowserAnimationClock implements AnimationClock {
  now(): number {
    return performance.now();
  }

  requestFrame(callback: FrameRequestCallback): number {
    return requestAnimationFrame(callback);
  }

  cancelFrame(handle: number): void {
    cancelAnimationFrame(handle);
  }
}

export interface AudioClock {
  nowSeconds(): number;
}

export class WebAudioClock implements AudioClock {
  constructor(private readonly context: BaseAudioContext) {}

  nowSeconds(): number {
    return this.context.currentTime;
  }
}
