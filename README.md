# ChatWaifu NEXT

ChatWaifu NEXT（ChatWaifuV2）是 local-first 的 AI 角色 Runtime。仓库当前包含一个可直接
运行的基础 Demo：文字与真实麦克风对话、VAD 自动回合、本地 STT/中文 TTS、Pipecat
SmallWebRTC 全双工音频、语义 Avatar、抢话打断、SQLite 会话历史、明确记忆与只读
Runtime Skill 已接通。

## 直接运行 Demo

环境要求：Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 22+（含 npm）、
GNU Make。项目会自动在 `.local/tooling/` 准备固定版本的 pnpm，无需全局安装。首次检出可直接运行：

```bash
make demo
```

命令也会自动安装或校验前端依赖；`.env` 是可选配置，只有需要覆盖默认 provider 时才创建：

```bash
cp .env.example .env
```

命令会监督启动隔离的 faster-whisper worker、Runtime 与 Web，等待三者健康后打开
<http://127.0.0.1:5173>；按 `Ctrl+C` 会同时停止三个进程。若不想自动打开浏览器：

```bash
make demo DEMO_ARGS=--no-open
```

首次启动会下载公开的多语言 `faster-whisper base` 模型（约 150 MB），之后复用
`.local/models/faster-whisper/` 缓存。模型推理在独立 worker 中运行，麦克风音频不会发往
云端。页面就绪后点击“开启语音”并允许麦克风；说完约 650 ms 后自动结束回合，不需要再按
发送。

Demo 默认使用明确标注的离线 Demo LLM。在 macOS 上，`tts.provider=auto` 会选择系统
`say` 的 `Tingting` 中文语音；CI 或缺少系统语音工具的平台回退到 `fake` 测试音。真实本地
模型可通过 `.env` 切换到 OpenAI-compatible 端点，例如 Ollama、LM Studio 或 vLLM：

```bash
CHATWAIFU_LLM__PROVIDER=openai_compatible
CHATWAIFU_LLM__MODEL=qwen3:8b
CHATWAIFU_LLM__BASE_URL=http://127.0.0.1:11434/v1
```

## Demo 能做什么

- WebSocket 增量文本和 SQLite append-only 事件流
- 浏览器麦克风与输入设备选择、实时音量计、Silero VAD 和本地 faster-whisper 转写
- Pipecat SmallWebRTC 双向音频；分段生成本地 WAV 后通过远端音轨播放
- 开口抢话或点击“打断”会取消旧 generation、丢弃迟到输出并清空播放队列
- 桌面页面固定为显示区域高度，右侧历史独立滚动，左侧 Live2D 始终留在视口内
- “重置”经确认后清空当前对话、全部明确记忆、事件历史和本地生成语音
- `AvatarCue` 驱动 thinking、speaking、idle 与口型状态
- 角色人格来自 `characters/default/character.json`
- 只有明确的“请记住…”和“请忘记…”才会修改长期记忆；忘记采用可审计 tombstone
- `runtime.status` Runtime Skill 通过 manifest 注册，只读返回实际 LLM/TTS/provider 状态
- 安装本地 Cubism vendor 后，主聊天和 `/avatar-lab` 会使用真实 Live2D；缺失时自动回退 Fake

数据默认写入 `.local/data/chatwaifu.db` 与 `.local/data/audio/`，两者都不会提交到 Git。

## TTS 选择

默认 Demo 没有采用 GPT-SoVITS。它适合作为已有角色音色训练/推理服务，但不适合作为基础
安装依赖。当前分层策略是：

1. macOS 系统语音：零下载、立即可用，用于当前 Demo 验收。
2. sherpa-onnx + Kokoro v1.1-zh：推荐的轻量本地发行目标；中英双语、103 个 speaker、
   24 kHz，不依赖 PyTorch，但不等于音色克隆。
3. CosyVoice 3 0.5B worker：需要零样本音色克隆时优先评估的独立服务。
4. GPT-SoVITS：保留为用户自行管理的外部 HTTP provider，不塞进主 Runtime。

完整依据和边界见 [ADR 0012](docs/adr/0012-tiered-local-tts.md)。

实时语音的数据流、进程边界和取消语义见
[Realtime voice demo slice](docs/architecture/realtime-voice-demo.md)。

## Live2D 安装

已解压官方 Cubism SDK for Web 5 R5 到 `~/Downloads/CubismSdkForWeb-5-r.5` 时，运行：

```bash
make setup-live2d-vendor
make demo
```

命令会使用 SDK 内公开测试模型 Natori，构建官方 Framework 适配桥，并把 Core、桥接产物和
模型放进 Git 忽略目录。主聊天会自动显示 Live2D；也可打开 `/avatar-lab` 验证表情、动作、
口型与点击命中。换 SDK 路径或样例模型的方法见
[Live2D vendor 说明](vendor/live2d/README.md)。发布或商用前仍需单独复核 Core 与模型许可。

## 常用开发命令

```bash
make demo               # 一次启动 Runtime + Web
make setup-stt-worker   # 只准备隔离的 faster-whisper worker 环境
make dev-runtime        # 只启动 FastAPI Runtime（127.0.0.1:8765）
make dev-web            # 只启动 Web（127.0.0.1:5173）
make test-runtime       # Runtime/API/取消/记忆专项测试
make test-avatar        # Avatar SDK 与 Web 单元测试
make test-e2e           # Chromium Avatar Lab 验收
make setup-live2d-vendor # 从 Downloads 安装并构建本地 Live2D vendor
make check-live2d-vendor # 检查 Framework/Core/桥接/模型是否齐全
make format             # Python、TypeScript、Rust 格式化
make lint               # Ruff、ESLint、Clippy
make typecheck          # Pyright、tsc、cargo check
make test               # Python、TypeScript、Rust 测试
make check-generated    # 协议受控产物无漂移检查
```

协议以 `packages/protocol-python/src/chatwaifu_protocol/` 为源；不要手工编辑
`schemas/domain/v1/` 或生成的 TypeScript domain 文件。专有 Cubism Core 与有授权的角色模型
不进入仓库，缺失时 Fake avatar 仍然完整可用。

## 当前边界

这是基础可用 Demo，不声称已经完成：可再发行的 Live2D 资产包与自定义角色模型、RTVI
数据通道与公网 TURN、生产级插件沙箱、训练后的自定义音色、完整 Tauri 安装包、长时间语音
压力测试或远端 CI 矩阵。这些能力都有独立边界，不会伪装成已经交付。

架构、执行顺序和交接约束见 `CHATWAIFU_NEXT_ARCHITECTURE.md`、
`CHATWAIFU_NEXT_IMPLEMENTATION_PLAN.md`、`CODEX_HANDOFF.md` 与 `docs/implementation-status.yaml`。
