import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  parseChannelGroupAudienceSnapshot,
  parseChannelGroupRouteSnapshot,
  parseChannelGroupTurnSnapshot,
  parseChannelParticipantLinkSnapshot,
} from "@chatwaifu/protocol";
import * as client from "../chat/runtime-client/channelGroupsClient";
import { RuntimeRequestError } from "../chat/runtime-client/http";
import { setRemoteRuntimeConnection } from "../chat/runtimeEndpoint";
import type { ChannelConnectionSnapshot } from "../chat/runtimeClient";
import { QQGroupRoutesPanel } from "./QQGroupRoutesPanel";
import { QQChannelPanel } from "./QQChannelPanel";
import * as runtimeClient from "../chat/runtimeClient";

vi.mock("./StickerLibraryPanel", () => ({ StickerLibraryPanel: () => null }));

vi.mock("../chat/runtime-client/channelGroupsClient", () => ({
  getChannelGroupConnection: vi.fn(),
  getChannelGroupParticipants: vi.fn(),
  getChannelGroupRoutes: vi.fn(),
  getChannelParticipantLinks: vi.fn(),
  observeChannelGroupAudience: vi.fn(),
  createChannelParticipantLink: vi.fn(),
  updateChannelParticipantLink: vi.fn(),
  createChannelGroupRoute: vi.fn(),
  updateChannelGroupRoute: vi.fn(),
  getChannelGroupTurns: vi.fn(),
  cancelChannelGroupTurn: vi.fn(),
}));
vi.mock("../chat/runtimeClient", () => ({
  getChannelConnections: vi.fn(),
  deleteChannelConnection: vi.fn(),
  updateChannelConnection: vi.fn(),
}));
const id = "00000000-0000-4000-8000-000000000001";
const routeId = "00000000-0000-4000-8000-000000000002";
const freshObservationId = "00000000-0000-4000-8000-000000000003";
const now = () => new Date().toISOString();
function connection(): ChannelConnectionSnapshot {
  return {
    configuration: {
      connection_id: id,
      provider_id: "qq_napcat",
      name: "QQ",
      character_id: "default",
      principal_scope: "local",
      account_key: "900",
      allowed_sender_keys: ["100"],
      enabled: true,
    },
    revision: 1,
    created_at: now(),
    updated_at: now(),
    status: "ready",
  };
}
const props = {
  connection: connection(),
  runtimeOnline: true,
  connectionVerified: true,
};
function audience(observationId = id) {
  return parseChannelGroupAudienceSnapshot({
    observation_id: observationId,
    connection_id: id,
    connection_revision: 1,
    account_key: "900",
    group_id: "123",
    member_ids: ["100", "200"],
    member_fingerprint: "a".repeat(64),
    observed_at: now(),
    expires_at: new Date(Date.now() + 45_000).toISOString(),
  });
}
function link(sender = "100") {
  return parseChannelParticipantLinkSnapshot({
    link_id: sender === "100" ? id : routeId,
    account_key: "900",
    sender_key: sender,
    participant_id: sender === "100" ? "alice" : "bob",
    enabled: true,
    revision: 2,
    created_at: now(),
    updated_at: now(),
  });
}
function route() {
  return parseChannelGroupRouteSnapshot({
    route_id: routeId,
    connection_id: id,
    account_key: "900",
    group_id: "123",
    character_id: "default",
    scene_id: "scene-new",
    display_name: "朋友群",
    revision: 1,
    enabled: false,
    pause_reason: "operator_disabled",
    observation_id: id,
    audience_fingerprint: "a".repeat(64),
    members: [link("100"), link("200")].map((value) => ({
      link_id: value.link_id,
      sender_key: value.sender_key,
      participant_id: value.participant_id,
      can_speak: value.sender_key === "100",
    })),
    created_at: now(),
    updated_at: now(),
  });
}
function turn() {
  return parseChannelGroupTurnSnapshot({
    route_id: routeId,
    route_revision: 1,
    scene_id: "scene-new",
    participant_id: "alice",
    cancelable: true,
    provider_receipt_present: true,
    turn: {
      channel_turn_id: id,
      connection_id: id,
      external_message_id: "group:123:message",
      sender_key: "100",
      conversation_key: "123",
      principal_scope: "scene:scene-new",
      session_id: id,
      turn_id: id,
      generation_id: id,
      status: "processing",
      delivery_status: "sending",
      revision: 3,
      reply_text: "已确认回复",
      created_at: now(),
      updated_at: now(),
    },
  });
}
beforeEach(() => {
  vi.mocked(runtimeClient.getChannelConnections).mockResolvedValue([
    connection(),
  ]);
  vi.mocked(client.getChannelGroupConnection).mockResolvedValue(connection());
  vi.mocked(client.getChannelGroupParticipants).mockResolvedValue([
    { participant_id: "alice", display_name: "Alice" },
    { participant_id: "bob", display_name: "Bob" },
  ]);
  vi.mocked(client.getChannelGroupRoutes).mockResolvedValue({
    schema_version: "1.0",
    items: [],
    next_cursor: null,
  });
  vi.mocked(client.getChannelParticipantLinks).mockResolvedValue({
    schema_version: "1.0",
    items: [],
    next_cursor: null,
  });
  vi.mocked(client.observeChannelGroupAudience).mockImplementation(() =>
    Promise.resolve(audience()),
  );
  vi.mocked(client.createChannelParticipantLink).mockImplementation(
    (_id, _observation, sender) => Promise.resolve(link(sender)),
  );
  vi.mocked(client.createChannelGroupRoute).mockResolvedValue(route());
  vi.mocked(client.updateChannelGroupRoute).mockImplementation(
    (_id, _route, body) =>
      Promise.resolve({
        ...route(),
        revision: body.expected_revision + 1,
        enabled: body.enabled,
        pause_reason: body.enabled ? null : "operator_disabled",
      }),
  );
  vi.mocked(client.updateChannelParticipantLink).mockResolvedValue({
    ...link(),
    enabled: false,
    revision: 3,
  });
  vi.mocked(client.getChannelGroupTurns).mockResolvedValue({
    schema_version: "1.0",
    items: [turn()],
    next_cursor: null,
  });
  vi.mocked(client.cancelChannelGroupTurn).mockResolvedValue({
    ...turn(),
    cancelable: false,
    turn: { ...turn().turn, status: "cancelled", revision: 4 },
  });
});
afterEach(() => {
  cleanup();
  setRemoteRuntimeConnection(null);
  vi.restoreAllMocks();
  vi.resetAllMocks();
});
async function ready() {
  await waitFor(() =>
    expect(
      screen.getByLabelText<HTMLInputElement>("QQ 群号").matches(":disabled"),
    ).toBe(false),
  );
}
async function observeCreation() {
  await ready();
  fireEvent.change(screen.getByLabelText("QQ 群号"), {
    target: { value: "123" },
  });
  fireEvent.change(screen.getByLabelText("群路由名称"), {
    target: { value: "朋友群" },
  });
  fireEvent.click(screen.getByRole("button", { name: "读取该群当前成员" }));
  await screen.findByText("QQ 100");
}
async function selectExisting() {
  vi.mocked(client.getChannelGroupRoutes).mockResolvedValue({
    schema_version: "1.0",
    items: [route()],
  });
  render(<QQGroupRoutesPanel {...props} />);
  fireEvent.click(await screen.findByRole("button", { name: "管理 朋友群" }));
}

describe("QQ group operator management", () => {
  it("requires fresh audience confirmation for the selected requested-voice setting", async () => {
    vi.mocked(client.getChannelParticipantLinks).mockResolvedValue({
      schema_version: "1.0",
      items: [link("100"), link("200")],
    });
    await selectExisting();
    await screen.findByRole("button", { name: "重新读取成员以启用" });
    const voice = screen.getByRole<HTMLInputElement>("switch", {
      name: "允许本群按当前请求发语音",
    });
    expect(voice.checked).toBe(false);
    fireEvent.click(voice);
    expect(client.updateChannelGroupRoute).not.toHaveBeenCalled();
    vi.mocked(client.observeChannelGroupAudience).mockResolvedValue(
      audience(freshObservationId),
    );
    fireEvent.click(screen.getByRole("button", { name: "重新读取成员以启用" }));
    const confirmation = await screen.findByLabelText<HTMLInputElement>(
      "我已重新核对成员，确认该群共享范围与所选回复权限",
    );
    fireEvent.click(confirmation);
    fireEvent.click(voice);
    expect(confirmation.checked).toBe(false);
    expect(
      screen
        .getByRole<HTMLButtonElement>("button", { name: "确认启用群路由" })
        .matches(":disabled"),
    ).toBe(true);
    fireEvent.click(voice);
    fireEvent.click(confirmation);
    fireEvent.click(screen.getByRole("button", { name: "确认启用群路由" }));
    await waitFor(() =>
      expect(client.updateChannelGroupRoute).toHaveBeenCalledOnce(),
    );
    expect(
      vi.mocked(client.updateChannelGroupRoute).mock.calls[0]?.[2],
    ).toMatchObject({
      allow_requested_voice: true,
      enabled: true,
      observation_id: freshObservationId,
      expected_revision: 1,
    });
  });
  it("opens group management explicitly without changing owner private settings", async () => {
    render(<QQChannelPanel characterId="default" runtimeOnline />);
    const button = await screen.findByRole("button", {
      name: "管理 QQ 群路由",
    });
    expect(client.getChannelGroupRoutes).not.toHaveBeenCalled();
    fireEvent.click(button);
    await ready();
    expect(screen.getByRole("region", { name: "QQ 群路由" })).toBeTruthy();
    expect(
      screen.getByRole<HTMLInputElement>("switch", { name: "启用 QQ 连接" })
        .checked,
    ).toBe(true);
    expect(runtimeClient.updateChannelConnection).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "收起 QQ 群管理" }));
    expect(screen.queryByRole("region", { name: "QQ 群路由" })).toBeNull();
  });
  it("reads defaults without observing, linking, creating or enabling any group", async () => {
    render(<QQGroupRoutesPanel {...props} />);
    await ready();
    expect(screen.getByText(/新加入的人可能/u)).toBeTruthy();
    expect(screen.getByText(/不支持主动消息或其他工具/u)).toBeTruthy();
    expect(client.observeChannelGroupAudience).not.toHaveBeenCalled();
    expect(client.createChannelParticipantLink).not.toHaveBeenCalled();
    expect(client.createChannelGroupRoute).not.toHaveBeenCalled();
    expect(client.updateChannelGroupRoute).not.toHaveBeenCalled();
  });
  it("requires explicit identities, creates OFF, and independently observes before enable", async () => {
    render(<QQGroupRoutesPanel {...props} />);
    await observeCreation();
    const firstSelect =
      screen.getByLabelText<HTMLSelectElement>("QQ 100 对应的注册成员");
    expect(firstSelect.value).toBe("");
    expect(
      screen
        .getByRole<HTMLButtonElement>("button", { name: "创建关闭的路由" })
        .matches(":disabled"),
    ).toBe(true);
    fireEvent.change(firstSelect, { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: "关联 QQ 100" }));
    await screen.findByText(/已关联 Alice/u);
    fireEvent.change(screen.getByLabelText("QQ 200 对应的注册成员"), {
      target: { value: "bob" },
    });
    fireEvent.click(screen.getByRole("button", { name: "关联 QQ 200" }));
    await screen.findByText(/已关联 Bob/u);
    fireEvent.click(screen.getByLabelText("允许 QQ 100 发言"));
    fireEvent.click(
      screen.getByLabelText(
        "我已核对所有成员身份，并确认全部群上下文可以向该群共享",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "创建关闭的路由" }));
    await screen.findByText(/已创建关闭的群路由/u);
    expect(client.createChannelGroupRoute).toHaveBeenCalledWith(
      id,
      {
        schema_version: "1.0",
        observation_id: id,
        display_name: "朋友群",
        allow_requested_voice: false,
        speaker_sender_keys: ["100"],
      },
      expect.any(Object),
    );
    expect(client.updateChannelGroupRoute).not.toHaveBeenCalled();
    expect(
      screen
        .getByRole<HTMLButtonElement>("button", { name: "确认启用群路由" })
        .matches(":disabled"),
    ).toBe(true);
    vi.mocked(client.observeChannelGroupAudience).mockResolvedValueOnce(
      audience(freshObservationId),
    );
    fireEvent.click(screen.getByRole("button", { name: "重新读取成员以启用" }));
    await screen.findByLabelText(
      "我已重新核对成员，确认该群共享范围与所选回复权限",
    );
    fireEvent.click(
      screen.getByLabelText("我已重新核对成员，确认该群共享范围与所选回复权限"),
    );
    fireEvent.click(screen.getByRole("button", { name: "确认启用群路由" }));
    await screen.findByText(/群路由已启用/u);
    expect(client.updateChannelGroupRoute).toHaveBeenCalledWith(
      id,
      routeId,
      {
        schema_version: "1.0",
        enabled: true,
        allow_requested_voice: false,
        expected_revision: 1,
        observation_id: freshObservationId,
        speaker_sender_keys: ["100"],
      },
      expect.any(Object),
    );
    expect(client.observeChannelGroupAudience).toHaveBeenCalledTimes(2);
  });
  it("does not silently grant speaking permission from an existing identity mapping", async () => {
    vi.mocked(client.getChannelParticipantLinks).mockResolvedValue({
      schema_version: "1.0",
      items: [link(), link("200")],
    });
    render(<QQGroupRoutesPanel {...props} />);
    await observeCreation();
    expect(
      screen.getByLabelText<HTMLInputElement>("允许 QQ 100 发言").checked,
    ).toBe(false);
    expect(
      screen.getByLabelText<HTMLInputElement>("允许 QQ 200 发言").checked,
    ).toBe(false);
    expect(client.createChannelParticipantLink).not.toHaveBeenCalled();
  });
  it("invalidates an expired observation without automatic refresh or enable", async () => {
    vi.mocked(client.getChannelParticipantLinks).mockResolvedValue({
      schema_version: "1.0",
      items: [link(), link("200")],
    });
    render(<QQGroupRoutesPanel {...props} />);
    await observeCreation();
    fireEvent.click(
      screen.getByLabelText(
        "我已核对所有成员身份，并确认全部群上下文可以向该群共享",
      ),
    );
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 60_000);
    fireEvent.change(screen.getByLabelText("群路由名称"), {
      target: { value: "名称更新" },
    });
    expect(screen.getByText(/成员观测已过期/u)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "创建关闭的路由" }));
    expect(client.createChannelGroupRoute).not.toHaveBeenCalled();
    expect(client.observeChannelGroupAudience).toHaveBeenCalledOnce();
  });
  it("loads account-wide mappings completely before permitting creation", async () => {
    vi.mocked(client.getChannelParticipantLinks)
      .mockResolvedValueOnce({
        schema_version: "1.0",
        items: [link()],
        next_cursor: "more-links",
      })
      .mockResolvedValueOnce({
        schema_version: "1.0",
        items: [link("200")],
        next_cursor: null,
      });
    render(<QQGroupRoutesPanel {...props} />);
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "加载更多成员映射" }));
    await waitFor(() =>
      expect(screen.queryByText(/映射尚未完整读取/u)).toBeNull(),
    );
    expect(client.getChannelParticipantLinks).toHaveBeenLastCalledWith(
      id,
      expect.any(Object),
      "more-links",
    );
    await observeCreation();
    expect(screen.getByText(/已关联 Bob/u)).toBeTruthy();
  });
  it.each([409, 503])(
    "refreshes after update %s without retrying the mutation",
    async (status) => {
      await selectExisting();
      vi.mocked(client.updateChannelGroupRoute).mockRejectedValueOnce(
        new RuntimeRequestError("untrusted server detail", status),
      );
      fireEvent.click(
        screen.getByRole("button", { name: "保存权限并停用群路由" }),
      );
      await screen.findByText(
        status === 409 ? /版本或成员状态已变化/u : /操作结果未确认/u,
      );
      await waitFor(() =>
        expect(client.getChannelGroupRoutes).toHaveBeenCalledTimes(2),
      );
      expect(client.updateChannelGroupRoute).toHaveBeenCalledOnce();
      expect(screen.queryByText("untrusted server detail")).toBeNull();
    },
  );
  it("edits the speaker subset using current CAS while keeping the route OFF", async () => {
    await selectExisting();
    fireEvent.click(screen.getByLabelText("允许 Bob（QQ 200）发言"));
    fireEvent.click(
      screen.getByRole("button", { name: "保存权限并停用群路由" }),
    );
    await screen.findByText(/保存为关闭状态/u);
    expect(client.updateChannelGroupRoute).toHaveBeenCalledWith(
      id,
      routeId,
      {
        schema_version: "1.0",
        enabled: false,
        allow_requested_voice: false,
        expected_revision: 1,
        observation_id: null,
        speaker_sender_keys: ["100", "200"],
      },
      expect.any(Object),
    );
  });
  it("explains reconnect and membership pauses without silently resuming", async () => {
    vi.mocked(client.getChannelGroupRoutes).mockResolvedValue({
      schema_version: "1.0",
      items: [
        { ...route(), pause_reason: "membership_changed" },
        {
          ...route(),
          route_id: freshObservationId,
          display_name: "重连群",
          pause_reason: "reconnect",
        },
      ],
    });
    render(<QQGroupRoutesPanel {...props} />);
    await ready();
    expect(screen.getByText(/成员已变化，需新场景/u)).toBeTruthy();
    expect(screen.getByText(/重连后需重新核对成员/u)).toBeTruthy();
    expect(client.updateChannelGroupRoute).not.toHaveBeenCalled();
  });
  it("keeps unbound and disabled history readable while mutations stay disabled", async () => {
    const unbound = {
      ...connection(),
      status: "disabled" as const,
      configuration: {
        ...connection().configuration,
        enabled: false,
        account_key: null,
        allowed_sender_keys: [],
      },
    };
    vi.mocked(client.getChannelGroupConnection).mockResolvedValue(unbound);
    vi.mocked(client.getChannelGroupRoutes).mockResolvedValue({
      schema_version: "1.0",
      items: [route()],
    });
    render(<QQGroupRoutesPanel {...props} connection={unbound} />);
    fireEvent.click(await screen.findByRole("button", { name: "管理 朋友群" }));
    fireEvent.click(screen.getByRole("button", { name: "读取群历史与回执" }));
    await screen.findByText("QQ 回执已确认", { exact: false });
    expect(screen.getByText("已确认回复")).toBeTruthy();
    expect(
      screen
        .getByRole<HTMLButtonElement>("button", { name: "取消此群请求" })
        .matches(":disabled"),
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "取消此群请求" }));
    expect(client.cancelChannelGroupTurn).not.toHaveBeenCalled();
  });
  it("cancels with the durable turn revision and preserves the provider receipt", async () => {
    await selectExisting();
    fireEvent.click(screen.getByRole("button", { name: "读取群历史与回执" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "取消此群请求" }),
    );
    await screen.findByText(/已读取取消结果/u);
    expect(client.cancelChannelGroupTurn).toHaveBeenCalledWith(
      id,
      routeId,
      id,
      3,
      expect.any(Object),
    );
    expect(screen.getByText(/QQ 回执已确认/u)).toBeTruthy();
  });
  it("drops late observation on disconnect and requires a fresh read after reconnect", async () => {
    let finish!: (value: client.ChannelGroupAudienceSnapshot) => void;
    vi.mocked(client.observeChannelGroupAudience).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const view = render(<QQGroupRoutesPanel {...props} />);
    await ready();
    fireEvent.change(screen.getByLabelText("QQ 群号"), {
      target: { value: "123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "读取该群当前成员" }));
    view.rerender(<QQGroupRoutesPanel {...props} runtimeOnline={false} />);
    act(() => finish(audience()));
    expect(screen.queryByText("QQ 100")).toBeNull();
    view.rerender(<QQGroupRoutesPanel {...props} />);
    await ready();
    expect(screen.queryByText("QQ 100")).toBeNull();
    expect(client.observeChannelGroupAudience).toHaveBeenCalledOnce();
  });
  it("drops a late online response across Runtime server and account contexts", async () => {
    setRemoteRuntimeConnection({
      baseUrl: "https://first.example",
      token: "first-private",
    });
    let finish!: (value: client.ChannelGroupAudienceSnapshot) => void;
    vi.mocked(client.observeChannelGroupAudience).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<QQGroupRoutesPanel {...props} />);
    await ready();
    fireEvent.change(screen.getByLabelText("QQ 群号"), {
      target: { value: "123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "读取该群当前成员" }));
    act(() =>
      setRemoteRuntimeConnection({
        baseUrl: "https://second.example",
        token: "second-private",
      }),
    );
    act(() => finish(audience()));
    await ready();
    expect(screen.queryByText("QQ 100")).toBeNull();
    expect(screen.getByLabelText<HTMLInputElement>("QQ 群号").value).toBe("");
    expect(client.createChannelGroupRoute).not.toHaveBeenCalled();
  });
  it("rejects a fresh read that resolves a different connection revision", async () => {
    vi.mocked(client.getChannelGroupConnection).mockResolvedValue({
      ...connection(),
      revision: 2,
    });
    render(<QQGroupRoutesPanel {...props} />);
    await screen.findByText(/群管理状态未完整确认/u);
    expect(
      screen.getByLabelText<HTMLInputElement>("QQ 群号").matches(":disabled"),
    ).toBe(true);
    expect(client.observeChannelGroupAudience).not.toHaveBeenCalled();
  });
  it("drops a blocked observation when the QQ account or route revision changes", async () => {
    let finish!: (value: client.ChannelGroupAudienceSnapshot) => void;
    vi.mocked(client.observeChannelGroupAudience).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const view = render(<QQGroupRoutesPanel {...props} />);
    await ready();
    fireEvent.change(screen.getByLabelText("QQ 群号"), {
      target: { value: "123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "读取该群当前成员" }));
    const changed = {
      ...connection(),
      revision: 2,
      configuration: { ...connection().configuration, account_key: "901" },
    };
    vi.mocked(client.getChannelGroupConnection).mockResolvedValue(changed);
    view.rerender(<QQGroupRoutesPanel {...props} connection={changed} />);
    act(() => finish(audience()));
    await ready();
    expect(screen.queryByText("QQ 100")).toBeNull();
    expect(client.createChannelParticipantLink).not.toHaveBeenCalled();
    expect(client.createChannelGroupRoute).not.toHaveBeenCalled();
  });
  it("does not render another route's late or malformed history", async () => {
    vi.mocked(client.getChannelGroupTurns).mockResolvedValue({
      schema_version: "1.0",
      items: [{ ...turn(), route_id: freshObservationId }],
    });
    await selectExisting();
    fireEvent.click(screen.getByRole("button", { name: "读取群历史与回执" }));
    await screen.findByText(/操作结果未确认/u);
    expect(screen.queryByText("已确认回复")).toBeNull();
    expect(client.cancelChannelGroupTurn).not.toHaveBeenCalled();
  });
});
