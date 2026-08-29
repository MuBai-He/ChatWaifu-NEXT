import type { SkillRunSnapshot } from "@chatwaifu/protocol";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  NATIVE_INTERACTION_GUARD_NOTIFICATION,
  type NativeInteractionGuardNotification,
} from "../../nativeInteractionGuard";
import {
  decideSkillConfirmation,
  getSkillConfirmations,
  type SkillConfirmation,
} from "./runtimeClient";
import { SkillConfirmationPrompt } from "./SkillConfirmationPrompt";
import {
  RUNTIME_CONNECTION_NOTIFICATION,
  RUNTIME_EVENT_NOTIFICATION,
} from "./runtimeSocketClient";

vi.mock("./runtimeClient", () => ({
  decideSkillConfirmation: vi.fn(),
  getSkillConfirmations: vi.fn(),
}));

const readConfirmation: SkillConfirmation = {
  request_id: "00000000-0000-4000-8000-000000000021",
  skill_run_id: "00000000-0000-4000-8000-000000000022",
  skill_id: "web.search",
  capability: "search",
  permissions: ["network.read"],
  side_effect: "read",
  reason: "宁宁想联网查找你刚才问到的最新资料。",
  requested_at: "2026-08-29T00:00:00Z",
  expires_at: "2026-08-29T00:05:00Z",
  allowed_decisions: ["deny", "allow_session", "allow_always", "allow_once"],
  argument_preview: {
    text: '{\n  "query": "ChatWaifu 最新资料"\n}',
    truncated: false,
    redacted: false,
  },
};

describe("SkillConfirmationPrompt", () => {
  beforeEach(() => {
    vi.mocked(getSkillConfirmations).mockResolvedValue([]);
    vi.mocked(decideSkillConfirmation).mockResolvedValue(
      {} as SkillRunSnapshot,
    );
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows pending confirmation globally and submits a one-time grant", async () => {
    vi.mocked(getSkillConfirmations)
      .mockResolvedValueOnce([readConfirmation])
      .mockResolvedValue([]);

    render(<SkillConfirmationPrompt sessionId="session-1" />);

    expect(
      await screen.findByRole("alertdialog", { name: "需要你的确认" }),
    ).toBeTruthy();
    expect(screen.getByText("web.search.search")).toBeTruthy();
    expect(screen.getByText(readConfirmation.reason)).toBeTruthy();
    expect(screen.getByText(/ChatWaifu 最新资料/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "允许一次" }));

    await waitFor(() =>
      expect(decideSkillConfirmation).toHaveBeenCalledWith(
        readConfirmation.request_id,
        "allow_once",
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("alertdialog", { name: "需要你的确认" }),
      ).toBeNull(),
    );
  });

  it("labels redacted and truncated argument previews", async () => {
    vi.mocked(getSkillConfirmations).mockResolvedValueOnce([
      {
        ...readConfirmation,
        argument_preview: {
          text: '{\n  "query": "公开关键词",\n  "api_key": "[REDACTED]"\n}\n…',
          redacted: true,
          truncated: true,
        },
      },
    ]);

    render(<SkillConfirmationPrompt sessionId="session-1" />);

    expect(await screen.findByText("敏感字段已隐藏")).toBeTruthy();
    expect(screen.getByText("内容已截断")).toBeTruthy();
    expect(screen.getByText(/\[REDACTED\]/)).toBeTruthy();
  });

  it("does not offer persistent grants for dangerous operations", async () => {
    vi.mocked(getSkillConfirmations).mockResolvedValueOnce([
      {
        ...readConfirmation,
        request_id: "00000000-0000-4000-8000-000000000023",
        capability: "publish",
        side_effect: "external_communication",
        allowed_decisions: ["deny", "allow_once"],
      },
    ]);

    render(<SkillConfirmationPrompt sessionId="session-1" />);

    expect(await screen.findByText("对外通信")).toBeTruthy();
    expect(screen.getByRole("button", { name: "允许一次" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "拒绝" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "本会话允许" })).toBeNull();
    expect(screen.queryByRole("button", { name: "始终允许" })).toBeNull();
  });

  it("refreshes when Runtime announces a confirmation", async () => {
    vi.mocked(getSkillConfirmations)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([readConfirmation]);
    render(<SkillConfirmationPrompt sessionId="session-1" />);
    await waitFor(() => expect(getSkillConfirmations).toHaveBeenCalledOnce());

    window.dispatchEvent(
      new CustomEvent(RUNTIME_EVENT_NOTIFICATION, {
        detail: {
          event_type: "skill.confirmation_requested",
          session_id: "session-1",
        },
      }),
    );

    expect(
      await screen.findByRole("alertdialog", { name: "需要你的确认" }),
    ).toBeTruthy();
  });

  it("refreshes immediately when the Runtime event socket reconnects", async () => {
    vi.mocked(getSkillConfirmations)
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([readConfirmation]);
    render(<SkillConfirmationPrompt sessionId="session-1" />);
    await waitFor(() => expect(getSkillConfirmations).toHaveBeenCalledOnce());

    window.dispatchEvent(
      new CustomEvent(RUNTIME_CONNECTION_NOTIFICATION, {
        detail: { sessionId: "session-1", state: "connected" },
      }),
    );

    expect(
      await screen.findByRole("alertdialog", { name: "需要你的确认" }),
    ).toBeTruthy();
    expect(getSkillConfirmations).toHaveBeenCalledTimes(2);
  });

  it("clears the previous session prompt before loading a new session", async () => {
    vi.mocked(getSkillConfirmations).mockImplementation((sessionId) =>
      Promise.resolve(sessionId === "session-1" ? [readConfirmation] : []),
    );
    const { rerender } = render(
      <SkillConfirmationPrompt sessionId="session-1" />,
    );
    expect(
      await screen.findByRole("alertdialog", { name: "需要你的确认" }),
    ).toBeTruthy();

    rerender(<SkillConfirmationPrompt sessionId="session-2" />);

    expect(
      screen.queryByRole("alertdialog", { name: "需要你的确认" }),
    ).toBeNull();
    await waitFor(() =>
      expect(getSkillConfirmations).toHaveBeenCalledWith("session-2"),
    );
  });

  it("owns keyboard focus, traps Tab, and restores the composer focus", async () => {
    vi.mocked(getSkillConfirmations)
      .mockResolvedValueOnce([readConfirmation])
      .mockResolvedValue([]);
    const { container } = render(
      <>
        <input aria-label="underlying composer" />
        <SkillConfirmationPrompt sessionId="session-1" />
      </>,
    );
    const composer = screen.getByRole("textbox", {
      name: "underlying composer",
    });
    composer.focus();

    await screen.findByRole("alertdialog", { name: "需要你的确认" });
    const deny = screen.getByRole("button", { name: "拒绝" });
    const allowOnce = screen.getByRole("button", { name: "允许一次" });
    await waitFor(() => expect(document.activeElement).toBe(deny));
    expect(container.inert).toBe(true);

    fireEvent.keyDown(deny, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(allowOnce);
    fireEvent.keyDown(allowOnce, { key: "Tab" });
    expect(document.activeElement).toBe(deny);
    fireEvent.click(allowOnce);

    await waitFor(() =>
      expect(
        screen.queryByRole("alertdialog", { name: "需要你的确认" }),
      ).toBeNull(),
    );
    expect(container.inert).toBe(false);
    expect(document.activeElement).toBe(composer);
  });

  it("pins native desktop interaction while confirmation is visible", async () => {
    vi.mocked(getSkillConfirmations).mockResolvedValueOnce([readConfirmation]);
    const notifications: NativeInteractionGuardNotification[] = [];
    const listener = (rawEvent: Event) => {
      notifications.push(
        (rawEvent as CustomEvent<NativeInteractionGuardNotification>).detail,
      );
    };
    window.addEventListener(NATIVE_INTERACTION_GUARD_NOTIFICATION, listener);
    const { unmount } = render(
      <SkillConfirmationPrompt sessionId="session-1" />,
    );
    await screen.findByRole("alertdialog", { name: "需要你的确认" });
    await waitFor(() =>
      expect(notifications).toContainEqual({
        active: true,
        sources: ["skill-confirmation"],
      }),
    );

    unmount();
    expect(notifications.at(-1)).toEqual({
      active: false,
      sources: [],
    });
    window.removeEventListener(NATIVE_INTERACTION_GUARD_NOTIFICATION, listener);
  });

  it("ignores confirmation events from another session", async () => {
    vi.mocked(getSkillConfirmations).mockResolvedValue([]);
    render(<SkillConfirmationPrompt sessionId="session-1" />);
    await waitFor(() => expect(getSkillConfirmations).toHaveBeenCalledOnce());

    window.dispatchEvent(
      new CustomEvent(RUNTIME_EVENT_NOTIFICATION, {
        detail: {
          event_type: "skill.confirmation_requested",
          session_id: "session-2",
        },
      }),
    );

    await Promise.resolve();
    expect(getSkillConfirmations).toHaveBeenCalledOnce();
  });
});
