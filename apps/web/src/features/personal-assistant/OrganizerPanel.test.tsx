import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OrganizerPanel } from "./OrganizerPanel";
import { organizerRequest } from "./organizer";

vi.mock("../chat/runtimeEndpoint", () => ({
  isDesktopHost: () => false,
  resolveRuntimeConnection: () =>
    Promise.resolve({ baseUrl: "https://server.example" }),
}));
vi.mock("./organizer", () => ({
  organizerRequest: vi.fn(),
  deviceCall: vi.fn(),
  errorText: (e: unknown) => String(e),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it.each(["task", "apple"])(
  "reuses the %s request identity after a lost enqueue response",
  async (kind) => {
    let attempts = 0;
    vi.mocked(organizerRequest).mockImplementation((path) => {
      if (path.startsWith("/organizer"))
        return Promise.resolve({
          devices: [
            {
              device_id: "device",
              name: "Mac",
              last_seen: 0,
              sources: [
                {
                  id: "list",
                  title: "测试列表",
                  resource: "reminder",
                  writable: true,
                },
              ],
            },
          ],
          tasks: [],
          operations: [],
          scheduler_error: null,
        });
      if (path.startsWith("/destinations"))
        return Promise.resolve({ items: [] });
      if (++attempts === 1) return Promise.reject(new Error("response lost"));
      return Promise.resolve({});
    });
    render(<OrganizerPanel sessionId="owner" />);
    await screen.findByText("Mac · 离线");
    let button: HTMLElement;
    if (kind === "task") {
      fireEvent.change(screen.getByLabelText("提醒内容"), {
        target: { value: "喝水" },
      });
      fireEvent.change(screen.getByLabelText(/首次时间/), {
        target: { value: "2026-10-01T09:00" },
      });
      button = screen.getByRole("button", { name: "保存任务" });
    } else {
      fireEvent.click(screen.getByRole("button", { name: "日历与提醒" }));
      fireEvent.change(screen.getByLabelText("已允许的列表"), {
        target: { value: "reminder:list" },
      });
      fireEvent.change(screen.getByLabelText("新事项标题"), {
        target: { value: "买牛奶" },
      });
      button = screen.getByRole("button", { name: "创建事项" });
    }
    fireEvent.submit(button.closest("form")!);
    await screen.findByText("Error: response lost");
    fireEvent.submit(button.closest("form")!);
    await waitFor(() => expect(attempts).toBe(2));
    const payloads = vi
      .mocked(organizerRequest)
      .mock.calls.filter(
        ([path]) => path === "/tasks" || path === "/apple/operations",
      )
      .map(([, body]) => body as Record<string, { request_id: string }>);
    const key = kind === "task" ? "task" : "operation";
    expect(payloads[0][key].request_id).toBe(payloads[1][key].request_id);
  },
);
