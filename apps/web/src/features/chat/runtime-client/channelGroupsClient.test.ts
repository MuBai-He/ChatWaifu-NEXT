import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  readRuntimeRequestContext,
  setRemoteRuntimeConnection,
} from "../runtimeEndpoint";
import {
  cancelChannelGroupTurn,
  createChannelGroupRoute,
  createChannelParticipantLink,
  getChannelGroupRoutes,
  getChannelGroupTurns,
  getChannelParticipantLinks,
  observeChannelGroupAudience,
  registerChannelGroupAudience,
  updateChannelGroupRoute,
  updateChannelParticipantLink,
  type ChannelGroupsRequestOptions,
} from "./channelGroupsClient";

const id = "00000000-0000-4000-8000-000000000001";
const other = "00000000-0000-4000-8000-000000000002";
const now = "2026-10-04T00:00:00Z";
let options: ChannelGroupsRequestOptions;
beforeEach(async () => {
  setRemoteRuntimeConnection({
    baseUrl: "https://runtime.example",
    token: "private-test-token",
  });
  options = {
    expectedContext: await readRuntimeRequestContext(),
    signal: new AbortController().signal,
  };
});
afterEach(() => {
  setRemoteRuntimeConnection(null);
  vi.unstubAllGlobals();
});
function reply(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
  });
}
function fetchReply(payload: unknown) {
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockImplementation(() => Promise.resolve(reply(payload)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
function bodyOf(init?: RequestInit): unknown {
  if (typeof init?.body !== "string") throw new Error("expected JSON body");
  return JSON.parse(init.body) as unknown;
}
function route() {
  return {
    route_id: id,
    connection_id: id,
    account_key: "900",
    group_id: "123",
    character_id: "default",
    scene_id: "scene-a",
    display_name: "Test group",
    revision: 1,
    observation_id: id,
    audience_fingerprint: "a".repeat(64),
    members: ["100", "200"].map((sender, n) => ({
      link_id: n ? other : id,
      sender_key: sender,
      participant_id: `person-${n}`,
      can_speak: n === 0,
    })),
    created_at: now,
    updated_at: now,
  };
}
function link() {
  return {
    link_id: id,
    account_key: "900",
    sender_key: "100",
    participant_id: "alice",
    enabled: true,
    revision: 1,
    created_at: now,
    updated_at: now,
  };
}
function turn() {
  return {
    route_id: id,
    route_revision: 1,
    scene_id: "scene-a",
    participant_id: "alice",
    cancelable: true,
    turn: {
      channel_turn_id: other,
      connection_id: id,
      external_message_id: "group:123:1",
      sender_key: "100",
      conversation_key: "123",
      principal_scope: "scene:scene-a",
      session_id: id,
      turn_id: id,
      generation_id: id,
      status: "accepted",
      revision: 2,
      created_at: now,
      updated_at: now,
    },
  };
}

describe("group management client", () => {
  it("registers only through the explicit operator confirmation endpoint", async () => {
    const fetchMock = fetchReply({
      observation_id: id,
      connection_id: id,
      connection_revision: 1,
      account_key: "900",
      group_id: "123",
      member_ids: ["100", "200"],
      member_fingerprint: "a".repeat(64),
      observed_at: now,
      expires_at: "2026-10-04T00:00:30Z",
    });
    await registerChannelGroupAudience(id, other, options);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `https://runtime.example/v1/channel-connections/${id}/group-participant-registrations`,
    );
    expect(bodyOf(fetchMock.mock.calls[0]?.[1])).toEqual({
      schema_version: "1.0",
      observation_id: other,
    });
  });
  it("observes only the typed group identifier, with pinned credentials", async () => {
    const fetchMock = fetchReply({
      observation_id: id,
      connection_id: id,
      connection_revision: 1,
      account_key: "900",
      group_id: "123",
      member_ids: ["100", "200"],
      member_fingerprint: "a".repeat(64),
      observed_at: now,
      expires_at: "2026-10-04T00:00:30Z",
    });
    const value = await observeChannelGroupAudience(id, "123", options);
    expect(value.member_ids).toEqual(["100", "200"]);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `https://runtime.example/v1/channel-connections/${id}/group-audience-observations`,
    );
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(bodyOf(fetchMock.mock.calls[0]?.[1])).toEqual({
      schema_version: "1.0",
      group_id: "123",
    });
    expect(
      new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Authorization"),
    ).toBe("Bearer private-test-token");
  });
  it.each(["", "0", "0123", "123 ", "123/456"])(
    "does not request a malformed group ID %s",
    (group) => {
      const fetchMock = fetchReply({});
      expect(() => observeChannelGroupAudience(id, group, options)).toThrow();
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );
  it("uses opaque encoded cursors and recursively validates all page items", async () => {
    const fetchMock = fetchReply({
      items: [route()],
      next_cursor: "opaque+&/?",
    });
    const page = await getChannelGroupRoutes(
      "id/with?path",
      options,
      "opaque+&/?",
    );
    expect(page.items[0]?.enabled).toBe(false);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://runtime.example/v1/channel-connections/id%2Fwith%3Fpath/group-routes?limit=50&cursor=opaque%2B%26%2F%3F",
    );
    fetchMock.mockResolvedValueOnce(
      reply({ items: [{ ...route(), members: [] }] }),
    );
    await expect(getChannelGroupRoutes(id, options)).rejects.toThrow(
      "无效响应",
    );
  });
  it("maps a member explicitly and only toggles the immutable mapping with CAS", async () => {
    const fetchMock = fetchReply(link());
    await createChannelParticipantLink(id, other, "100", "alice", options);
    await updateChannelParticipantLink(id, id, false, 1, options);
    expect(bodyOf(fetchMock.mock.calls[0]?.[1])).toEqual({
      schema_version: "1.0",
      observation_id: other,
      sender_key: "100",
      participant_id: "alice",
    });
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBe("PUT");
    expect(bodyOf(fetchMock.mock.calls[1]?.[1])).toEqual({
      schema_version: "1.0",
      enabled: false,
      expected_revision: 1,
    });
    fetchMock.mockResolvedValueOnce(reply({ items: [link()] }));
    expect((await getChannelParticipantLinks(id, options)).items).toHaveLength(
      1,
    );
  });
  it("creates only OFF-capable DTOs and requires separate observation to enable", async () => {
    const fetchMock = fetchReply(route());
    await createChannelGroupRoute(
      id,
      {
        schema_version: "1.0",
        observation_id: other,
        display_name: "Test group",
        speaker_sender_keys: ["100"],
      },
      options,
    );
    await updateChannelGroupRoute(
      id,
      id,
      {
        schema_version: "1.0",
        enabled: true,
        expected_revision: 1,
        observation_id: id,
        speaker_sender_keys: ["100"],
      },
      options,
    );
    expect(bodyOf(fetchMock.mock.calls[0]?.[1])).toEqual({
      schema_version: "1.0",
      observation_id: other,
      display_name: "Test group",
      speaker_sender_keys: ["100"],
    });
    expect(bodyOf(fetchMock.mock.calls[1]?.[1])).toMatchObject({
      enabled: true,
      expected_revision: 1,
      observation_id: id,
    });
    expect(() =>
      updateChannelGroupRoute(
        id,
        id,
        {
          schema_version: "1.0",
          enabled: true,
          expected_revision: 2,
          speaker_sender_keys: ["100"],
        },
        options,
      ),
    ).toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("reads durable receipt history and cancels one request with its turn revision", async () => {
    const fetchMock = fetchReply({ items: [turn()] });
    const history = await getChannelGroupTurns(id, id, options, "prior-page");
    expect(history.items[0]?.provider_receipt_present).toBe(false);
    fetchMock.mockResolvedValueOnce(reply(turn()));
    await cancelChannelGroupTurn(id, id, other, 2, options);
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      `https://runtime.example/v1/channel-connections/${id}/group-routes/${id}/turns/${other}/cancel`,
    );
    expect(bodyOf(fetchMock.mock.calls[1]?.[1])).toEqual({
      schema_version: "1.0",
      expected_revision: 2,
    });
  });
  it("refuses mutation after an online server or credential switch", async () => {
    const fetchMock = fetchReply(route());
    setRemoteRuntimeConnection({
      baseUrl: "https://other.example",
      token: "other-private-token",
    });
    await expect(
      createChannelGroupRoute(
        id,
        {
          schema_version: "1.0",
          observation_id: id,
          display_name: "Test group",
          speaker_sender_keys: [],
        },
        options,
      ),
    ).rejects.toThrow("上下文已变化");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
