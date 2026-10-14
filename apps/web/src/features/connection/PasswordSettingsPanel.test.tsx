import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PasswordSettingsPanel } from "./PasswordSettingsPanel";
import {
  getPasswordStatus,
  replacePasswordSession,
  updatePassword,
} from "./runtimePassword";

const context = {
  revision: 1,
  connection: {
    baseUrl: "https://runtime.example",
    token: "original-access-token",
  },
};
vi.mock("../chat/runtimeEndpoint", () => ({
  readRuntimeRequestContext: vi.fn(() => Promise.resolve(context)),
}));
vi.mock("./runtimePassword", () => ({
  getPasswordStatus: vi.fn(),
  updatePassword: vi.fn(),
  replacePasswordSession: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getPasswordStatus).mockResolvedValue({
    schema_version: 1,
    configured: false,
    revision: 0,
  });
  vi.mocked(updatePassword).mockResolvedValue("new-exchanged-credential");
  vi.mocked(replacePasswordSession).mockResolvedValue();
});
afterEach(cleanup);

it("sets a password, confirms it, and replaces the credential without persisting plaintext", async () => {
  render(<PasswordSettingsPanel online />);
  await screen.findByText("尚未设置登录密码，请先设置一次。");
  fireEvent.change(screen.getByLabelText("新密码"), {
    target: { value: "test password 123" },
  });
  fireEvent.change(screen.getByLabelText("确认新密码"), {
    target: { value: "different password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "设置密码" }));
  expect(await screen.findByRole("status")).toHaveProperty(
    "textContent",
    "两次输入的新密码不一致。",
  );
  expect(updatePassword).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("确认新密码"), {
    target: { value: "test password 123" },
  });
  fireEvent.click(screen.getByRole("button", { name: "设置密码" }));
  await waitFor(() =>
    expect(replacePasswordSession).toHaveBeenCalledWith(
      context,
      "new-exchanged-credential",
    ),
  );
  expect(updatePassword).toHaveBeenCalledWith(
    context,
    {
      new_password: "test password 123",
      current_password: undefined,
      expected_revision: 0,
    },
    expect.any(AbortSignal),
  );
  expect(screen.getByLabelText("新密码")).toHaveProperty("value", "");
});

it("requires the current password and uses the loaded revision when changing it", async () => {
  vi.mocked(getPasswordStatus).mockResolvedValue({
    schema_version: 1,
    configured: true,
    revision: 7,
  });
  render(<PasswordSettingsPanel online />);
  await screen.findByLabelText("当前密码");
  for (const [label, value] of [
    ["当前密码", "old password 123"],
    ["新密码", "new password 123"],
    ["确认新密码", "new password 123"],
  ])
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "修改密码" }));
  await waitFor(() =>
    expect(updatePassword).toHaveBeenCalledWith(
      context,
      {
        new_password: "new password 123",
        current_password: "old password 123",
        expected_revision: 7,
      },
      expect.any(AbortSignal),
    ),
  );
});

it("clears password drafts and stops reads when the settings section is hidden", async () => {
  const view = render(<PasswordSettingsPanel online />);
  await screen.findByText("尚未设置登录密码，请先设置一次。");
  const signal = vi.mocked(getPasswordStatus).mock.calls[0][1];
  fireEvent.change(screen.getByLabelText("新密码"), {
    target: { value: "private password" },
  });
  view.rerender(<PasswordSettingsPanel online active={false} />);
  expect(screen.queryByLabelText("新密码")).toBeNull();
  expect(signal.aborted).toBe(true);
  expect(getPasswordStatus).toHaveBeenCalledTimes(1);
  view.rerender(<PasswordSettingsPanel online />);
  await screen.findByText("尚未设置登录密码，请先设置一次。");
  expect(screen.getByLabelText("新密码")).toHaveProperty("value", "");
});
