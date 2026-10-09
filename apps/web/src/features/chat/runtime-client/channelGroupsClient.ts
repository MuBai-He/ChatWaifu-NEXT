import {
  parseChannelConnectionSnapshot,
  parseChannelGroupAudienceRequest,
  parseChannelGroupRegistrationRequest,
  parseChannelGroupAudienceSnapshot,
  parseChannelGroupRouteCreate,
  parseChannelGroupRoutePage,
  parseChannelGroupRouteSnapshot,
  parseChannelGroupRouteUpdate,
  parseChannelGroupTurnCancelRequest,
  parseChannelGroupTurnPage,
  parseChannelGroupTurnSnapshot,
  parseChannelParticipantLinkCreate,
  parseChannelParticipantLinkPage,
  parseChannelParticipantLinkSnapshot,
  parseChannelParticipantLinkUpdate,
} from "@chatwaifu/protocol";
import { requestRuntime, runtimeParser } from "./http";
import type { RuntimeRequestContext } from "../runtimeEndpoint";
import { z } from "zod";

export type ChannelGroupsRequestOptions = {
  signal?: AbortSignal;
  expectedContext: RuntimeRequestContext;
};

export type ChannelGroupAudienceSnapshot = ReturnType<
  typeof parseChannelGroupAudienceSnapshot
>;
export type ChannelGroupRouteSnapshot = ReturnType<
  typeof parseChannelGroupRouteSnapshot
>;
export type ChannelGroupRoutePage = ReturnType<
  typeof parseChannelGroupRoutePage
>;
export type ChannelGroupTurnSnapshot = ReturnType<
  typeof parseChannelGroupTurnSnapshot
>;
export type ChannelGroupTurnPage = ReturnType<typeof parseChannelGroupTurnPage>;
export type ChannelParticipantLinkSnapshot = ReturnType<
  typeof parseChannelParticipantLinkSnapshot
>;
export type ChannelParticipantLinkPage = ReturnType<
  typeof parseChannelParticipantLinkPage
>;
export type ChannelGroupRouteCreate = ReturnType<
  typeof parseChannelGroupRouteCreate
>;
export type ChannelGroupRouteUpdate = ReturnType<
  typeof parseChannelGroupRouteUpdate
>;

function path(connectionId: string) {
  return `/v1/channel-connections/${encodeURIComponent(connectionId)}`;
}
function pageQuery(cursor?: string) {
  const query = new URLSearchParams({ limit: "50" });
  if (cursor) query.set("cursor", cursor);
  return query.toString();
}
export function getChannelGroupConnection(
  connectionId: string,
  options: ChannelGroupsRequestOptions,
) {
  return requestRuntime(
    path(connectionId),
    runtimeParser(parseChannelConnectionSnapshot),
    options,
  );
}
export async function getChannelGroupParticipants(
  options: ChannelGroupsRequestOptions,
) {
  const result = await requestRuntime(
    "/v1/participants",
    z.object({
      items: z.array(
        z.object({ participant_id: z.string(), display_name: z.string() }),
      ),
    }),
    options,
  );
  return result.items;
}
export function observeChannelGroupAudience(
  connectionId: string,
  groupId: string,
  options: ChannelGroupsRequestOptions,
) {
  const body = parseChannelGroupAudienceRequest({ group_id: groupId });
  return requestRuntime(
    `${path(connectionId)}/group-audience-observations`,
    runtimeParser(parseChannelGroupAudienceSnapshot),
    {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs: 30_000,
      ...options,
    },
  );
}
export function registerChannelGroupAudience(
  connectionId: string,
  observationId: string,
  options: ChannelGroupsRequestOptions,
) {
  const body = parseChannelGroupRegistrationRequest({
    observation_id: observationId,
  });
  return requestRuntime(
    `${path(connectionId)}/group-participant-registrations`,
    runtimeParser(parseChannelGroupAudienceSnapshot),
    {
      method: "POST",
      body: JSON.stringify(body),
      timeoutMs: 35_000,
      ...options,
    },
  );
}
export function getChannelParticipantLinks(
  connectionId: string,
  options: ChannelGroupsRequestOptions,
  cursor?: string,
) {
  return requestRuntime(
    `${path(connectionId)}/participant-links?${pageQuery(cursor)}`,
    runtimeParser(parseChannelParticipantLinkPage),
    options,
  );
}
export function createChannelParticipantLink(
  connectionId: string,
  observationId: string,
  senderKey: string,
  participantId: string,
  options: ChannelGroupsRequestOptions,
) {
  const body = parseChannelParticipantLinkCreate({
    observation_id: observationId,
    sender_key: senderKey,
    participant_id: participantId,
  });
  return requestRuntime(
    `${path(connectionId)}/participant-links`,
    runtimeParser(parseChannelParticipantLinkSnapshot),
    { method: "POST", body: JSON.stringify(body), ...options },
  );
}
export function updateChannelParticipantLink(
  connectionId: string,
  linkId: string,
  enabled: boolean,
  expectedRevision: number,
  options: ChannelGroupsRequestOptions,
) {
  const body = parseChannelParticipantLinkUpdate({
    enabled,
    expected_revision: expectedRevision,
  });
  return requestRuntime(
    `${path(connectionId)}/participant-links/${encodeURIComponent(linkId)}`,
    runtimeParser(parseChannelParticipantLinkSnapshot),
    { method: "PUT", body: JSON.stringify(body), ...options },
  );
}
export function getChannelGroupRoutes(
  connectionId: string,
  options: ChannelGroupsRequestOptions,
  cursor?: string,
) {
  return requestRuntime(
    `${path(connectionId)}/group-routes?${pageQuery(cursor)}`,
    runtimeParser(parseChannelGroupRoutePage),
    options,
  );
}
export function createChannelGroupRoute(
  connectionId: string,
  input: ChannelGroupRouteCreate,
  options: ChannelGroupsRequestOptions,
) {
  const body = parseChannelGroupRouteCreate(input);
  return requestRuntime(
    `${path(connectionId)}/group-routes`,
    runtimeParser(parseChannelGroupRouteSnapshot),
    { method: "POST", body: JSON.stringify(body), ...options },
  );
}
export function updateChannelGroupRoute(
  connectionId: string,
  routeId: string,
  input: ChannelGroupRouteUpdate,
  options: ChannelGroupsRequestOptions,
) {
  const body = parseChannelGroupRouteUpdate(input);
  return requestRuntime(
    `${path(connectionId)}/group-routes/${encodeURIComponent(routeId)}`,
    runtimeParser(parseChannelGroupRouteSnapshot),
    { method: "PUT", body: JSON.stringify(body), ...options },
  );
}
export function getChannelGroupTurns(
  connectionId: string,
  routeId: string,
  options: ChannelGroupsRequestOptions,
  cursor?: string,
) {
  return requestRuntime(
    `${path(connectionId)}/group-routes/${encodeURIComponent(routeId)}/turns?${pageQuery(cursor)}`,
    runtimeParser(parseChannelGroupTurnPage),
    options,
  );
}
export function cancelChannelGroupTurn(
  connectionId: string,
  routeId: string,
  turnId: string,
  expectedRevision: number,
  options: ChannelGroupsRequestOptions,
) {
  const body = parseChannelGroupTurnCancelRequest({
    expected_revision: expectedRevision,
  });
  return requestRuntime(
    `${path(connectionId)}/group-routes/${encodeURIComponent(routeId)}/turns/${encodeURIComponent(turnId)}/cancel`,
    runtimeParser(parseChannelGroupTurnSnapshot),
    { method: "POST", body: JSON.stringify(body), ...options },
  );
}
