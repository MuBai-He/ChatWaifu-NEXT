# Windows x64 安装候选

ChatWaifu NEXT 的 Windows 目标固定为 x64。Windows 11 ARM 虚拟机可以作为 x64 模拟开发主机，
但不能替代原生 x64/CUDA 笔记本上的最终模型验收。

## 基础安装候选包含什么

| 包含                       | 不包含                           |
| -------------------------- | -------------------------------- |
| Desktop Pet UI             | CUDA / PyTorch                   |
| Tauri x64 Host             | Qwen3-TTS / GPT-SoVITS 权重      |
| PyInstaller onedir Runtime | faster-whisper 模型环境          |
| x64 AppContainer helper    | 私有 Live2D / 宁宁声线           |
| 必要公开资源与配置模板     | 开发 checkout、uv、Python 安装器 |

大模型能力通过独立 `.cwpack` 安装；这让基础 App 可以更新，而不必重新打包数 GB 的模型与 CUDA
环境，也降低把私有资产意外上传到 Release 的风险。

## 构建环境

- Windows x64，或所有关键进程都运行在 x64 模拟下的 Windows 11 ARM。
- Git、Node.js、uv、Rust/MSVC 与 Tauri/NSIS 依赖。
- 普通用户 PowerShell；安装器和 Worker Pack 都是 per-user，不要求管理员权限。

```powershell
git clone https://github.com/MuBai-He/ChatWaifu-NEXT.git
Set-Location .\ChatWaifu-NEXT
.\tools\windows\bootstrap_x64.ps1
```

脚本固定安装 CPython 3.12 x86_64，并校验 `.venv` 为 `win-amd64`，添加
`x86_64-pc-windows-msvc` Rust target，再恢复锁定的 pnpm workspace。如果已有错误架构的 venv：

```powershell
.\tools\windows\bootstrap_x64.ps1 -RecreateEnvironment
```

## 开发运行

```powershell
.\tools\windows\dev_x64.ps1
```

缺少本地模型 Worker 或私有 TTS profile 时，Windows 开发脚本会把它们视为可选能力：Runtime 仍能
启动，文字/Demo 与云端 Provider 可用，本地 STT 会禁用，TTS 使用安全回退。终端里的“Local worker
unavailable”是能力降级说明，不等于 Host 启动失败。

## 构建 NSIS 候选

```powershell
.\tools\windows\build_installer_x64.ps1
```

脚本会：

1. 使用独立 x64 Python 环境冻结 Runtime。
2. 构建并暂存 x64 AppContainer helper。
3. 构建 Desktop frontend 与 Tauri Host。
4. 生成 current-user NSIS 安装器。
5. 校验 Host、Runtime、helper 均为 PE machine `0x8664`。
6. smoke 启动冻结 Runtime，并输出安装器 SHA-256。

产物位于：

```text
dist/windows/installer/*-setup.exe
dist/windows/installer/*-setup.exe.sha256
```

仅用于 owner 本地测试时，可以临时叠加忽略目录中的 Live2D：

```powershell
.\tools\windows\build_installer_x64.ps1 `
  -Live2DSource "C:\path\to\private\live2d"
```

这个产物是私有候选，不能上传到公共 CI、Tag 或 Release。

## 基础安装 smoke

在没有现存 ChatWaifu 安装和进程的测试账户中运行：

```powershell
$installer = Get-ChildItem .\dist\windows\installer\*-setup.exe | Select-Object -First 1
.\tools\windows\smoke_installed_x64.ps1 -InstallerPath $installer.FullName
```

这条 destructive smoke 会真实安装、检查注册表/开始菜单/三份 x64 PE、启动冻结 Runtime、验证动态
端口与 SQLite，再强制退出、确认子进程/监听器清理、静默卸载并验证用户数据保留。请勿在有重要
测试数据或正在运行桌宠的账户上执行。

## 还不能称为公开发行

当前记录中，一个未签名、带私有 Live2D overlay 的 owner-only 候选已在 Windows 11 ARM VM 的 x64
模拟环境通过基础 smoke。但以下项目仍未完成：

- 原生 Windows x64/CUDA 笔记本验收。
- 干净账户里的前台 UI、对话、设置、记忆与音频完整流程。
- 正常退出、升级/重装与安装态 AppContainer/MCP ACL/profile 回收。
- Qwen3-TTS 与 faster-whisper `.cwpack` 的目标机真实推理。
- 仓库/资产许可证决策、代码签名与公共发布。

因此文档称它为“安装候选”，不会称为正式下载版。下一步见[本地 AI Worker Packs](/guide/worker-packs)。
