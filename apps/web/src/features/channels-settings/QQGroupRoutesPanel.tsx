import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { ChannelConnectionSnapshot } from "../chat/runtimeClient";
import type { Participant } from "../chat/conversationScope";
import {
  assertRuntimeRequestContext,
  getRuntimeContextRevision,
  readRuntimeRequestContext,
  subscribeRuntimeContext,
} from "../chat/runtimeEndpoint";
import {
  cancelChannelGroupTurn,
  createChannelGroupRoute,
  createChannelParticipantLink,
  getChannelGroupRoutes,
  getChannelGroupConnection,
  getChannelGroupParticipants,
  getChannelGroupTurns,
  getChannelParticipantLinks,
  observeChannelGroupAudience,
  updateChannelGroupRoute,
  updateChannelParticipantLink,
  type ChannelGroupAudienceSnapshot,
  type ChannelGroupRoutePage,
  type ChannelGroupRouteSnapshot,
  type ChannelGroupsRequestOptions,
  type ChannelGroupTurnPage,
  type ChannelGroupTurnSnapshot,
  type ChannelParticipantLinkSnapshot,
} from "../chat/runtime-client/channelGroupsClient";
import { isConflictError } from "../chat/runtime-client/realtimeClient";
import { StickerLibraryPanel } from "./StickerLibraryPanel";
import { GroupAutonomyPanel } from "./GroupAutonomyPanel";
import { SettingsToggle } from "../settings/SettingsPrimitives";
import { useScopeControls } from "../connection/clientControls";
import "./qq-group-routes-panel.css";

type Props = {
  connection: ChannelConnectionSnapshot;
  runtimeOnline: boolean;
  connectionVerified: boolean;
};
type Operation =
  "observe" | "link" | "create" | "update" | "history" | "cancel" | "page";
type Request = ChannelGroupsRequestOptions & { controller: AbortController };
const emptyRoutes: ChannelGroupRoutePage = {
  schema_version: "1.0",
  items: [],
  next_cursor: null,
};

export function QQGroupRoutesPanel(props: Props) {
  const epoch = useSyncExternalStore(
    subscribeRuntimeContext,
    getRuntimeContextRevision,
    getRuntimeContextRevision,
  );
  const config = props.connection.configuration;
  return (
    <QQGroupRoutesContent
      key={`${epoch}:${config.connection_id}:${config.account_key ?? ""}:${config.character_id}:${props.connection.revision}:${props.connection.status}:${props.connectionVerified}`}
      {...props}
    />
  );
}

function QQGroupRoutesContent({
  connection,
  runtimeOnline,
  connectionVerified,
}: Props) {
  const config = connection.configuration;
  const connectionId = config.connection_id;
  const [routes, setRoutes] = useState<ChannelGroupRoutePage>(emptyRoutes);
  const [links, setLinks] = useState<ChannelParticipantLinkSnapshot[]>([]);
  const [linkCursor, setLinkCursor] = useState<string | null>(null);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [fresh, setFresh] = useState(false);
  const [routesFresh, setRoutesFresh] = useState(false);
  const [readRevision, setReadRevision] = useState(0);
  const [lastOnline, setLastOnline] = useState(runtimeOnline);
  const [notice, setNotice] = useState<string | null>(null);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [showStickers, setShowStickers] = useState(false);
  const [groupId, setGroupId] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [creationObservation, setCreationObservation] =
    useState<ChannelGroupAudienceSnapshot | null>(null);
  const [creationSpeakers, setCreationSpeakers] = useState<string[]>([]);
  const [creationConfirmed, setCreationConfirmed] = useState(false);
  const [creationVoice, setCreationVoice] = useState(false);
  const [selected, setSelected] = useState<ChannelGroupRouteSnapshot | null>(
    null,
  );
  const [routeObservation, setRouteObservation] =
    useState<ChannelGroupAudienceSnapshot | null>(null);
  const [routeSpeakers, setRouteSpeakers] = useState<string[]>([]);
  const [routeConfirmed, setRouteConfirmed] = useState(false);
  const [routeVoice, setRouteVoice] = useState(false);
  const scopeControls = useScopeControls();
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<ChannelGroupTurnPage | null>(null);
  const [historyFresh, setHistoryFresh] = useState(false);
  const requestRef = useRef<Request | null>(null);
  const operationRef = useRef<Operation | null>(null);
  const creationFresh = useObservationFresh(creationObservation);
  const routeFresh = useObservationFresh(routeObservation);

  if (lastOnline !== runtimeOnline) {
    setLastOnline(runtimeOnline);
    setFresh(false);
    setRoutesFresh(false);
    setHistoryFresh(false);
    setCreationObservation(null);
    setRouteObservation(null);
    setCreationConfirmed(false);
    setRouteConfirmed(false);
    setOperation(null);
  }

  useEffect(() => {
    if (!runtimeOnline) return;
    const controller = new AbortController();
    operationRef.current = null;
    void (async () => {
      const context = await readRuntimeRequestContext();
      controller.signal.throwIfAborted();
      const request = {
        controller,
        expectedContext: context,
        signal: controller.signal,
      };
      requestRef.current = request;
      const [routeResult, linkResult, peopleResult, connectionResult] =
        await Promise.allSettled([
          getChannelGroupRoutes(connectionId, request),
          getChannelParticipantLinks(connectionId, request),
          getChannelGroupParticipants(request),
          getChannelGroupConnection(connectionId, request),
        ]);
      guard(request);
      if (routeResult.status === "fulfilled") {
        checkRoutes(routeResult.value, connectionId);
        setRoutes(routeResult.value);
        setRoutesFresh(true);
        setSelected((old) =>
          old
            ? ((routeResult.value.items ?? []).find(
                (r) => r.route_id === old.route_id,
              ) ?? null)
            : null,
        );
      }
      if (linkResult.status === "fulfilled") {
        checkLinks(linkResult.value.items ?? [], config.account_key);
        setLinks(linkResult.value.items ?? []);
        setLinkCursor(linkResult.value.next_cursor ?? null);
      }
      if (peopleResult.status === "fulfilled")
        setParticipants(peopleResult.value);
      const sameConnection =
        connectionResult.status === "fulfilled" &&
        connectionResult.value.configuration.connection_id === connectionId &&
        connectionResult.value.revision === connection.revision &&
        connectionResult.value.configuration.account_key ===
          config.account_key &&
        connectionResult.value.configuration.character_id ===
          config.character_id &&
        connectionResult.value.status === connection.status &&
        connectionResult.value.configuration.enabled === config.enabled;
      if (
        [routeResult, linkResult, peopleResult].every(
          (r) => r.status === "fulfilled",
        ) &&
        sameConnection
      )
        setFresh(true);
      else
        setNotice(
          "群管理状态未完整确认，请刷新后再修改；已读取的历史仍可查看。",
        );
    })().catch(() => {
      if (!controller.signal.aborted)
        setNotice("无法确认群管理状态，请刷新后再操作。");
    });
    return () => {
      controller.abort();
      if (requestRef.current?.controller === controller)
        requestRef.current = null;
    };
  }, [
    runtimeOnline,
    connectionId,
    config.account_key,
    config.character_id,
    config.enabled,
    connection.revision,
    connection.status,
    readRevision,
  ]);

  const reload = () => {
    requestRef.current?.controller.abort();
    operationRef.current = null;
    setOperation(null);
    setFresh(false);
    setRoutesFresh(false);
    setHistoryFresh(false);
    setCreationObservation(null);
    setRouteObservation(null);
    setCreationConfirmed(false);
    setRouteConfirmed(false);
    setReadRevision((value) => value + 1);
  };
  const mutable =
    runtimeOnline &&
    connectionVerified &&
    fresh &&
    connection.status === "ready" &&
    config.enabled !== false &&
    Boolean(config.account_key);
  const busy = operation !== null;
  const readAvailable = runtimeOnline && routesFresh;
  const currentRoute =
    selected &&
    selected.account_key === config.account_key &&
    selected.character_id === config.character_id &&
    !selected.deleted_at;
  const run = async (
    kind: Operation,
    action: (request: Request) => Promise<void>,
    readOnly = false,
  ) => {
    const request = requestRef.current;
    if (
      !(readOnly ? readAvailable : mutable) ||
      operationRef.current ||
      !request ||
      request.signal?.aborted
    )
      return;
    operationRef.current = kind;
    setOperation(kind);
    setNotice(null);
    try {
      guard(request);
      await action(request);
    } catch (error: unknown) {
      if (request.signal?.aborted) return;
      setNotice(
        isConflictError(error)
          ? "版本或成员状态已变化，正在刷新；请核对后重新操作。"
          : "操作结果未确认，正在刷新；不会自动重试修改。",
      );
      reload();
    } finally {
      if (!request.signal?.aborted) {
        operationRef.current = null;
        setOperation(null);
      }
    }
  };
  const observe = (route?: ChannelGroupRouteSnapshot) => {
    const target = route?.group_id ?? groupId;
    if (!validQQId(target)) return;
    if (route) {
      setRouteObservation(null);
      setRouteConfirmed(false);
    } else {
      setCreationObservation(null);
      setCreationConfirmed(false);
    }
    void run("observe", async (request) => {
      const result = await observeChannelGroupAudience(
        connectionId,
        target,
        request,
      );
      guard(request);
      if (
        result.connection_id !== connectionId ||
        result.connection_revision !== connection.revision ||
        result.account_key !== config.account_key ||
        result.group_id !== target ||
        Date.parse(result.expires_at) <= Date.now()
      )
        throw new Error("observation mismatch");
      setMapping({});
      if (route) {
        setRouteObservation(result);
        setRouteSpeakers((speakers) =>
          speakers.filter((id) => result.member_ids.includes(id)),
        );
      } else {
        setCreationObservation(result);
        setCreationSpeakers([]);
      }
    });
  };
  const rememberLink = (result: ChannelParticipantLinkSnapshot) => {
    checkLinks([result], config.account_key);
    setLinks((old) => [
      ...old.filter((item) => item.link_id !== result.link_id),
      result,
    ]);
    setCreationConfirmed(false);
    setRouteConfirmed(false);
  };
  const linkMember = (
    observation: ChannelGroupAudienceSnapshot,
    sender: string,
  ) => {
    const participantId = mapping[sender];
    if (
      !participantId ||
      !participants.some((p) => p.participant_id === participantId) ||
      !observation.member_ids.includes(sender) ||
      !observationIsFresh(observation)
    )
      return;
    void run("link", async (request) => {
      const result = await createChannelParticipantLink(
        connectionId,
        observation.observation_id,
        sender,
        participantId,
        request,
      );
      guard(request);
      if (
        result.sender_key !== sender ||
        result.participant_id !== participantId
      )
        throw new Error("link mismatch");
      rememberLink(result);
    });
  };
  const toggleLink = (link: ChannelParticipantLinkSnapshot) => {
    void run("link", async (request) => {
      const result = await updateChannelParticipantLink(
        connectionId,
        link.link_id,
        !link.enabled,
        link.revision,
        request,
      );
      guard(request);
      if (
        result.link_id !== link.link_id ||
        result.sender_key !== link.sender_key ||
        result.participant_id !== link.participant_id
      )
        throw new Error("link mismatch");
      rememberLink(result);
      // Link revocation can pause several routes, including ones on another page.
      const resultRoutes = await getChannelGroupRoutes(connectionId, request);
      guard(request);
      checkRoutes(resultRoutes, connectionId);
      setRoutes(resultRoutes);
      setSelected((old) =>
        old
          ? ((resultRoutes.items ?? []).find(
              (r) => r.route_id === old.route_id,
            ) ?? null)
          : null,
      );
      setRouteObservation(null);
    });
  };
  const allLinked = (observation: ChannelGroupAudienceSnapshot | null) => {
    if (!observation || linkCursor) return false;
    const found = observation.member_ids.map((id) =>
      links.find(
        (link) =>
          link.sender_key === id &&
          link.enabled &&
          participants.some((p) => p.participant_id === link.participant_id),
      ),
    );
    return (
      found.every(Boolean) &&
      new Set(found.map((link) => link?.participant_id)).size === found.length
    );
  };
  const createRoute = () => {
    if (
      !creationObservation ||
      !creationFresh ||
      !creationConfirmed ||
      !allLinked(creationObservation) ||
      !displayName.trim() ||
      displayName.trim().length > 80
    )
      return;
    void run("create", async (request) => {
      const result = await createChannelGroupRoute(
        connectionId,
        {
          schema_version: "1.0",
          observation_id: creationObservation.observation_id,
          display_name: displayName.trim(),
          speaker_sender_keys: creationSpeakers,
          allow_requested_voice: creationVoice,
        },
        request,
      );
      guard(request);
      checkRoute(result, connectionId);
      if (
        result.enabled ||
        result.group_id !== creationObservation.group_id ||
        result.account_key !== config.account_key
      )
        throw new Error("created route mismatch");
      setRoutes((old) => ({
        ...old,
        items: [
          result,
          ...(old.items ?? []).filter((r) => r.route_id !== result.route_id),
        ].slice(0, 50),
      }));
      selectRoute(result);
      setCreationObservation(null);
      setCreationConfirmed(false);
      setNotice("已创建关闭的群路由。启用前请单独重新读取成员并确认共享范围。");
    });
  };
  const selectRoute = (route: ChannelGroupRouteSnapshot) => {
    setCreationObservation(null);
    setCreationConfirmed(false);
    setSelected(route);
    setRouteVoice(route.allow_requested_voice ?? false);
    setRouteSpeakers(
      route.members
        .filter((member) => member.can_speak)
        .map((member) => member.sender_key),
    );
    setRouteObservation(null);
    setRouteConfirmed(false);
    setHistory(null);
    setHistoryFresh(false);
  };
  const saveRoute = (enabled: boolean) => {
    if (
      !selected ||
      !currentRoute ||
      (enabled &&
        (!routeObservation ||
          !routeFresh ||
          !routeConfirmed ||
          !allLinked(routeObservation) ||
          !routeSpeakers.length))
    )
      return;
    const route = selected;
    void run("update", async (request) => {
      const result = await updateChannelGroupRoute(
        connectionId,
        route.route_id,
        {
          schema_version: "1.0",
          enabled,
          expected_revision: route.revision,
          observation_id: enabled ? routeObservation?.observation_id : null,
          speaker_sender_keys: routeSpeakers,
          allow_requested_voice: routeVoice,
        },
        request,
      );
      guard(request);
      checkRoute(result, connectionId);
      if (
        result.route_id !== route.route_id ||
        result.account_key !== config.account_key ||
        result.group_id !== route.group_id
      )
        throw new Error("route mismatch");
      setRoutes((old) => ({
        ...old,
        items: (old.items ?? []).map((r) =>
          r.route_id === result.route_id ? result : r,
        ),
      }));
      setSelected(result);
      setRouteVoice(result.allow_requested_voice ?? false);
      setRouteObservation(null);
      setRouteConfirmed(false);
      setNotice(
        enabled
          ? `群路由已启用；仅允许所选成员 @ 发起对话。${result.allow_requested_voice ? "当前明确要求时可回复语音。" : "只回复文字。"}`
          : "群路由已保存为关闭状态，历史和实际回执仍保留。",
      );
    });
  };
  const readHistory = (cursor?: string) => {
    if (!selected) return;
    const route = selected;
    setHistoryFresh(false);
    void run(
      "history",
      async (request) => {
        const result = await getChannelGroupTurns(
          connectionId,
          route.route_id,
          request,
          cursor,
        );
        guard(request);
        checkTurns(result, connectionId, route.route_id);
        setHistory(result);
        setHistoryFresh(true);
      },
      true,
    );
  };
  const cancelTurn = (item: ChannelGroupTurnSnapshot) => {
    if (!selected || !currentRoute || !historyFresh || !item.cancelable) return;
    const routeId = selected.route_id;
    void run("cancel", async (request) => {
      const result = await cancelChannelGroupTurn(
        connectionId,
        routeId,
        item.turn.channel_turn_id,
        item.turn.revision,
        request,
      );
      guard(request);
      checkTurns(
        { schema_version: "1.0", items: [result] },
        connectionId,
        routeId,
      );
      if (result.turn.channel_turn_id !== item.turn.channel_turn_id)
        throw new Error("turn mismatch");
      setHistory((old) =>
        old
          ? {
              ...old,
              items: (old.items ?? []).map((turn) =>
                turn.turn.channel_turn_id === result.turn.channel_turn_id
                  ? result
                  : turn,
              ),
            }
          : old,
      );
      setNotice("已读取取消结果；已确认的 QQ 投递事实不会撤回。");
    });
  };

  return (
    <section className="qq-group-routes-panel" aria-label="QQ 群路由">
      <h3>QQ 群 · 成员与回复权限</h3>
      <p>
        新路由默认关闭。仅注册成员的结构化 @
        触发回复；可单独开启本群静态表情学习。群聊默认文字；只有单独授权的群可按当前请求发语音，不支持主动消息或其他工具。主人私聊设置独立保留。
      </p>
      {scopeControls ? (
        <button
          type="button"
          className="qq-channel-secondary-action"
          onClick={(event) => scopeControls.open(event.currentTarget)}
        >
          管理对话参与者
        </button>
      ) : null}
      <p>
        成员须先在“对话设置 → 添加参与者”中注册。关联 QQ
        号时必须逐一选择；不会按昵称自动匹配。一个群内的不同成员须关联不同的
        participant。
      </p>
      <p className="qq-group-risk">
        群成员观测可能来自 NapCat 缓存，无法实时锁定受众。新加入的人可能在
        Runtime
        发现之前看到回复。全部群上下文都必须可以向该群共享，不能用于固定受众的秘密。
      </p>
      {!mutable ? (
        <p role="status">
          {!runtimeOnline
            ? "Runtime 离线，历史仅显示上次读取结果。"
            : "连接或管理状态尚未确认可修改；历史可独立读取。"}
        </p>
      ) : null}
      <button type="button" disabled={!runtimeOnline || busy} onClick={reload}>
        刷新群管理状态
      </button>
      {linkCursor ? (
        <div>
          <p>成员映射尚未完整读取，创建和启用暂不可用。</p>
          <button
            type="button"
            disabled={!readAvailable || busy}
            onClick={() =>
              void run(
                "page",
                async (request) => {
                  const page = await getChannelParticipantLinks(
                    connectionId,
                    request,
                    linkCursor,
                  );
                  guard(request);
                  checkLinks(page.items ?? [], config.account_key);
                  setLinks((old) => [
                    ...old,
                    ...(page.items ?? []).filter(
                      (item) => !old.some((l) => l.link_id === item.link_id),
                    ),
                  ]);
                  setLinkCursor(page.next_cursor ?? null);
                },
                true,
              )
            }
          >
            加载更多成员映射
          </button>
        </div>
      ) : null}
      <fieldset disabled={!mutable || busy} className="qq-group-fields">
        <legend>创建关闭的群路由</legend>
        <label>
          QQ 群号
          <input
            inputMode="numeric"
            value={groupId}
            onChange={(event) => {
              setGroupId(event.currentTarget.value);
              setCreationObservation(null);
              setCreationConfirmed(false);
            }}
          />
        </label>
        <label>
          群路由名称
          <input
            maxLength={80}
            value={displayName}
            onChange={(event) => setDisplayName(event.currentTarget.value)}
          />
        </label>
        <SettingsToggle
          label="新群允许按需语音"
          description="默认关闭；开启后仍只在当前授权发言者明确要求时允许语音。创建不会启用群回复。"
          checked={creationVoice}
          onChange={setCreationVoice}
        />
        <button
          type="button"
          disabled={!validQQId(groupId)}
          onClick={() => observe()}
        >
          读取该群当前成员
        </button>
        {creationObservation ? (
          <AudienceMapper
            observation={creationObservation}
            fresh={creationFresh}
            links={links}
            participants={participants}
            mapping={mapping}
            setMapping={setMapping}
            speakers={creationSpeakers}
            setSpeakers={(value) => {
              setCreationSpeakers(value);
              setCreationConfirmed(false);
            }}
            onLink={linkMember}
            onToggle={toggleLink}
          />
        ) : null}
        {creationObservation ? (
          <label className="qq-group-check">
            <input
              type="checkbox"
              checked={creationConfirmed}
              disabled={!creationFresh || !allLinked(creationObservation)}
              onChange={(event) =>
                setCreationConfirmed(event.currentTarget.checked)
              }
            />
            我已核对所有成员身份，并确认全部群上下文可以向该群共享
          </label>
        ) : null}
        <button
          type="button"
          disabled={
            !creationFresh ||
            !allLinked(creationObservation) ||
            !creationConfirmed ||
            !displayName.trim()
          }
          onClick={createRoute}
        >
          创建关闭的路由
        </button>
      </fieldset>
      <h4>已有群路由</h4>
      <ul className="qq-group-list">
        {(routes.items ?? []).map((route) => (
          <li key={route.route_id}>
            <strong>{route.display_name}</strong>
            <span>
              群 {route.group_id} · {route.enabled ? "已启用" : "已关闭"} ·{" "}
              {pauseLabel(route.pause_reason)}
            </span>
            <button
              type="button"
              disabled={!readAvailable || busy}
              onClick={() => {
                setShowStickers(false);
                selectRoute(route);
              }}
            >
              管理 {route.display_name}
            </button>
          </li>
        ))}
      </ul>
      {routesFresh && !(routes.items ?? []).length ? (
        <p>没有群路由；当前没有开启群回复。</p>
      ) : null}
      {routes.next_cursor ? (
        <button
          type="button"
          disabled={!readAvailable || busy}
          onClick={() =>
            void run(
              "page",
              async (request) => {
                const page = await getChannelGroupRoutes(
                  connectionId,
                  request,
                  routes.next_cursor ?? undefined,
                );
                guard(request);
                checkRoutes(page, connectionId);
                setRoutes(page);
                setSelected(null);
                setHistory(null);
              },
              true,
            )
          }
        >
          下一页群路由
        </button>
      ) : null}
      {selected ? (
        <div className="qq-group-editor">
          <h4>管理：{selected.display_name}</h4>
          <details>
            <summary>自主参与与预算</summary>
            <GroupAutonomyPanel
              routeId={selected.route_id}
              routeRevision={selected.revision}
            />
          </details>
          <p>
            群 {selected.group_id} · 版本 {selected.revision} ·{" "}
            {pauseLabel(selected.pause_reason)}
          </p>
          <p>场景由服务器创建。成员变更时需新场景；不能沿用旧受众的上下文。</p>
          <p>
            {selected.allow_requested_voice
              ? "本群已开启按需语音：平时文字，仅当前发言者明确要求时可发送语音。"
              : "本群按需语音关闭，只回复文字。"}
          </p>
          {config.character_id === "default" ? (
            <>
              <button
                type="button"
                disabled={!readAvailable || busy}
                onClick={() => setShowStickers((value) => !value)}
              >
                {showStickers ? "收起本群表情库" : "管理本群表情学习"}
              </button>
              {showStickers ? (
                <StickerLibraryPanel
                  characterId={config.character_id}
                  runtimeOnline={
                    runtimeOnline &&
                    readAvailable &&
                    connectionVerified &&
                    !busy
                  }
                  groupScope={{
                    routeId: selected.route_id,
                    sceneId: selected.scene_id,
                  }}
                />
              ) : null}
            </>
          ) : null}
          <fieldset
            disabled={!mutable || busy || !currentRoute}
            className="qq-group-fields"
          >
            <legend>发言权限与启用</legend>
            <SettingsToggle
              label="允许本群按当前请求发语音"
              description="平时回复文字。保存启用前仍需读取成员并确认；历史、引用和旁听内容不能授权语音。"
              checked={routeVoice}
              onChange={(value) => {
                setRouteVoice(value);
                setRouteConfirmed(false);
              }}
            />
            <button type="button" onClick={() => observe(selected)}>
              重新读取成员以启用
            </button>
            {routeObservation ? (
              <AudienceMapper
                observation={routeObservation}
                fresh={routeFresh}
                links={links}
                participants={participants}
                mapping={mapping}
                setMapping={setMapping}
                speakers={routeSpeakers}
                setSpeakers={(value) => {
                  setRouteSpeakers(value);
                  setRouteConfirmed(false);
                }}
                onLink={linkMember}
                onToggle={toggleLink}
              />
            ) : (
              <div>
                {selected.members.map((member) => (
                  <label className="qq-group-check" key={member.sender_key}>
                    <input
                      type="checkbox"
                      checked={routeSpeakers.includes(member.sender_key)}
                      onChange={(event) =>
                        setRouteSpeakers(
                          toggleValue(
                            routeSpeakers,
                            member.sender_key,
                            event.currentTarget.checked,
                          ),
                        )
                      }
                    />
                    允许 {participantLabel(participants, member.participant_id)}
                    （QQ {member.sender_key}）发言
                  </label>
                ))}
              </div>
            )}
            {routeObservation ? (
              <label className="qq-group-check">
                <input
                  type="checkbox"
                  checked={routeConfirmed}
                  disabled={!routeFresh || !allLinked(routeObservation)}
                  onChange={(event) =>
                    setRouteConfirmed(event.currentTarget.checked)
                  }
                />
                我已重新核对成员，确认该群共享范围与所选回复权限
              </label>
            ) : null}
            <button
              type="button"
              disabled={
                !routeFresh ||
                !allLinked(routeObservation) ||
                !routeConfirmed ||
                !routeSpeakers.length
              }
              onClick={() => saveRoute(true)}
            >
              确认启用群路由
            </button>
            <button type="button" onClick={() => saveRoute(false)}>
              保存权限并停用群路由
            </button>
          </fieldset>
          <button
            type="button"
            disabled={!readAvailable || busy}
            onClick={() => readHistory()}
          >
            读取群历史与回执
          </button>
          {history ? (
            <ul className="qq-group-list">
              {(history.items ?? []).map((item) => (
                <li key={item.turn.channel_turn_id}>
                  <strong>
                    {participantLabel(participants, item.participant_id)} ·{" "}
                    {turnLabel(item.turn.status)}
                  </strong>
                  <span>
                    {formatDate(item.turn.created_at)} · 投递{" "}
                    {deliveryLabel(item.turn.delivery_status)} ·{" "}
                    {item.provider_receipt_present
                      ? "QQ 回执已确认"
                      : "QQ 回执未确认"}
                  </span>
                  {item.turn.reply_text ? (
                    <p className="qq-group-reply">{item.turn.reply_text}</p>
                  ) : null}
                  <button
                    type="button"
                    disabled={
                      !mutable ||
                      !currentRoute ||
                      busy ||
                      !historyFresh ||
                      !item.cancelable
                    }
                    onClick={() => cancelTurn(item)}
                  >
                    取消此群请求
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          {historyFresh && !(history?.items ?? []).length ? (
            <p>此路由没有群请求记录。</p>
          ) : null}
          {history?.next_cursor ? (
            <button
              type="button"
              disabled={!readAvailable || busy}
              onClick={() => readHistory(history.next_cursor ?? undefined)}
            >
              更早的群历史
            </button>
          ) : null}
        </div>
      ) : null}
      {notice ? <p role="alert">{notice}</p> : null}
    </section>
  );
}

function AudienceMapper({
  observation,
  fresh,
  links,
  participants,
  mapping,
  setMapping,
  speakers,
  setSpeakers,
  onLink,
  onToggle,
}: {
  observation: ChannelGroupAudienceSnapshot;
  fresh: boolean;
  links: ChannelParticipantLinkSnapshot[];
  participants: Participant[];
  mapping: Record<string, string>;
  setMapping: (value: Record<string, string>) => void;
  speakers: string[];
  setSpeakers: (value: string[]) => void;
  onLink: (observation: ChannelGroupAudienceSnapshot, sender: string) => void;
  onToggle: (link: ChannelParticipantLinkSnapshot) => void;
}) {
  return (
    <div className="qq-group-audience">
      <p role="status">
        {fresh
          ? `观测有效至 ${formatDate(observation.expires_at)}，仍可能是缓存列表。`
          : "成员观测已过期，请重新读取；不会自动刷新或启用。"}
      </p>
      <ul className="qq-group-list">
        {observation.member_ids.map((sender) => {
          const link = links.find((item) => item.sender_key === sender);
          return (
            <li key={sender}>
              <strong>QQ {sender}</strong>
              {link ? (
                <>
                  <span>
                    已关联 {participantLabel(participants, link.participant_id)}{" "}
                    · {link.enabled ? "有效" : "已撤销"}（身份映射不可改）
                  </span>
                  <button
                    type="button"
                    disabled={!fresh}
                    onClick={() => onToggle(link)}
                  >
                    {link.enabled
                      ? `撤销 QQ ${sender} 的关联`
                      : `恢复 QQ ${sender} 的关联`}
                  </button>
                </>
              ) : (
                <>
                  <label>
                    QQ {sender} 对应的注册成员
                    <select
                      value={mapping[sender] ?? ""}
                      disabled={!fresh}
                      onChange={(event) =>
                        setMapping({
                          ...mapping,
                          [sender]: event.currentTarget.value,
                        })
                      }
                    >
                      <option value="">请显式选择，不按昵称匹配</option>
                      {participants.map((participant) => (
                        <option
                          key={participant.participant_id}
                          value={participant.participant_id}
                          disabled={observation.member_ids.some(
                            (id) =>
                              id !== sender &&
                              (links.find(
                                (l) => l.sender_key === id && l.enabled,
                              )?.participant_id ?? mapping[id]) ===
                                participant.participant_id,
                          )}
                        >
                          {participant.display_name}（
                          {participant.participant_id}）
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    disabled={!fresh || !mapping[sender]}
                    onClick={() => onLink(observation, sender)}
                  >
                    关联 QQ {sender}
                  </button>
                </>
              )}
              <label className="qq-group-check">
                <input
                  type="checkbox"
                  disabled={!fresh || !link?.enabled}
                  checked={speakers.includes(sender)}
                  onChange={(event) =>
                    setSpeakers(
                      toggleValue(
                        speakers,
                        sender,
                        event.currentTarget.checked,
                      ),
                    )
                  }
                />
                允许 QQ {sender} 发言
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
function guard(request: Request) {
  request.controller.signal.throwIfAborted();
  assertRuntimeRequestContext(request.expectedContext);
}
function checkRoute(route: ChannelGroupRouteSnapshot, connectionId: string) {
  if (route.connection_id !== connectionId)
    throw new Error("connection mismatch");
}
function checkRoutes(page: ChannelGroupRoutePage, connectionId: string) {
  for (const route of page.items ?? []) checkRoute(route, connectionId);
}
function checkLinks(
  links: ChannelParticipantLinkSnapshot[],
  account: string | null | undefined,
) {
  if (account && links.some((link) => link.account_key !== account))
    throw new Error("account mismatch");
}
function checkTurns(
  page: ChannelGroupTurnPage,
  connectionId: string,
  routeId: string,
) {
  if (
    (page.items ?? []).some(
      (item) =>
        item.route_id !== routeId || item.turn.connection_id !== connectionId,
    )
  )
    throw new Error("turn scope mismatch");
}
function validQQId(value: string) {
  return /^[1-9][0-9]{0,19}$/u.test(value);
}
function toggleValue(values: string[], id: string, enabled: boolean) {
  return enabled
    ? [...new Set([...values, id])]
    : values.filter((value) => value !== id);
}
function participantLabel(participants: Participant[], id: string) {
  return participants.find((p) => p.participant_id === id)?.display_name ?? id;
}
function observationIsFresh(value: ChannelGroupAudienceSnapshot) {
  return Date.parse(value.expires_at) > Date.now();
}
function useObservationFresh(value: ChannelGroupAudienceSnapshot | null) {
  const [, setRevision] = useState(0);
  useEffect(() => {
    if (!value) return;
    const timer = window.setTimeout(
      () => setRevision((n) => n + 1),
      Math.max(0, Date.parse(value.expires_at) - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [value]);
  return Boolean(value && observationIsFresh(value));
}
function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
function pauseLabel(value: ChannelGroupRouteSnapshot["pause_reason"]) {
  const labels = {
    operator_disabled: "管理员关闭",
    reconnect: "重连后需重新核对成员",
    membership_changed: "成员已变化，需新场景",
    account_changed: "QQ 账号已变化",
    connection_disabled: "连接已停用",
    connection_deleted: "连接已删除",
    configuration_changed: "连接配置已变化",
    link_revoked: "成员关联已撤销",
    scene_reset: "场景已重置",
    route_deleted: "路由已删除",
  };
  return value ? labels[value] : "没有暂停原因";
}
function turnLabel(value: ChannelGroupTurnSnapshot["turn"]["status"]) {
  return {
    accepted: "已接收",
    cancelling: "正在取消",
    timed_out: "超时",
    processing: "处理中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  }[value];
}
function deliveryLabel(
  value: ChannelGroupTurnSnapshot["turn"]["delivery_status"],
) {
  return value
    ? {
        pending: "待投递",
        sending: "投递中",
        delivered: "已投递",
        failed: "失败",
        cancelled: "已取消",
      }[value]
    : "未计划";
}
