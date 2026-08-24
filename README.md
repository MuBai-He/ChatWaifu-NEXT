# ChatWaifu NEXT

ChatWaifu NEXT（ChatWaifuV2）是 local-first 的 AI 角色 Runtime。仓库当前包含一个可直接
运行的绫地宁宁主题基础 Demo：文字与真实麦克风对话、VAD 自动回合、本地 STT/角色 TTS、Pipecat
SmallWebRTC 全双工音频、语义 Avatar、抢话打断、SQLite 会话历史、结构化长期记忆，以及带权限与
确认的 Runtime Skills/MCP 插件链路已接通。

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

命令会监督启动隔离的 faster-whisper worker、Kokoro TTS worker、Runtime 与 Web，等待全部
健康后打开 <http://127.0.0.1:5173>；按 `Ctrl+C` 会同时停止全部进程。若不想自动打开浏览器：

```bash
make demo DEMO_ARGS=--no-open
```

首次启动会下载公开的多语言 `faster-whisper base`（约 150 MB）和 Kokoro v1.1（约 365 MB），
之后复用 `.local/models/` 缓存。STT/TTS 推理都在独立本地 worker 中运行，麦克风音频不会发往
云端。页面就绪后点击“开启语音”并允许麦克风，默认按住“说话”讲话，松开约 650 ms 后由
VAD 自动结束回合，不需要再按发送。只有明确切换到“自由对话”后才会持续送入麦克风；该模式
也会听到旁边人的话，适合安静、独处的环境。

Demo 默认使用明确标注的离线 Demo LLM；若 `.env` 已配置 OpenAI-compatible 服务，启动器会
直接使用真实模型。模型可以是本机 Ollama、LM Studio、vLLM，也可以是用户明确选择的兼容云
端点。API Key 只保存在本机 `.env`，不会进入 Web 或公开配置接口：

```bash
CHATWAIFU_LLM__PROVIDER=openai_compatible
CHATWAIFU_LLM__MODEL=qwen3:8b
CHATWAIFU_LLM__BASE_URL=http://127.0.0.1:11434/v1
# CHATWAIFU_LLM__API_KEY=仅在服务要求时填写
```

默认角色为绫地宁宁主题人格，近期已提交对话会作为下一轮上下文。角色提示词约束为日常
Galgame 节奏和短回复，不复述原作长对白，也不会把未写入 Runtime 记忆的内容当成事实。

## Demo 能做什么

- WebSocket 增量文本和 SQLite append-only 事件流
- 浏览器麦克风与输入设备选择、默认按住说话、可选自由对话、Silero VAD 和本地
  faster-whisper 转写
- Pipecat SmallWebRTC 双向音频；分段生成本地 WAV 后通过远端音轨播放
- 按住说话时开口抢话，或点击“打断”，会取消旧 generation、丢弃迟到输出并清空播放队列
- 桌面页面固定为显示区域高度，右侧历史独立滚动，左侧 Live2D 始终留在视口内
- “重置”经确认后清空当前对话、全部明确记忆、事件历史和本地生成语音
- `AvatarCue` 驱动 thinking、speaking、idle、表情、角色动作与口型状态；明确的对话意图和
  角色触摸可触发宁宁的 `headpat`、`stare`、`flustered`、`sing`，不会随机播放长动作
- 绫地宁宁主题人格、开场白、角色声线与内容声明来自 `characters/default/character.json`
- 方案 A 结构化记忆：明确普通记忆直接提交，普通对话候选进入“记忆中心”，敏感内容逐条确认
- 记忆支持来源查看、FTS5 召回、去重、冲突 supersede、修正、置顶与可审计 tombstone
- `runtime.status` Runtime Skill 通过版本化 manifest 注册，只读返回实际 provider 状态
- “Skills & 插件”控制中心支持按需加载说明、运行记录、权限确认、取消、启停和可恢复卸载
- 内置 Local Echo 示例验证 MCP stdio、Schema、超时、取消与写操作确认；可从控制中心安装
- 安装本地 Cubism vendor 后，主聊天和 `/avatar-lab` 会使用真实 Live2D；缺失时自动回退 Fake

数据默认写入 `.local/data/chatwaifu.db` 与 `.local/data/audio/`，两者都不会提交到 Git。

## Runtime Skills 与本地插件

产品 Runtime Skills 位于 `skills/`，Codex 开发技能位于 `.agents/skills/`，两者不会互相加载。
页面左侧打开“Skills & 插件”，可安装仓库内 Local Echo 测试插件，或填写一个本地插件目录的
绝对路径。插件需要 `plugin.json`、`SKILL.md` 与 `chatwaifu.yaml`；安装时拒绝 symlink、越界
路径和过大文件。

插件通过 MCP stdio 在逐次创建的 Python 子进程中运行，使用独立工作目录、清理后的环境、
Schema 校验、统一错误、超时与取消。写入、破坏、外部通信和设备控制不会因为安装插件就自动
获得授权；权限 grant 与每次操作确认是两个独立步骤。当前是软隔离，不是 OS 沙箱，只应安装
信任的本地插件。具体边界见 [ADR 0013](docs/adr/0013-permissioned-stdio-mcp-plugins.md)。

方案 A 已作为 SQLite WAL + FTS5 的唯一记忆真值落地；方案 B 语义索引与方案 C 时序图仅保留
禁用端口，不下载向量模型或引入图数据库。实现边界见
[结构化记忆内核](docs/architecture/structured-memory-kernel.md)，后续评估门见
[记忆系统方案调研](docs/research/memory-system-options.md)。

## TTS 选择

默认 Demo 没有采用 GPT-SoVITS。它适合作为已有且已获授权的角色音色训练/推理服务，但不适合
成为基础安装依赖。当前分层策略是：

1. sherpa-onnx + Kokoro v1.1：当前 `make demo` 的角色语音；中英双语、103 个 speaker、
   24 kHz，不依赖 PyTorch。当前 speaker 是普通合成女声，不是原作声优克隆。
2. macOS 系统语音：零下载的手动回退路径，直接运行 Runtime 且选择 `auto` 时可用。
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

当前本机也可从用户提供且仅限本地的 `~/Downloads/AYACHI NENE.7z` 安装宁宁模型：

```bash
uv run python tools/setup_ayachi_nene_model.py
make build-live2d-bridge
```

该命令只写入 Git 忽略的 vendor 目录，模型资产不会被提交；再发行前必须自行确认授权。

## 常用开发命令

```bash
make demo               # 一次启动 Runtime + Web
make setup-stt-worker   # 只准备隔离的 faster-whisper worker 环境
make setup-tts-worker   # 只准备 Kokoro worker 并校验/下载公开模型
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
数据通道与公网 TURN、生产级插件沙箱、训练后的自定义音色、向量/图记忆后端、完整 Tauri
安装包、长时间语音压力测试或远端 CI 矩阵。这些能力都有独立边界，不会伪装成已经交付。

架构、执行顺序和交接约束见 `CHATWAIFU_NEXT_ARCHITECTURE.md`、
`CHATWAIFU_NEXT_IMPLEMENTATION_PLAN.md`、`CODEX_HANDOFF.md` 与 `docs/implementation-status.yaml`。
