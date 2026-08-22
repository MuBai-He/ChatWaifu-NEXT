# ChatWaifu NEXT

ChatWaifu NEXT（ChatWaifuV2）是 local-first 的 AI 角色 Runtime。仓库当前包含一个可直接
运行的基础 Demo：文字对话、增量回复、本地中文 TTS、语义 Avatar、抢话打断、SQLite
会话历史、明确记忆与只读 Runtime Skill 已接通。

## 直接运行 Demo

环境要求：Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 22+、pnpm 11.19+、
GNU Make。首次检出先安装依赖：

```bash
cp .env.example .env
make bootstrap
```

以后只需一个命令：

```bash
make demo
```

命令会监督启动 Runtime 与 Web、等待两者健康后打开
<http://127.0.0.1:5173>；按 `Ctrl+C` 会同时停止两个进程。若不想自动打开浏览器：

```bash
uv run python tools/run_demo.py --no-open
```

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
- 分段生成本地 WAV，并按 generation 排队播放
- “打断”立即取消 LLM/TTS、丢弃迟到输出、清空浏览器播放队列
- `AvatarCue` 驱动 thinking、speaking、idle 与口型状态
- 角色人格来自 `characters/default/character.json`
- 只有明确的“请记住…”和“请忘记…”才会修改长期记忆；忘记采用可审计 tombstone
- `runtime.status` Runtime Skill 通过 manifest 注册，只读返回实际 LLM/TTS/provider 状态
- `/avatar-lab` 保留 Fake/CI 完整路径和可选 Live2D vendor 接入口

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

## 常用开发命令

```bash
make demo               # 一次启动 Runtime + Web
make dev-runtime        # 只启动 FastAPI Runtime（127.0.0.1:8765）
make dev-web            # 只启动 Web（127.0.0.1:5173）
make test-runtime       # Runtime/API/取消/记忆专项测试
make test-avatar        # Avatar SDK 与 Web 单元测试
make test-e2e           # Chromium Avatar Lab 验收
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

这是基础可用 Demo，不声称已经完成：真实 Live2D vendor 验收、全双工 WebRTC/Pipecat、
VAD/STT 语音输入、生产级插件沙箱、训练后的自定义音色、完整 Tauri 安装包或远端 CI 矩阵。
这些能力都有独立边界，不会伪装成已经交付。

架构、执行顺序和交接约束见 `CHATWAIFU_NEXT_ARCHITECTURE.md`、
`CHATWAIFU_NEXT_IMPLEMENTATION_PLAN.md`、`CODEX_HANDOFF.md` 与 `docs/implementation-status.yaml`。
