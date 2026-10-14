import { useEffect, useState } from "react";
import {
  isDesktopHost,
  readRuntimeRequestContext,
} from "../chat/runtimeEndpoint";
import { SettingsGroup, SettingsSectionIntro } from "./SettingsPrimitives";
import type { CommonSettingsContext } from "./SettingsRuntimeContext";

export function KnowledgeSettingsSection({
  context,
  active = true,
}: {
  context: CommonSettingsContext;
  active?: boolean;
}) {
  const [address, setAddress] = useState("");
  useEffect(() => {
    if (!active || !isDesktopHost()) return;
    let cancelled = false;
    void readRuntimeRequestContext()
      .then((snapshot) => {
        if (!cancelled)
          setAddress(new URL("/knowledge", snapshot.connection.baseUrl).href);
      })
      .catch(() => {
        if (!cancelled) setAddress("");
      });
    return () => {
      cancelled = true;
    };
  }, [active, context.runtime.connection]);

  const native = isDesktopHost();
  return (
    <>
      <SettingsSectionIntro
        icon="memory"
        title="知识图谱"
        description="在完整画布中查看人物、项目、记忆与资料之间的关系。"
      />
      <SettingsGroup
        title="个人知识地图"
        description="图谱保留为独立页面；当前 Runtime 与本人私有会话的访问权限继续生效。"
      >
        <p>
          查看关系地图、记忆与事项、来源原文和修改冲突，也可以带着资料向宁宁提问。
        </p>
        {native ? (
          address ? (
            <>
              <p>
                <a href={address} target="_blank" rel="noopener noreferrer">
                  在浏览器打开知识图谱 ↗
                </a>
              </p>
              <p>
                <small>
                  浏览器使用自己的登录会话。首次打开时，请输入已设置的密码或访问令牌。
                </small>
              </p>
              <p>
                <code>{address}</code>
              </p>
            </>
          ) : (
            <p role="status">连接 Runtime 后可打开知识图谱。</p>
          )
        ) : (
          <p>
            <a className="settings-return" href="/knowledge">
              打开知识图谱 →
            </a>
          </p>
        )}
        <p>
          <small>图谱仅展示本人资料；服务器需已启用知识库服务。</small>
        </p>
      </SettingsGroup>
    </>
  );
}
