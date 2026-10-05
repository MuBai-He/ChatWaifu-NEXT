import type { ReactNode } from "react";
import type { ChannelConnectionSnapshot } from "../chat/runtimeClient";

export function ChannelSettingsDisclosure({
  title,
  description,
  children,
  open = false,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  open?: boolean;
}) {
  return (
    <details className="channel-settings-disclosure" open={open || undefined}>
      <summary>
        <strong>{title}</strong>
        {description ? <small>{description}</small> : null}
      </summary>
      <div className="channel-settings-disclosure-content">{children}</div>
    </details>
  );
}

export function ChannelConnectionDetails({
  connection,
}: {
  connection: ChannelConnectionSnapshot;
}) {
  const c = connection.configuration;
  const kinds = (values: string[] = []) =>
    values
      .map((v) => ({ text: "文字", image: "图片", audio: "语音" })[v] ?? v)
      .join("、");
  return (
    <ChannelSettingsDisclosure
      title="连接身份与支持能力"
      description="账号、接收范围与当前状态"
    >
      <dl className="channel-settings-facts">
        <dt>角色</dt>
        <dd>{c.character_id}</dd>
        <dt>角色账号</dt>
        <dd>{c.account_key ?? "尚未确认"}</dd>
        <dt>私聊接收者</dt>
        <dd>{(c.allowed_sender_keys ?? []).join("、") || "无"}</dd>
        <dt>私聊记忆范围</dt>
        <dd>{c.principal_scope}</dd>
        <dt>输入支持</dt>
        <dd>{kinds(connection.capabilities?.inbound_message_kinds)}</dd>
        <dt>输出支持</dt>
        <dd>{kinds(connection.capabilities?.outbound_message_kinds)}</dd>
        <dt>最近活动</dt>
        <dd>
          {connection.last_seen_at
            ? new Date(connection.last_seen_at).toLocaleString("zh-CN")
            : "尚无活动"}
        </dd>
      </dl>
      <p>
        接收者由配对确定。更换账号或主人需重新绑定；群成员在群管理中单独授权。
      </p>
    </ChannelSettingsDisclosure>
  );
}
