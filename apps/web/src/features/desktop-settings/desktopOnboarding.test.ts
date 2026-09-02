import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  completeDesktopOnboarding,
  desktopOnboardingCompletionKey,
  isDesktopOnboardingCompleted,
  releaseDesktopOnboardingAutoOpen,
  shouldAutoOpenDesktopOnboarding,
} from "./desktopOnboarding";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
}

describe("desktop onboarding persistence", () => {
  let local: Storage;
  let session: Storage;

  beforeEach(() => {
    local = memoryStorage();
    session = memoryStorage();
    vi.stubGlobal("localStorage", local);
    vi.stubGlobal("sessionStorage", session);
    Object.defineProperty(window, "localStorage", {
      value: local,
      configurable: true,
      writable: true,
    });
    Object.defineProperty(window, "sessionStorage", {
      value: session,
      configurable: true,
      writable: true,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
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
