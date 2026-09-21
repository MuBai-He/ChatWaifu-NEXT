import {
  useConnectionControls,
  useScopeControls,
} from "../connection/clientControls";
import { SettingsGroup, SettingsSectionIntro } from "./SettingsPrimitives";

export function ConnectionSettingsSection() {
  const connection = useConnectionControls();
  const scope = useScopeControls();
  return (
    <>
      <SettingsSectionIntro
        icon="models"
        title="连接与对话"
        description="选择后端的运行位置，管理对话参与者与记忆范围。"
      />
      <SettingsGroup
        title="运行方式"
        description="桌宠始终在本机显示，后端可以在本机或服务器运行。"
      >
        <div className="desktop-settings-connection-row">
          <span>
            <strong>
              {connection?.mode === "remote" ? "远程服务器" : "本机运行"}
            </strong>
            <small>{connection?.address ?? "使用这台设备上的服务配置"}</small>
          </span>
          <button onClick={() => connection?.open()} disabled={!connection}>
            更改连接
          </button>
        </div>
      </SettingsGroup>
      <SettingsGroup
        title="对话范围"
        description="独立对话与共享场景分别保留记忆。切换会结束当前通话。"
      >
        <div className="desktop-settings-connection-row">
          <span>
            <strong>{scope?.label ?? "独立对话"}</strong>
            <small>管理参与者，或选择共享场景</small>
          </span>
          <button onClick={() => scope?.open()} disabled={!scope}>
            管理对话
          </button>
        </div>
      </SettingsGroup>
    </>
  );
}
