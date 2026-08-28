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

命令也会自动安装或校验前端依赖；`.env` 只保留端口、数据目录等部署覆盖，不再是产品模型
设置入口。需要时可创建：

```bash
cp .env.example .env
```

命令会监督启动隔离的 faster-whisper、Qwen3-TTS、GPT-SoVITS worker、Runtime 与 Web，等待全部
健康后打开 <http://127.0.0.1:5173>；按 `Ctrl+C` 会同时停止全部进程。若不想自动打开浏览器：

```bash
make demo DEMO_ARGS=--no-open
```

macOS 桌宠开发版可改用一个命令启动同一套本地 Runtime 与透明 Tauri 角色窗口：

```bash
make desktop
```

按住宁宁角色任意有效部位并移动鼠标可拖动桌宠，单击人物仍会触发角色互动；右下角 `◇` 打开桌宠设置，
`◉` 连接麦克风。HUD 只控制字幕显示，不再显示在线文字和装饰线。
菜单栏的月牙图标提供显示/隐藏角色、打开桌宠设置、切换鼠标穿透和退出。独立设置窗口包含桌宠、
声音、模型、陪伴和数据五类设置，不再复用 Galgame 对话界面。窗口位置、尺寸、置顶、鼠标穿透与
字幕状态保存在系统应用配置目录，不进入仓库；设置变更会实时同步到悬浮窗。设置窗口不会成为第二个
语音播放端，因此不会与桌宠重叠播放 TTS。透明 macOS 窗口依赖 Tauri private API，只用于桌宠发行
profile，不满足 Mac App Store 上架条件。Tauri 会以动态端口启动并监督本地 Runtime/Worker 服务栈；
异常退出会限次自动重启，连续失败后可从“陪伴”设置或托盘手动恢复。

Windows 发布目标固定为 x64。即使在 Windows 11 ARM 虚拟机中调试，也不生成 ARM 应用；首次在
PowerShell 中准备 x64 Python/Rust 目标，然后使用开发脚本启动带热更新和终端日志的桌面程序：

```powershell
.\tools\windows\bootstrap_x64.ps1
.\tools\windows\dev_x64.ps1
```

缺少 Windows 本地模型 Worker 时，开发脚本会启动可连接的 Demo Runtime，并暂时禁用本地麦克风
转写、使用确定性语音回退；支持的云端 TTS 仍可在设置界面配置。按 `Ctrl+C` 会停止桌面程序、
Runtime 和开发服务器。若本地 Live2D 使用超过 4096 像素的纹理，开发脚本会保留 `.source.png`
原图并生成仅限本机的 4K 运行副本，避免 Windows 虚拟显卡因 8K 纹理解码超时而落入安全回退。
模型与两份纹理仍位于 Git 忽略目录，不会提交。准备发布文件时再执行带测试和 PE 架构校验的构建：

```powershell
.\tools\windows\build_x64.ps1
```

若仓库中已有其他架构的 `.venv`，首次准备时增加 `-RecreateEnvironment`。Node、Git、uv 或
rustup 启动器本身可以是 ARM64 工具，最终 Runtime 解释器、Rust target 与桌面 EXE 仍会被脚本
分别校验为 `win-amd64`、`x86_64-pc-windows-msvc` 和 PE `0x8664`。

桌面版使用“月牙与星芒”应用标识；macOS 菜单栏使用同主题的单色模板图标，可随系统浅色/深色
菜单栏自动着色，不再显示单字托盘标题。

首次启动会下载公开的多语言 `faster-whisper base`（约 150 MB）和已配置的本地语音模型，
之后复用 `.local/models/` 缓存。STT/TTS 推理都在独立本地 worker 中运行，麦克风音频不会发往
云端。页面就绪后点击“开启语音”并允许麦克风，默认按住“说话”讲话，松开约 650 ms 后由
VAD 自动结束回合，不需要再按发送。只有明确切换到“自由对话”后才会持续送入麦克风；该模式
默认要求在句首叫“宁宁”或设置中的其他称呼，本地 STT 确认称呼后才会抢话并提交回合，旁边未叫
到她的交谈会被忽略。该门控可在“陪伴”设置中关闭。

“陪伴”设置还提供跨午夜安静时段、主动问候冷却/每日预算和空闲模型休眠。主动问候默认关闭；
开启后只在会话空闲且不处于安静时段时生成短问候。ASR/TTS 权重休眠不会关闭桌宠、Runtime、
记忆或会话，下一次交互会按需重新加载。

Demo 默认使用明确标注的离线 Demo LLM。打开页面的 `CONFIG / 设置`，可以分别设置聊天、记忆
提取、记忆总结与 Embedding 模型；它们可指向本机 Ollama、LM Studio、vLLM 或用户明确选择的
OpenAI-compatible 端点，保存后立即生效而无需重启。API Key 是只写字段：Web 不保存、不回显，
Runtime 只将它写入 Git 忽略的 `.local/config/model-secrets.json`（权限 `0600`）。旧 `.env`
聊天字段仅作为首次迁移兼容，不再是推荐入口。

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
- 绫地宁宁主题人格、开场白、角色声线、动作能力与关系策略来自 `characters/default/` 六文件角色包
- 持久化 Character Kernel 管理情绪、熟悉度、信任、好感、关系阶段、响应语义与 Prompt 分区预算
- 方案 A 结构化记忆：明确普通记忆直接提交，普通对话候选进入“记忆中心”，敏感内容逐条确认
- 记忆支持来源查看、FTS5 + 可重建 Embedding 混合召回、模型辅助提取、去重、冲突 supersede、修正、置顶与可审计 tombstone
- `runtime.status` Runtime Skill 通过版本化 manifest 注册，只读返回实际 provider 状态
- “Skills & 插件”控制中心支持按需加载说明、运行记录、权限确认、取消、启停和可恢复卸载
- 内置 Local Echo 示例验证 MCP stdio、Schema、超时、取消与写操作确认；“数据 → MCP 连接”还可管理
  任意 stdio、Streamable HTTP 或兼容 SSE 服务，并浏览 Tools、Resources、Resource Templates 与 Prompts
- 安装本地 Cubism vendor 后，主聊天和 `/avatar-lab` 会使用真实 Live2D；缺失时自动回退 Fake

数据默认写入 `.local/data/chatwaifu.db`、`.local/data/audio/` 与本地模型密钥文件，均不会提交到 Git。

## Runtime Skills、MCP Host 与 MCP Server

产品 Runtime Skills 位于 `skills/`，Codex 开发技能位于 `.agents/skills/`，两者不会互相加载。
页面左侧打开“Skills & 插件”，可安装仓库内 Local Echo 测试插件，或填写一个本地插件目录的
绝对路径。插件需要 `plugin.json`、`SKILL.md` 与 `chatwaifu.yaml`；安装时拒绝 symlink、越界
路径和过大文件。

Runtime 现在同时是 MCP Host 与受限 MCP Server。“数据 → MCP 连接”可保存 stdio、Streamable HTTP
或兼容 SSE 连接；Bearer Token 是只写字段，存入权限为 `0600` 的本地文件。连接测试会分页发现
Tools、Resources、Resource Templates 与 Prompts，外部工具随后映射为 Runtime Skill，继续经过
Schema 校验、Permission Broker、逐次副作用确认、超时、取消与审计，而不是由前端或模型绕过策略层
直接调用。远程地址默认仅允许 loopback；显式允许远程后仍会在每次连接前重新解析 DNS，并拒绝
link-local、metadata、reserved、重定向和系统代理继承。

本地不可信 stdio 连接默认要求 OS 级隔离且禁止网络：macOS 使用 Seatbelt，Linux 使用 bubblewrap；
缺少可强制执行的后端时会 fail closed。Windows 当前没有批准的原生 AppContainer 后端，因此
`sandbox_mode=required` 的不可信 stdio 服务会被拒绝；远程 MCP 与用户显式标为可信的本地连接仍可用。
连接测试后设置页会显示实际隔离后端；显式关闭沙箱时网络策略只能是“允许”，不会把进程清理或环境
变量过滤误报成网络隔离。
运行中的 ChatWaifu Runtime 还在同一个 loopback 端口公开标准 Streamable HTTP `/mcp`：匿名模式仅发布
安全只读能力，配置 Runtime 管理 Token 后才认证发布副作用工具，而且调用仍要求有效会话并可能进入
本地确认队列。完整边界见 [ADR 0018](docs/adr/0018-complete-mcp-host-server-and-sandbox.md)。

方案 A 已作为 SQLite WAL + FTS5 的唯一记忆真值落地；方案 B 语义索引与方案 C 时序图仅保留
禁用端口，不下载向量模型或引入图数据库。实现边界见
[结构化记忆内核](docs/architecture/structured-memory-kernel.md)，后续评估门见
[记忆系统方案调研](docs/research/memory-system-options.md)。

## TTS 选择

页面默认选择本地 Qwen3-TTS MLX，GPT-SoVITS 是可切换的独立重模型 Worker；两者通过同一
Runtime TTS provider contract 运行，不会把模型 SDK 或路径暴露给 Web。切换会先取消当前
generation，并在不再使用旧 provider 时卸载模型。Kokoro 和 macOS 系统语音保留为轻量回退。

当前 Qwen 使用官方 0.6B Base 的 MLX 8-bit 推理版本，仍是公开基础声线。仓库提供一个本地、
不可分发的宁宁数据审计与 Colab 微调包生成器；训练数据、WAV、checkpoint 和评测音频全部位于
`.local/`，不会提交。使用方法和训练后评测门见
[Qwen3-TTS 角色微调](docs/operations/qwen3-tts-character-finetuning.md)。统一接口、懒加载与
本地模型边界见 [ADR 0014](docs/adr/0014-unified-selectable-neural-tts.md)。

声音设置还可分别启用阿里云百炼 Qwen VC Realtime 或 CosyVoice Realtime 声音复刻。两者都能
边生成边播放；Qwen VC 保留复刻声线但不接受情绪指令，CosyVoice 3.5 Plus/Flash 还能把基础
情绪指令与 Character Kernel 的当前语气合并。音色 ID、基础模型、区域、语种、语速和音量在设置
页保存，API Key 使用独立的本地权限文件且不会回显。百炼仅接收当前待朗读句段，并通过有界 PCM
流输出；完整 WAV 继续作为断线回退。流式合约、取消和云端出站边界见
[ADR 0017](docs/adr/0017-provider-neutral-streaming-tts.md)。百炼复刻音色与创建时的
`target_model` 严格绑定；设置页填写的实时模型必须与该字段完全一致。

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
make desktop            # 一次启动 Runtime + Web + Tauri 桌宠
make setup-nltk-data    # 准备 Pipecat 断句所需的本地 NLTK 数据
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

`punkt_tab` 会从 NLTK 官方数据仓库的固定提交下载到 Git 忽略的 `.local/nltk_data`，并在
解压前校验 SHA-256。Runtime 会在导入 Pipecat 前使用该本地目录，因此代理 Fake-IP 模式不会
触发 NLTK 的 SSRF 告警；常规启动不会重复下载。

协议以 `packages/protocol-python/src/chatwaifu_protocol/` 为源；不要手工编辑
`schemas/domain/v1/` 或生成的 TypeScript domain 文件。专有 Cubism Core 与有授权的角色模型
不进入仓库，缺失时 Fake avatar 仍然完整可用。

## 当前边界

这是基础可用 Demo，不声称已经完成：可再发行的 Live2D 资产包与自定义角色模型、RTVI
数据通道与公网 TURN、生产级插件沙箱、训练后的自定义音色、向量/图记忆后端、签名并可分发的
Tauri 安装包、冻结发布版 sidecar、长时间语音压力测试或远端 CI 矩阵。这些能力都有
独立边界，不会伪装成已经交付。

架构、执行顺序和交接约束见 `CHATWAIFU_NEXT_ARCHITECTURE.md`、
`CHATWAIFU_NEXT_IMPLEMENTATION_PLAN.md`、`CODEX_HANDOFF.md` 与 `docs/implementation-status.yaml`。
