import { isRemoteRuntime } from "../chat/runtimeEndpoint";
import { useState } from "react";
import { ProductIcon } from "../../components/ProductIcon";
import {
  verifyWorkerPackIntegrity,
  type WorkerPackIntegrityResponse,
} from "../chat/runtimeClient";
import { useSettingsOperation } from "../settings/useSettingsOperation";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { DataClearConfirmationDialog } from "./DataClearConfirmationDialog";
import {
  installWorkerPackArchive,
  selectWorkerPackArchive,
} from "./desktopWorkerPacks";
import { SettingsGroup, SettingsStatus } from "./SettingsPrimitives";

export function DataSettingsSection({
  context,
}: {
  context: DesktopSettingsContext;
}) {
  const { data } = context;
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [integrity, setIntegrity] =
    useState<WorkerPackIntegrityResponse | null>(null);
  const { busy, notice, run, setNotice } = useSettingsOperation<
    "install" | "integrity"
  >();
  const desktopHost = context.desktop?.desktopHost ?? false;
  const workerPackInstallSupported =
    desktopHost &&
    !isRemoteRuntime() &&
    /Windows/i.test(window.navigator.userAgent);

  const installLocalPack = async () => {
    let archive: string | null;
    try {
      archive = await selectWorkerPackArchive();
    } catch (error: unknown) {
      setNotice({
        tone: "error",
        text: error instanceof Error ? error.message : "无法选择 Worker Pack",
      });
      return;
    }
    if (!archive) return;
    await run("install", () => installWorkerPackArchive(archive), {
      pending:
        "正在完整校验并安装 Worker Pack；大型模型可能需要几分钟，请保持应用运行…",
      success: (result) =>
        `已安装并启用 ${result.pack_id}@${result.version}；本地服务正在重启。`,
      error: "Worker Pack 安装失败",
    });
  };

  const verifyIntegrity = async () => {
    await run(
      "integrity",
      async () => {
        const result = await verifyWorkerPackIntegrity();
        setIntegrity(result);
        if (!result.valid) {
          throw new Error(
            `完整性校验发现 ${result.errors.length} 个问题，请重新安装对应 Worker Pack。`,
          );
        }
        return result;
      },
      {
        pending:
          "正在逐个读取并校验 Worker Pack，较大的本地模型可能需要几分钟…",
        success: (result) =>
          result.packs.length
            ? `完整性校验通过：${result.packs.length} 个 Worker Pack，${result.packs.reduce((count, pack) => count + pack.file_count, 0).toLocaleString()} 个文件。`
            : "未发现已安装的 Worker Pack。",
        error: "Worker Pack 完整性校验失败",
      },
    );
  };
  const resetAndClearCachedViews = async () => {
    const completed = await context.resetConversationAndMemory();
    return completed;
  };
  return (
    <>
      <SettingsGroup
        title="Worker Pack 管理"
        description={
          isRemoteRuntime()
            ? "语音与识别模型在服务器上管理，此客户端无需安装模型包。"
            : "本地语音与识别包是可选项，可在首次配置时跳过，之后随时安装"
        }
      >
        <div className="worker-pack-action-row">
          <div>
            <strong>从本机安装 .cwpack</strong>
            <small>
              选择单独下载的模型包；应用会完整校验、按用户安装并重启本地服务。
            </small>
          </div>
          <button
            type="button"
            disabled={!workerPackInstallSupported || busy !== null}
            title={
              workerPackInstallSupported
                ? undefined
                : "当前请在 ChatWaifu Windows 安装版中使用"
            }
            onClick={() => void installLocalPack()}
          >
            <ProductIcon name="plus" />
            {busy === "install" ? "正在安装…" : "选择并安装"}
          </button>
        </div>
        <div className="desktop-settings-danger-row">
          <div>
            <strong>完整校验本地语音与语音识别包</strong>
            <small>
              检查清单、SHA-256、额外文件和 Windows PE
              架构。校验期间仍可使用设置页。
            </small>
          </div>
          <button
            type="button"
            disabled={
              context.runtime.connection !== "connected" || busy !== null
            }
            onClick={() => void verifyIntegrity()}
          >
            <ProductIcon name="refresh" />
            {busy === "integrity" ? "正在完整校验…" : "开始完整校验"}
          </button>
        </div>
        {integrity?.packs.length ? (
          <ul
            className="worker-pack-integrity-list"
            aria-label="已校验 Worker Pack"
          >
            {integrity.packs.map((pack) => (
              <li key={`${pack.pack_id}@${pack.version}`}>
                <strong>{pack.pack_id}</strong>
                <span>
                  {pack.kind.toUpperCase()} · {pack.backend} ·{" "}
                  {pack.file_count.toLocaleString()} 个文件 ·{" "}
                  {formatBytes(pack.size_bytes)}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {integrity?.errors.length ? (
          <ul className="worker-pack-integrity-errors" aria-label="完整性问题">
            {integrity.errors.map((error, index) => (
              <li key={`${index}-${error}`}>{error}</li>
            ))}
          </ul>
        ) : null}
        <SettingsStatus notice={notice} className="desktop-settings-info" />
      </SettingsGroup>

      <SettingsGroup
        title="当前对话数据"
        description="数据保存在当前连接的 Runtime，清理前请核对范围"
      >
        <div className="desktop-settings-danger-row">
          <div>
            <strong>重置对话与记忆</strong>
            <small>清空当前对话、明确记忆和已生成语音，操作无法撤销。</small>
          </div>
          <button
            type="button"
            disabled={!data.sessionId || data.resetting}
            onClick={() => setConfirmingClear(true)}
          >
            <ProductIcon name="trash" />
            {data.resetting ? "正在清除…" : "清除当前数据"}
          </button>
        </div>
      </SettingsGroup>

      <DataClearConfirmationDialog
        open={confirmingClear}
        busy={data.resetting}
        onCancel={() => setConfirmingClear(false)}
        onConfirm={resetAndClearCachedViews}
      />
    </>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1_024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1_024;
  let unit = units[0];
  for (const next of units.slice(1)) {
    if (value < 1_024) break;
    value /= 1_024;
    unit = next;
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`;
}
