export type DesktopRuntimeConnection = "connecting" | "connected" | "offline";

export function connectionLabel(connection: DesktopRuntimeConnection): string {
  if (connection === "connected") return "已连接";
  if (connection === "connecting") return "正在启动";
  return "Runtime 离线";
}

export function connectionDetail(
  connection: DesktopRuntimeConnection,
  runtimeVersion?: string,
): string {
  if (connection === "connecting") {
    return "正在启动 Runtime 与已启用的本地模型，首次加载可能需要几分钟";
  }
  if (connection === "connected" && runtimeVersion) {
    return `Runtime ${runtimeVersion}`;
  }
  return "本地服务";
}
