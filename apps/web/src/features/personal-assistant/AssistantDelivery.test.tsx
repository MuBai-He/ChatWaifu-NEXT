/* eslint-disable @typescript-eslint/require-await -- async transport mocks intentionally settle immediately */
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readConversationScope } from "../chat/conversationScope";
import { AssistantDelivery } from "./AssistantDelivery";
import { deviceCall, organizerRequest } from "./organizer";

vi.mock("../chat/runtimeEndpoint", () => ({
  isDesktopHost: () => true,
  resolveRuntimeConnection: async () => ({ baseUrl: "https://server.example" }),
}));
vi.mock("../chat/conversationScope", () => ({
  readConversationScope: vi.fn(async () => ({
    participant_id: "local",
    scene_id: null,
  })),
}));
vi.mock("./organizer", () => ({
  deviceCall: vi.fn(),
  organizerRequest: vi.fn(),
}));
const delivery = {
  delivery_id: "a".repeat(36),
  kind: "alarm",
  title: "喝水",
  expires: 120,
};
let deliveries: (typeof delivery)[];
describe("desktop reminder delivery ownership", () => {
  beforeEach(() => {
    vi.mocked(readConversationScope).mockResolvedValue({
      participant_id: "local",
      scene_id: null,
    });
    vi.useFakeTimers();
    vi.setSystemTime(0);
    deliveries = [delivery];
    vi.mocked(deviceCall).mockImplementation(async (_server, action) => {
      if (action === "load")
        return {
          device_id: "device",
          secret: "secret",
          sources: [],
          results: {},
        };
      if (action === "present") return { first: true };
      return {};
    });
    vi.mocked(organizerRequest).mockImplementation(async (path) =>
      path === "/devices/poll" ? { deliveries, operations: [] } : {},
    );
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.clearAllMocks();
  });
  it("never polls private device data from a shared scene", async () => {
    vi.mocked(readConversationScope).mockResolvedValue({
      participant_id: "local",
      scene_id: "shared",
    });
    render(<AssistantDelivery />);
    await act(() => vi.advanceTimersByTimeAsync(6100));
    expect(deviceCall).not.toHaveBeenCalled();
    expect(organizerRequest).not.toHaveBeenCalled();
  });
  it("stops ringing immediately when the user closes, even if acknowledgement fails", async () => {
    render(<AssistantDelivery />);
    await act(() => vi.advanceTimersByTimeAsync(3100));
    expect(screen.getByText("喝水")).toBeTruthy();
    expect(vi.mocked(deviceCall).mock.calls.some((c) => c[1] === "sound")).toBe(
      true,
    );
    vi.mocked(organizerRequest).mockImplementation(async (path) => {
      if (path === "/devices/ack") throw new Error("offline");
      return { deliveries, operations: [] };
    });
    fireEvent.click(screen.getByText("关闭"));
    await act(() => vi.advanceTimersByTimeAsync(0));
    vi.mocked(deviceCall).mockClear();
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(vi.mocked(deviceCall).mock.calls.some((c) => c[1] === "sound")).toBe(
      false,
    );
  });
  it("does not replay a persisted presentation after remount", async () => {
    vi.mocked(deviceCall).mockImplementation(async (_server, action) =>
      action === "load"
        ? {
            device_id: "device",
            secret: "secret",
            sources: [],
            results: {},
            presentation_receipts: [delivery.delivery_id],
          }
        : { first: false },
    );
    render(<AssistantDelivery />);
    await act(() => vi.advanceTimersByTimeAsync(6100));
    expect(screen.getByText("喝水")).toBeTruthy();
    expect(
      vi
        .mocked(deviceCall)
        .mock.calls.some((c) => ["sound", "notify"].includes(c[1])),
    ).toBe(false);
    expect(
      vi
        .mocked(organizerRequest)
        .mock.calls.some(
          (c) =>
            c[0] === "/devices/ack" &&
            (c[1] as { action?: string } | undefined)?.action === "presented",
        ),
    ).toBe(true);
    expect(
      vi
        .mocked(deviceCall)
        .mock.calls.some((c) => c[1] === "forget_presentation"),
    ).toBe(true);
  });
  it("clears active sound on server cancellation and cleans up polling on unmount", async () => {
    const view = render(<AssistantDelivery />);
    await act(() => vi.advanceTimersByTimeAsync(3100));
    deliveries = [];
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(screen.queryByText("喝水")).toBeNull();
    view.unmount();
    vi.mocked(deviceCall).mockClear();
    await act(() => vi.advanceTimersByTimeAsync(10000));
    expect(deviceCall).not.toHaveBeenCalled();
  });
});
