export interface DesktopWorkerPackInstallResult {
  action: string;
  pack_id: string;
  version: string;
  kind: string;
  path: string;
  config_path: string;
  restart_required: boolean;
}

export async function selectWorkerPackArchive(): Promise<string | null> {
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({
    title: "选择 ChatWaifu Worker Pack",
    multiple: false,
    directory: false,
    filters: [{ name: "ChatWaifu Worker Pack", extensions: ["cwpack"] }],
  });
  if (selected === null) return null;
  if (Array.isArray(selected)) {
    throw new Error("一次只能安装一个 Worker Pack");
  }
  return selected;
}

export async function installWorkerPackArchive(
  archivePath: string,
): Promise<DesktopWorkerPackInstallResult> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<DesktopWorkerPackInstallResult>("install_worker_pack", {
    archivePath,
  });
}
