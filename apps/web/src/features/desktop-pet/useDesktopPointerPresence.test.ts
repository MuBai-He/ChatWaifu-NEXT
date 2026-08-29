import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { acquireNativeInteractionGuard } from "../../nativeInteractionGuard";
import {
  isPointInsideWindow,
  shouldKeepDesktopInteraction,
  useDesktopPointerPresence,
} from "./useDesktopPointerPresence";

const nativeMocks = vi.hoisted(() => ({
  invoke: vi.fn().mockResolvedValue(undefined),
  cursorPosition: vi.fn().mockResolvedValue({ x: 900, y: 900 }),
  outerPosition: vi.fn().mockResolvedValue({ x: 0, y: 0 }),
  innerSize: vi.fn().mockResolvedValue({ width: 430, height: 650 }),
}));

vi.mock("@tauri-apps/api/core", () => ({ invoke: nativeMocks.invoke }));
vi.mock("@tauri-apps/api/window", () => ({
  cursorPosition: nativeMocks.cursorPosition,
  getCurrentWindow: () => ({
    outerPosition: nativeMocks.outerPosition,
    innerSize: nativeMocks.innerSize,
  }),
}));

describe("desktop pointer presence", () => {
  const origin = { x: -430, y: 120 };
  const size = { width: 430, height: 650 };

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    Reflect.deleteProperty(window, "__TAURI_INTERNALS__");
  });

  it("tracks points inside a desktop window on negative-coordinate displays", () => {
    expect(isPointInsideWindow({ x: -430, y: 120 }, origin, size)).toBe(true);
    expect(isPointInsideWindow({ x: -1, y: 769 }, origin, size)).toBe(true);
  });

  it("excludes the right and bottom edges and points outside the window", () => {
    expect(isPointInsideWindow({ x: 0, y: 300 }, origin, size)).toBe(false);
    expect(isPointInsideWindow({ x: -200, y: 770 }, origin, size)).toBe(false);
    expect(isPointInsideWindow({ x: -431, y: 300 }, origin, size)).toBe(false);
  });

  it("keeps the native window interactive while a modal guard is active", () => {
    expect(shouldKeepDesktopInteraction(false, true)).toBe(true);
    expect(shouldKeepDesktopInteraction(true, false)).toBe(true);
    expect(shouldKeepDesktopInteraction(false, false)).toBe(false);
  });

  it("applies a guard that was acquired before the pointer hook mounted", async () => {
    Object.defineProperty(window, "__TAURI_INTERNALS__", {
      configurable: true,
      value: {},
    });
    const releaseFirst = acquireNativeInteractionGuard("skill-confirmation");
    const releaseSecond = acquireNativeInteractionGuard("skill-confirmation");

    const { result, unmount } = renderHook(() => useDesktopPointerPresence());

    await waitFor(() =>
      expect(nativeMocks.invoke).toHaveBeenCalledWith(
        "set_avatar_overlay_pointer_inside",
        { inside: true },
      ),
    );
    expect(result.current.pointerInside).toBe(true);

    releaseFirst();
    await Promise.resolve();
    expect(nativeMocks.invoke).toHaveBeenLastCalledWith(
      "set_avatar_overlay_pointer_inside",
      { inside: true },
    );

    releaseSecond();
    await waitFor(() =>
      expect(nativeMocks.invoke).toHaveBeenLastCalledWith(
        "set_avatar_overlay_pointer_inside",
        { inside: false },
      ),
    );
    unmount();
  });
});
