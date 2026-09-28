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
  it("keeps the delivery actionable when local journaling fails", async () => {
    vi.mocked(deviceCall).mockImplementation(async (_server, action) => {
      if (action === "load")
        return { device_id: "device", secret: "secret", sources: [], results: {} };
      if (action === "present") return { first: true };
      if (action === "queue_action") throw new Error("device_save_failed");
      return {};
    });
    render(<AssistantDelivery />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    fireEvent.click(screen.getByText("关闭"));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("喝水")).toBeTruthy();
    expect(screen.getByText(/本机未能保存这次操作/)).toBeTruthy();
    expect(screen.getByText("关闭").hasAttribute("disabled")).toBe(false);
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
  it("retries a locally saved snooze after reconnect without presenting it again", async () => {
    const actions: Record<string, { action: "snooze"; rejected: boolean }> = {};
    let snoozeAttempts = 0;
    vi.mocked(deviceCall).mockImplementation(async (_server, action, payload) => {
      if (action === "load")
        return {
          device_id: "device",
          secret: "secret",
          sources: [],
          results: {},
          actions: { ...actions },
        };
      if (action === "present") return { first: true };
      if (action === "queue_action") {
        actions[(payload as { id: string }).id] = {
          action: "snooze",
          rejected: false,
        };
      }
      if (action === "forget_action") delete actions[payload as string];
      return {};
    });
    vi.mocked(organizerRequest).mockImplementation(async (path, body) => {
      if (path === "/devices/poll")
        return { deliveries: snoozeAttempts > 1 ? [] : deliveries, operations: [] };
      if (
        path === "/devices/ack" &&
        (body as { action?: string })?.action === "snooze"
      ) {
        snoozeAttempts += 1;
        if (snoozeAttempts === 1) throw new Error("offline");
      }
      return {};
    });
    render(<AssistantDelivery />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    fireEvent.click(screen.getByText(/5 分钟后提醒/));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText(/操作已保存在本机/)).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(snoozeAttempts).toBe(2);
    expect(actions).toEqual({});
    expect(screen.queryByText(/操作已保存在本机/)).toBeNull();
    expect(
      vi.mocked(deviceCall).mock.calls.filter((call) => call[1] === "notify"),
    ).toHaveLength(1);
  });
  it("journals every simultaneous delivery before exposing either action", async () => {
    const second = { ...delivery, delivery_id: "b".repeat(36), title: "出门" };
    deliveries = [delivery, second];
    const presented = new Set<string>();
    let finishSecond!: (value: { first: boolean }) => void;
    vi.mocked(deviceCall).mockImplementation(async (_server, action, payload) => {
      if (action === "load")
        return { device_id: "device", secret: "secret", sources: [], results: {} };
      if (action === "present") {
        const id = (payload as { id: string }).id;
        if (id === second.delivery_id) {
          const value = await new Promise<{ first: boolean }>((resolve) => {
            finishSecond = resolve;
          });
          presented.add(id);
          return value;
        }
        presented.add(id);
        return { first: true };
      }
      if (action === "queue_action" && !presented.has((payload as { id: string }).id))
        throw new Error("delivery_not_presented");
      return {};
    });
    render(<AssistantDelivery />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(presented.has(delivery.delivery_id)).toBe(true);
    expect(screen.queryByText("喝水")).toBeNull();
    expect(screen.queryByText("出门")).toBeNull();
    await act(async () => finishSecond({ first: true }));
    expect(screen.getByText("出门")).toBeTruthy();
    fireEvent.click(screen.getAllByText("关闭")[1]);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(
      vi.mocked(deviceCall).mock.calls.some(
        (call) => call[1] === "queue_action" &&
          (call[2] as { id: string }).id === second.delivery_id,
      ),
    ).toBe(true);
    expect(screen.queryByText(/本机未能保存/)).toBeNull();
  });
  it("replays a persisted action after remount but reports an expired snooze", async () => {
    let rejected = false;
    vi.mocked(deviceCall).mockImplementation(async (_server, action) => {
      if (action === "load")
        return {
          device_id: "device",
          secret: "secret",
          sources: [],
          results: {},
          actions: {
            [delivery.delivery_id]: { action: "snooze", rejected },
          },
        };
      if (action === "reject_action") rejected = true;
      if (action === "present") return { first: false };
      return {};
    });
    vi.mocked(organizerRequest).mockImplementation(async (path, body) => {
      if (
        path === "/devices/ack" &&
        (body as { action?: string })?.action === "snooze"
      )
        throw new Error("delivery_not_active");
      return path === "/devices/poll"
        ? { deliveries, operations: [] }
        : {};
    });
    render(<AssistantDelivery />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(rejected).toBe(true);
    expect(screen.getByText(/贪睡未生效/)).toBeTruthy();
    expect(
      vi
        .mocked(deviceCall)
        .mock.calls.some((call) => ["sound", "notify"].includes(call[1])),
    ).toBe(false);
  });
  it("stops polling a revoked device while allowing a new pairing to recover", async () => {
    let deviceId = "revoked-device";
    let polls = 0;
    vi.mocked(deviceCall).mockImplementation(async (_server, action) =>
      action === "load"
        ? { device_id: deviceId, secret: "secret", sources: [], results: {} }
        : {},
    );
    vi.mocked(organizerRequest).mockImplementation(async (path) => {
      if (path === "/devices/poll") {
        polls += 1;
        if (deviceId === "revoked-device")
          throw new Error("device_not_authorized");
        return { deliveries: [], operations: [] };
      }
      return {};
    });
    render(<AssistantDelivery />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText(/配对已撤销/)).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(polls).toBe(1);
    deviceId = "new-device";
    await act(() => vi.advanceTimersByTimeAsync(30000));
    expect(polls).toBe(2);
    expect(screen.queryByText(/配对已撤销/)).toBeNull();
  });
});
