import { useEffect, useState, useSyncExternalStore } from "react";
import type { CharacterProfile } from "../chat/types";
import {
  getRuntimeContextRevision,
  readRuntimeRequestContext,
  subscribeRuntimeContext,
} from "../chat/runtimeEndpoint";
import { useConnectionControls } from "../connection/clientControls";
import { SettingsSectionIntro } from "../settings/SettingsPrimitives";
import { ChannelSettingsDisclosure } from "./ChannelSettingsPrimitives";
import { StickerLibraryPanel } from "./StickerLibraryPanel";
import { PhotoMemoryPanel } from "./PhotoMemoryPanel";
import { QQChannelPanel } from "./QQChannelPanel";
import { WeixinChannelPanel } from "./WeixinChannelPanel";
import { ChannelRuntimeSettingsPanel } from "./ChannelRuntimeSettingsPanel";
import "./channels-settings.css";
import "./channel-settings-controls.css";

export type ChannelsSettingsContext = {
  runtime: { connection: "connecting" | "connected" | "offline" };
  appearance: { character: CharacterProfile | null };
};

export function ChannelsSettingsSection({
  context,
}: {
  context: ChannelsSettingsContext;
}) {
  const epoch = useSyncExternalStore(
    subscribeRuntimeContext,
    getRuntimeContextRevision,
    getRuntimeContextRevision,
  );
  return <ChannelsSettingsContent key={epoch} context={context} />;
}
function ChannelsSettingsContent({
  context,
}: {
  context: ChannelsSettingsContext;
}) {
  const [tab, setTab] = useState("weixin");
  const [address, setAddress] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void readRuntimeRequestContext()
      .then(({ connection }) => {
        if (!active) return;
        const url = new URL(connection.baseUrl);
        setAddress(`${url.origin}${url.pathname}`.replace(/\/+$/, ""));
      })
      .catch(() => {
        if (active) setAddress("后端地址尚未确认，请检查连接设置");
      });
    return () => {
      active = false;
    };
  }, []);
  const connectionControls = useConnectionControls();
  const online = context.runtime.connection === "connected";
  const characterId = context.appearance.character?.character_id ?? "default";
  return (
    <div className="channels-settings-section settings-controls">
      <SettingsSectionIntro
        icon="channels"
        title="消息渠道"
        description="把微信和 QQ 消息接入当前 Runtime，分别管理绑定、回复、资料与受众权限。"
      />
      <div className="channel-settings-location">
        <div>
          <strong>
            {connectionControls?.mode === "remote"
              ? "设置保存到远程 Runtime"
              : "设置保存到当前 Runtime"}
          </strong>
          <small>{address ?? "正在确定后端地址…"}</small>
        </div>
        {connectionControls ? (
          <button
            type="button"
            className="qq-channel-secondary-action"
            onClick={connectionControls.open}
          >
            更改 Runtime 连接
          </button>
        ) : null}
      </div>
      <nav className="channel-settings-tabs" aria-label="消息渠道设置分类">
        {[
          { id: "weixin", label: "微信" },
          { id: "qq", label: "QQ" },
          { id: "advanced", label: "交流与权限" },
        ].map((item) => (
          <button
            key={item.id}
            type="button"
            aria-current={tab === item.id ? "page" : undefined}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>
      {tab === "qq" && (
        <div className="settings-action-row">
          <div>
            <strong>由宁宁决定什么时候回应</strong>
            <small>
              自由交流、语音和账号操作权限集中管理；群参与方式在具体群里设置。
            </small>
          </div>
          <button type="button" onClick={() => setTab("advanced")}>
            交流与权限 →
          </button>
        </div>
      )}
      {tab === "weixin" ? (
        <>
          <WeixinChannelPanel
            key={characterId}
            characterId={characterId}
            runtimeOnline={online}
          />
          {characterId === "default" ? (
            <section className="channels-settings-card">
              <div className="channel-settings-body">
                <ChannelSettingsDisclosure
                  title="私聊表情与微信照片"
                  description="表情学习与照片保存独立；私聊表情库由 QQ 与微信主人共享"
                  open
                >
                  <StickerLibraryPanel
                    characterId={characterId}
                    runtimeOnline={online}
                  />
                  <PhotoMemoryPanel
                    characterId={characterId}
                    runtimeOnline={online}
                  />
                </ChannelSettingsDisclosure>
              </div>
            </section>
          ) : null}
        </>
      ) : tab === "qq" ? (
        <QQChannelPanel
          key={characterId}
          characterId={characterId}
          runtimeOnline={online}
        />
      ) : (
        <ChannelRuntimeSettingsPanel runtimeOnline={online} />
      )}
    </div>
  );
}
