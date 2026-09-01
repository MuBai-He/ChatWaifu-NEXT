import { beforeEach, describe, expect, it } from "vitest";

import {
  completeDesktopOnboarding,
  desktopOnboardingCompletionKey,
  isDesktopOnboardingCompleted,
  releaseDesktopOnboardingAutoOpen,
  shouldAutoOpenDesktopOnboarding,
} from "./desktopOnboarding";

describe("desktop onboarding persistence", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("auto-opens once per desktop session until the guide is complete", () => {
    expect(shouldAutoOpenDesktopOnboarding(false)).toBe(false);
    expect(shouldAutoOpenDesktopOnboarding(true)).toBe(true);
    expect(shouldAutoOpenDesktopOnboarding(true)).toBe(false);

    releaseDesktopOnboardingAutoOpen();
    expect(shouldAutoOpenDesktopOnboarding(true)).toBe(true);
  });

  it("persists completion without storing provider credentials", () => {
    completeDesktopOnboarding();

    expect(isDesktopOnboardingCompleted()).toBe(true);
    expect(window.localStorage.length).toBe(1);
    expect(window.localStorage.getItem(desktopOnboardingCompletionKey)).toBe(
      "true",
    );
    expect(shouldAutoOpenDesktopOnboarding(true)).toBe(false);
  });
});
