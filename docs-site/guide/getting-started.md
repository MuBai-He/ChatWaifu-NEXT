# 安装总览

如果你只是想先看一眼界面，从 Web 开发入口开始；准备桌宠安装包或本地模型时，再进入对应章节。
下面把几条路径放在一起，也标出它们目前各自能做到哪一步。

## 先看结论

| 场景                    | 入口                                                   | 适合谁                                 |
| ----------------------- | ------------------------------------------------------ | -------------------------------------- |
| Web 界面与 Runtime 开发 | `make bootstrap` + `make dev-runtime` + `make dev-web` | 全新 checkout、前端/后端开发           |
| 完整本地语音 Demo       | 准备本地 TTS profile 后 `make demo`                    | 已拥有本地引擎与合法声音资产的开发者   |
| macOS 桌宠开发          | 准备本地 Worker 后 `make desktop`                      | Tauri/桌宠交互开发                     |
| Windows x64 开发        | `bootstrap_x64.ps1` + `dev_x64.ps1`                    | Windows x64 或 Windows-on-ARM x64 模拟 |
| Windows 安装候选        | `build_installer_x64.ps1`                              | 发行工程与目标机验收                   |
| Windows 本地模型        | 单独构建、安装 `.cwpack`                               | 有 CUDA 笔记本或离线 STT 需求的 owner  |

::: danger 全新 checkout 不要直接运行 `make demo`
完整 Demo 会强制加载 `.local/config/tts-profiles.toml`，并要求预先存在 Qwen3-TTS MLX 与
GPT-SoVITS 的本地 Python 环境。它们包含机器路径、私有参考音频或权重，因此不会进入 Git。
全新 checkout 应先使用下面的“安全开发启动”，或者完成[本地 TTS 准备](/guide/configuration#本地-qwen3-tts-与-gpt-sovits)后再运行 Demo。
:::

## 基础要求

- Git。
- Python `>=3.12,<3.14` 与 [uv](https://docs.astral.sh/uv/)。
- Node.js 22 或更新的受支持版本。仓库会通过 `tools/run_pnpm.py` 准备项目固定的 pnpm，通常无需全局安装 pnpm。
- GNU Make（macOS/Linux 开发命令）。
- Rust stable 与平台原生构建依赖（仅 Desktop/Tauri）。
- Windows 安装候选需要 NSIS/Tauri 对应工具链；脚本会固定目标为 `x86_64-pc-windows-msvc`。

当前仓库最近一次记录的本地验证环境是 macOS arm64、Python 3.12、Node 26、pnpm 11 与 Rust 1.98；
这是一条验证记录，不等于对所有版本的兼容承诺。

## 克隆与 Bootstrap

::: info 当前仓库访问边界
源码仓库目前是 private，`git clone` 需要获得授权的 GitHub 账号。GitHub Pages 文档站会公开发布，
但这不代表源码、模型、声音或 Live2D 资产已经公开授权。
:::

```bash
git clone https://github.com/MuBai-He/ChatWaifu-NEXT.git
cd ChatWaifu-NEXT
make bootstrap
```

`make bootstrap` 会同步 Python workspace、准备固定 NLTK 数据、安装锁定的 pnpm workspace、拉取
Rust crates 并重新生成跨端协议。它不会下载宁宁模型、声音权重或私有 Live2D 资产。

## 全新 checkout 的安全开发启动

终端一使用确定性 Demo LLM 和测试音启动 Runtime：

```bash
CHATWAIFU_TTS__PROVIDER=fake make dev-runtime
```

终端二启动 Web：

```bash
make dev-web
```

打开 <http://127.0.0.1:5173/>。这条路径适合验证文字、记忆、设置、Skills 与 Fake Avatar；本地 STT
默认禁用，测试音也不是角色声音。需要完整语音回路时再进入[模型与 TTS 配置](/guide/configuration)。

## 产品输出不是同一个页面

```bash
make build-web
make build-desktop-ui
```

| 产品           | 输出                    | 标签             |
| -------------- | ----------------------- | ---------------- |
| Web Galgame    | `apps/web/dist/web`     | `web-vX.Y.Z`     |
| Desktop Pet UI | `apps/web/dist/desktop` | `desktop-vX.Y.Z` |

两者来自同一 `main`，共享 Runtime、语音、记忆与 Avatar 模块，但入口和产物图是编译期隔离的。
Windows 安装候选还会把 Desktop UI、Tauri Host、冻结 Runtime 与 AppContainer helper 组装在一起，
不是把 `apps/web/dist/desktop` 压缩一下就结束。

## 接下来

- macOS/Web 开发与完整本地 Demo：[macOS 与 Web 开发](/guide/web-development)
- Windows 安装候选：[Windows x64](/guide/windows)
- 模型、声音与只写密钥：[模型与 TTS 配置](/guide/configuration)
- 本地 CUDA/ASR 大包：[本地 AI Worker Packs](/guide/worker-packs)
