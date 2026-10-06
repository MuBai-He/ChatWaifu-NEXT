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
        description="设置桌宠与 Web 当前的说话者和记忆范围。切换会结束当前通话。"
      >
        <div className="desktop-settings-connection-row">
          <span>
            <strong>{scope?.label ?? "独立对话"}</strong>
            <small>
              独立对话按人保留记忆；共享场景使用固定听众的场景记忆。QQ
              群权限在“渠道”管理。
            </small>
          </span>
          <button
            onClick={(event) => scope?.open(event.currentTarget)}
            disabled={!scope}
          >
            管理对话
          </button>
        </div>
      </SettingsGroup>
    </>
  );
}
