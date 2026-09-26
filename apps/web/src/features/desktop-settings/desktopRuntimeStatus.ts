export type DesktopRuntimeConnection = "connecting" | "connected" | "offline";

export function connectionLabel(
  connection: DesktopRuntimeConnection,
  remote = false,
): string {
  if (remote)
    return connection === "connected"
      ? "已连接"
      : connection === "connecting"
        ? "正在连接"
        : "服务器未连接";
  if (connection === "connected") return "已连接";
  if (connection === "connecting") return "正在启动";
  return "Runtime 离线";
}

export function connectionDetail(
  connection: DesktopRuntimeConnection,
  runtimeVersion?: string,
  remote = false,
): string {
  if (remote)
    return connection === "connecting"
      ? "正在连接远程服务器"
      : connection === "connected"
        ? "远程服务器"
        : "检查服务器地址与网络连接";
  if (connection === "connecting") {
    return "正在启动 Runtime 与已启用的本地模型，首次加载可能需要几分钟";
  }
  if (connection === "connected" && runtimeVersion) {
    return `Runtime ${runtimeVersion}`;
  }
  return "本地服务";
}
