/* eslint-disable @typescript-eslint/require-await -- mocked transports settle immediately */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { requestRuntime } from "../chat/runtime-client/http";
import { organizerRequest } from "./organizer";
import { AgendaOverview } from "./AgendaOverview";

vi.mock("../chat/runtime-client/http", () => ({ requestRuntime: vi.fn() }));
vi.mock("./organizer", () => ({ organizerRequest: vi.fn() }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("reminder inbox", () => {
  it("distinguishes missed from unhandled and dismisses only the selected history row", async () => {
    vi.mocked(requestRuntime).mockImplementation(async (path) =>
      path.includes("/accounts") ? [] : { items: [] },
    );
    const history = [
      {
        delivery_id: "missed-id",
        task_id: "missed-task",
        due: 100,
        state: "missed",
        title: "离线闹钟",
        kind: "alarm",
      },
      {
        delivery_id: "unhandled-id",
        task_id: "unhandled-task",
        due: 200,
        state: "unhandled",
        title: "未处理的测试提醒",
        kind: "reminder",
      },
    ];
    vi.mocked(organizerRequest).mockImplementation(async (path) =>
      path.startsWith("/organizer/history/")
        ? { dismissed: true }
        : {
            devices: [],
            tasks: [],
            operations: [],
            scheduler_error: null,
            history,
          },
    );
    render(<AgendaOverview sessionId="owner-session" />);
    await waitFor(() => expect(screen.getByText("离线闹钟")).toBeTruthy());
    expect(screen.getByText(/设备未确认送达/)).toBeTruthy();
    expect(screen.getByText(/已展示，未处理/)).toBeTruthy();
    fireEvent.click(screen.getAllByText("知道了")[0]);
    await waitFor(() => expect(screen.queryByText("离线闹钟")).toBeNull());
    expect(screen.getByText("未处理的测试提醒")).toBeTruthy();
    expect(organizerRequest).toHaveBeenCalledWith(
      "/organizer/history/missed-id/dismiss",
      { session_id: "owner-session" },
    );
  });
});
