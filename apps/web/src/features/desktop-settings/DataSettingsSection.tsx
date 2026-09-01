import { useState } from "react";
import { ProductIcon } from "../../components/ProductIcon";
import { MemoryControlCenter } from "../chat/MemoryControlCenter";
import { SkillsControlCenter } from "../chat/SkillsControlCenter";
import {
  verifyWorkerPackIntegrity,
  type WorkerPackIntegrityResponse,
} from "../chat/runtimeClient";
import { useSettingsOperation } from "../settings/useSettingsOperation";
import type { DesktopSettingsContext } from "./DesktopSettingsContext";
import { DataClearConfirmationDialog } from "./DataClearConfirmationDialog";
import { McpConnectionsPanel } from "./McpConnectionsPanel";
import { SettingsIcon } from "./SettingsIcon";
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
  const { busy, notice, run } = useSettingsOperation<"integrity">();

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
  return (
    <>
      <div className="desktop-settings-tool-grid">
        <article className="desktop-settings-tool-card">
          <span>
            <SettingsIcon name="memory" />
          </span>
          <h2>结构化记忆</h2>
          <p>查看建议、修正事实、确认敏感内容并管理遗忘。</p>
          <MemoryControlCenter
            sessionId={data.sessionId}
            onChanged={data.refreshMemories}
          />
        </article>
        <article className="desktop-settings-tool-card">
          <span>
            <SettingsIcon name="skills" />
          </span>
          <h2>Skills 与插件</h2>
          <p>管理能力权限、插件隔离、确认请求和最近运行。</p>
          <SkillsControlCenter sessionId={data.sessionId} />
        </article>
        <article className="desktop-settings-tool-card">
          <span>
            <SettingsIcon name="plugin" />
          </span>
          <h2>MCP 连接</h2>
          <p>连接本地或远程 MCP 服务，检查工具、资源和 Prompt 能力。</p>
          <McpConnectionsPanel sessionId={data.sessionId} />
        </article>
      </div>

      <SettingsGroup
        title="Worker Pack 完整性"
        description="仅在你主动操作时执行完整文件哈希；日常启动只做快速安全检查"
      >
        <div className="desktop-settings-danger-row">
          <div>
            <strong>校验本地语音与语音识别包</strong>
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

      <SettingsGroup title="本地数据" description="数据只保存在这台设备">
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
        onConfirm={context.resetConversationAndMemory}
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
