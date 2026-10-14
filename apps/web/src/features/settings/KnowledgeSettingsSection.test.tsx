import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { KnowledgeSettingsSection } from "./KnowledgeSettingsSection";
import {
  isDesktopHost,
  readRuntimeRequestContext,
} from "../chat/runtimeEndpoint";
import type { CommonSettingsContext } from "./SettingsRuntimeContext";

vi.mock("../chat/runtimeEndpoint", () => ({
  isDesktopHost: vi.fn(),
  readRuntimeRequestContext: vi.fn(),
}));
const context = {
  runtime: { connection: "connected" },
} as CommonSettingsContext;
beforeEach(() => {
  vi.mocked(isDesktopHost).mockReturnValue(false);
  vi.mocked(readRuntimeRequestContext).mockResolvedValue({
    revision: 1,
    connection: { baseUrl: "https://runtime.example", token: "PRIVATE-TOKEN" },
  });
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("keeps browser navigation in the same authenticated origin and tab", () => {
  render(<KnowledgeSettingsSection context={context} />);
  const link = screen.getByRole("link", { name: "打开知识图谱 →" });
  expect(link.getAttribute("href")).toBe("/knowledge");
  expect(link.getAttribute("target")).toBeNull();
});

it("shows a desktop URL without including credentials", async () => {
  vi.mocked(isDesktopHost).mockReturnValue(true);
  render(<KnowledgeSettingsSection context={context} />);
  const link = await screen.findByRole("link", {
    name: "在浏览器打开知识图谱 ↗",
  });
  expect(link.getAttribute("href")).toBe("https://runtime.example/knowledge");
  expect(document.body.textContent).not.toContain("PRIVATE-TOKEN");
  expect(link.getAttribute("rel")).toContain("noopener");
});

it("does not resolve desktop connection details while hidden", async () => {
  vi.mocked(isDesktopHost).mockReturnValue(true);
  render(<KnowledgeSettingsSection context={context} active={false} />);
  await waitFor(() => expect(readRuntimeRequestContext).not.toHaveBeenCalled());
});
