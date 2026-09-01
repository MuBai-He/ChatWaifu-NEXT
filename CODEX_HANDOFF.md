# ChatWaifu Next Codex 执行交接书

> 本文件可以直接作为 Codex 的项目级任务说明。  
> 必读顺序：本文件 -> `CHATWAIFU_NEXT_ARCHITECTURE.md` -> `CHATWAIFU_NEXT_IMPLEMENTATION_PLAN.md`。

---

## 0.1 2026-09-01 当前接手状态

本节是跨机器接手入口，优先于本文后面的历史首轮任务。第 8、9 节记录的是项目初始化时的
Phase 0/1 约束，已经完成，**不得再把它们当作当前任务限制**。当前事实以
`docs/implementation-status.yaml`、已接受 ADR 和当前 checkout 为准。

### Git 基线

```text
Repository: https://github.com/MuBai-He/ChatWaifu-NEXT.git
Branch:     codex/windows-installer
Validated product-code commit: 6ff2470
Original remote handoff baseline: b03a38b
Platform:   原生 Windows 11 x64 + NVIDIA CUDA；owner-only 安装态验收基本完成，明确的人耳/语音抢话门仍开放
```

`6ff2470` 是最终安装候选包含的产品代码点；其后的测试与文档提交不改变安装载荷，最终远端同步
仍应以当前分支最新 HEAD 为准。源代码工作区不需要从 macOS 复制完整工程目录。Windows 机器应从 Git 干净 clone；macOS
的 `.venv`、`.local/envs`、`node_modules`、`target` 和构建缓存不可复用。

### 当前产品能力

- Web 与 Desktop 是一个仓库、一个产品内核、两个独立构建 profile。
- Tauri 桌宠、独立设置窗口、Live2D、文本/语音会话、播放 ACK、打断和设备恢复已经连通。
- Character Kernel、结构化记忆、分角色模型路由、Runtime Skills/MCP、权限确认和插件隔离已经落地。
- TTS 共用统一 Provider contract；本地 Qwen3-TTS、GPT-SoVITS 与百炼 Qwen/CosyVoice 可配置切换。
- Windows 基础安装包、冻结 Runtime、AppContainer helper 和 `.cwpack` Worker Pack 边界已经实现。
- 桌宠透明空白区域可穿透点击；Live2D 命中区域、字幕、输入框和悬浮控件仍可交互。
- 原生微信 iLink 扫码接入已进入该分支，但它不属于本次 CUDA 验收的阻塞项。

### 2026-09-01 原生 Windows x64/CUDA 实机结果

下面严格区分机器自动化、Codex Computer Use 的真实窗口观察和仍需要用户确认的项目。私有模型、
Live2D、生成音频、密钥、数据库和本机资产路径均不进入 Git。

#### 自动化与进程级验证

- Windows 11 Pro 25H2 build 26200.9168，原生 AMD64，Ryzen 9 7900X；PowerShell 7.6.4 x64。
- NVIDIA GeForce RTX 3090（24 GiB、compute capability 8.6），驱动 616.56；`nvidia-smi`
  报告 CUDA 13.4，直接加载 `nvcuda.dll` 得到 Driver API 13.4。WebView2 为 152.0.4191.53 x64。
- Git 2.45.1.windows.1、uv 0.12.7 x86_64、Node 23.9.0、pnpm 11.19.0、Rust 1.98.0；
  Python 3.12.10 为 win-amd64，Rust target 为 `x86_64-pc-windows-msvc`。
- Qwen3-TTS pack 使用 Torch 2.7.1+cu126，`torch.cuda.is_available()` 为真，模型参数与 tensor
  实际位于 `cuda:0`。最终 post-inference integrity smoke 的首个中文受控推理为 15.130 秒、随后日文热态推理为 4.665 秒；两份 24 kHz WAV
  均非静音、无削波，取消、卸载与动态监听端口关闭通过。推理后 Torch allocated/reserved 分别约
  2.164/2.303 GB，安装态首次生成观察到整卡占用约增加 2.2 GiB。
- faster-whisper Base 固定 revision pack 在完全离线模型目录执行 CPU int8，21.455 秒日文样音
  在 0.863 秒内得到非空且合理的日文转写；取消、卸载与监听端口关闭通过。
- owner-only NSIS 候选已在当前非提权用户安装。Host、Frozen Runtime、AppContainer helper、
  294 个安装载荷原生文件及 552 个 pack EXE/DLL/PYD 均为 PE `0x8664`；唯一 `0x014c` 文件是
  NSIS 生成的 `uninstall.exe` stub。Runtime 和两个 Worker 使用动态回环端口。
- 最终候选补上 Tauri 官方 single-instance 插件：连续两次从开始菜单启动只保留一个产品 Host、
  一个 supervisor 和一个 Runtime，第二次启动会重新显示现有宁宁窗口，不会复制 CUDA/Worker 进程树。
- 自动化已验证开始菜单/桌面快捷方式、安装目录、Worker Pack receipt/selection、强制终止后的完整
  进程树与端口清理，以及重装后的设置、SQLite 数据、pack 和选择保留。最终哈希候选还真实完成了
  安装、健康启动、重复启动抑制、强退、卸载与两种注册表视图/快捷方式清理；正常托盘退出仍见下方。
- 安装态 Runtime API 证明已播放的中文/日文段落均收到 `stopped/ended` ACK，`played_pts_ms`
  等于各段 duration；后续键盘新回合取消旧 generation 后未观察到旧 generation 的迟到文本、音频或播放事件。
  实机还暴露了播放中连接麦克风会把同一 generation 尾段丢在 WAV/WebRTC 交界的问题；`f7bccde`
  将输出所有权延迟到下一 generation 再切换，并加入尾段不断流/下一轮 WebRTC 独占回归测试。
- 最终根目录 Python 门禁为 533 passed、5 个明确平台/显式探针 skip；Pyright 0、Ruff lint/format
  通过。Web 为 32 files/144 tests，workspace 共 183 tests，Tauri 为 34 tests，Clippy 通过。

| 产物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `ChatWaifu NEXT_0.2.0_x64-setup.exe` | 128,243,973 | `e8c7883eadc76a55aed45a3105aa19acb9ad981d4dc52c48d1984433caa5a063` |
| `chatwaifu-faster-whisper-base-cpu-int8-0.1.0.cwpack` | 250,542,825 | `86cf28dc4d07e32587c1be29751e11d5d682f0d461e0d808808b78d894bd4d96` |
| `chatwaifu-qwen3-tts-nene-cu126-0.1.0.cwpack` | 5,443,989,887 | `af33a0f7afb105eeacd6c7a7de7071819afbf4916ba5d85a11a7817f146c00e9` |

#### Computer Use 真实窗口观察

- 开发态真实 Tauri 桌宠透明显示并加载、动画化本地宁宁 Live2D；透明空白区域可穿透，人物、
  字幕、输入框和按钮仍可点击，拖动人物可移动原生窗口。设置窗口可以打开、排版和独立滚动正常。
- 从安装态快捷方式启动后，Runtime 最终进入 `ready`，设置页显示 Runtime、faster-whisper 与
  Qwen3-TTS CUDA provider 就绪；聊天、模型保存/测试和记忆中心均可操作。
- 安装态分别提交中文和日文消息，窗口显示逐步字幕/播放进度，同时进程级 GPU 采样确认真实 CUDA
  合成。旧回合最后一段播放在新回合提交前 1.502 秒已经停止，因此这两轮只能证明顺序播放，不能
  冒充播放中的 typed/voice barge-in。重装并重启后，先前写入的私有记忆标记仍能在记忆中心看到。
- 完整安装态两 pack 冷启动在本机既观察到约 151 秒，也观察到约 443 秒；后者主要消耗在每次启动
  对 Qwen 31,223 个文件/约 5.4 GB archive 展开内容做完整哈希。Web 等待预算已调整为 455 秒以覆盖
  native host 的 450 秒预算，并补上 ready 事件监听注册竞态测试。443 秒虽然仍在界内，但已经是明确的
  可用性风险，后续必须在不降低完整性校验的前提下做可证明的可信缓存/增量验证优化。

#### 尚未完成或不能由自动化宣称

- 应用内麦克风连接已成功，未出现可由 Codex确认的 Windows 隐私弹窗。一次真实输入产生了 2,920 ms /
  93,440-byte VAD utterance，并由安装态 faster-whisper 回填为 `明明也好 今天的延期不錯`；这证明采集、
  VAD 自动结束和 Worker 回填链路工作，但对目标句的识别质量不理想。最终一次抢话提示期间用户明确表示
  没有说话，因此播放中的真实语音 barge-in 不能默认写成已通过。
- 最终安装态播放协调回归实际记录：麦克风在本轮第 1 段播放中连接后，本轮 4/4 段继续由
  `audio_element` 顺序播放并全部收到 ended ACK；下一轮 4/4 段全部由 `webrtc` 独占并收到 ended ACK，
  各段 started/stopped 严格交替。它证明 transport handoff 和协议级不重叠，不等同于人耳抢话通过。
- CUDA 中文/日文样音与参考音均已通过扬声器发声，波形客观指标通过；但音色、爆音、截断、速度和
  与参考样音的主观比较必须由人耳确认，当前不能写成“人工听感通过”。
- 安装态播放 ACK 与 transport 级无重叠已由协议/Runtime 状态验证，但扬声器主观无重叠仍需要播放中抢话与人耳观察；安装态
  透明空白区域点击穿透也尚未与开发态证据分开复验。
- 自动化已证明最终候选卸载后程序目录、开始菜单/桌面快捷方式、标准卸载项和 manufacturer metadata
  删除且用户数据/两个 Worker Pack 保留；本轮最终状态已卸载。Computer Use 不能定位 Windows 通知区，
  因此没有把 Alt+F4 的“隐藏到托盘”误报成正常退出，托盘菜单正常退出仍需人工补验。长时间多轮语音
  压力、installed AppContainer/MCP profile/ACL reconciliation、签名和私有角色资产许可审查仍是发布门。
- Qwen Torch wrapper 当前先生成完整波形，不能宣称 Provider 原生首 chunk 流式。

本轮已落地的根因修复包括：拒绝重解析/重定向的 Worker Pack 安装根、使私有 Live2D staging
具备崩溃安全回滚、隔离 Windows 数据库恢复命名空间、修正安装根 lint 门、避免应用退出把 avatar
可见性持久化为隐藏、覆盖 CUDA 冷启动等待预算与 ready 监听竞态、清理 data-preserving NSIS 卸载
遗留的 manufacturer metadata、消除 Windows PDB 目标名碰撞，以及让 generation completion 成为
真正的最终事件屏障。此外，Runtime 现在把 Numba/native cache 放进每次启动的 owner lease，以 OS 文件锁
区分活跃与 crash-owned cache，只回收可证明失主且路径深度/重解析检查通过的 `launch-*`；安装 helper
只允许用相同 archive 对已验证为损坏的 exact version 做显式原子 repair；Demo LLM 不再把隐藏记忆
上下文/来源标签原样念出；播放协调器不会在同一 generation 中途切换到新建 WebRTC；Tauri 的
single-instance guard 会激活现有桌宠而不是复制整套 Runtime/Worker graph。不要通过关闭 smoke、
固定端口或 `sleep` 绕过剩余验收。

### Windows 干净接手命令

```powershell
git clone https://github.com/MuBai-He/ChatWaifu-NEXT.git
cd ChatWaifu-NEXT
git switch codex/windows-installer
git rev-parse --short HEAD  # 应为 6ff2470 或其后续提交

Set-ExecutionPolicy -Scope Process Bypass
.\tools\windows\bootstrap_x64.ps1
.\tools\windows\dev_x64.ps1
```

先用 `dev_x64.ps1` 验证窗口、Runtime 和本地资源，再构建发布形态。模型 pack 和安装包命令见
`docs/operations/windows-local-ai-worker-packs.md` 与 `docs/architecture/product-release-profiles.md`。

### 只单独转移这些本地私有资产

| 资产 | macOS 侧已存在的目录 | Windows 建议落点 | 约大小 |
| --- | --- | --- | ---: |
| 宁宁 Qwen3-TTS 原始 CustomVoice checkpoint | `ChatWaifu-Nene-Qwen3-TTS/20260825-155901/checkpoint-epoch-0` | `C:\models\nene-qwen3-tts\checkpoint-epoch-0` | 2.3 GB |
| 已适配的宁宁 Live2D 模型 | `apps/web/public/vendor/live2d/model` | clone 后同一仓库相对路径 | 46 MB |
| GPT-SoVITS 宁宁模型，可选 | `nene` | `C:\models\nene-gpt-sovits` | 313 MB |

不要复制整个 `.local`。其中包含 macOS/MLX 专用环境、缓存、历史音频、数据库和明文 secret 文件；
既不能在 Windows 运行，也不应作为机器迁移包。模型/API 密钥在 Windows 设置界面重新填写。当前
CUDA 验收只需要前两项；faster-whisper 由固定 revision 的 Windows builder 下载并封装。

Qwen 与 Whisper pack 的标准命令：

```powershell
.\tools\windows\build_qwen3_tts_worker_pack_x64.ps1 `
    -ModelSource C:\models\nene-qwen3-tts\checkpoint-epoch-0 `
    -Voice ayachi_nene_local `
    -PackVersion 0.1.0

.\tools\windows\build_faster_whisper_worker_pack_x64.ps1 `
    -SmokeWav C:\validation\speech.wav `
    -PackVersion 0.1.0
```

新 Codex 开始时先读取本节、`docs/implementation-status.yaml`、ADR 0027/0028 和上述两份 Windows
文档；先观察真实 Windows/CUDA 基线，再修改代码，不要根据 macOS 结果宣称 Windows 已通过。

---

## 1. 你的角色

你是 ChatWaifu Next 的实现代理。你的任务是按照既定架构，以小步、可测试、可审计的方式建立一个本地优先的实时 AI 角色运行时。

你不是在旧 ChatWaifu 上修补功能，也不是要一次性生成一个看似完整但无法运行的模板仓库。

优先级：

```text
正确的边界
  > 可取消和可恢复
  > 自动化测试
  > 可运行的纵向链路
  > 性能优化
  > 接入更多模型
```

---

## 2. 权威文档

发生冲突时按以下优先级处理：

1. 用户在当前任务中的最新明确要求。
2. `CHATWAIFU_NEXT_ARCHITECTURE.md` 中的架构不变量。
3. `CHATWAIFU_NEXT_IMPLEMENTATION_PLAN.md` 中的阶段顺序和验收门。
4. 已接受 ADR。
5. 当前实现。
6. 旧 ChatWaifu 和 Hikari 代码。

旧代码只能提供灵感，不是兼容规范。

---

## 3. 系统一句话定义

ChatWaifu Next 是：

> 一个使用 Pipecat 管理实时媒体、使用自有 Core 管理人格/记忆/技能、使用 React/Tauri/Live2D 提供桌面角色表现，并可在本地与云端模型间按能力路由的 AI 角色运行时。

---

## 4. 已确定技术选择

```text
Backend:
  Python 3.12
  uv
  FastAPI
  Pipecat
  Pydantic v2
  AnyIO
  SQLAlchemy 2
  Alembic
  SQLite WAL + FTS5

Frontend:
  React
  TypeScript
  Vite
  Zustand
  Zod
  Pipecat Client

Desktop:
  Tauri 2
  Rust stable
  Dual-window architecture

Avatar:
  Official Live2D Cubism SDK for Web
  Renderer adapter abstraction
  High-level AvatarCue

Skills:
  Agent Skills compatible SKILL.md
  chatwaifu.yaml extension
  MCP tools/resources
  Permission Broker

Models:
  Isolated worker processes
  Capability manifest
  HTTP control + WebSocket binary stream in v1

Testing:
  pytest
  Vitest
  Playwright
  fake providers before real models
```

不要在没有 ADR 的情况下替换这些核心选择。

---

## 5. 必须保持的系统不变量

1. 所有 assistant 输出都有 `generation_id`。
2. 已失效 generation 的迟到 token、音频和 Avatar Cue 一律丢弃。
3. 用户打断必须传播到 Pipecat、LLM、TTS、播放队列和 Avatar。
4. 记忆必须有来源事件。
5. 未实际播放的角色文字不得写入共同经历。
6. LLM 只能产生高层 `AvatarCue`，不能写 Live2D 参数。
7. Plugin 只能通过批准的接口访问工具和记忆。
8. Skills 渐进加载，不能每轮暴露全部工具。
9. Cloud Provider 不是 Session 真相源。
10. Runtime 主进程不加载重模型依赖。
11. 所有队列有上限。
12. 所有跨边界消息有 schema version。
13. 所有副作用有权限与审计。
14. Secret 不写日志、不提交 Git、不放 localStorage。
15. Runtime 启动不依赖固定端口。

---

## 6. 旧代码迁移指导

### 6.1 旧 ChatWaifu

不要复制：

- Vosk/LLM/VITS 同文件主循环。
- 输出 `output.wav` 后阻塞播放。
- 多语言复制脚本。
- 非正式 ChatGPT cookie/token 接口。

可迁移：

- 角色文本资产。
- 有许可证的参考声音。
- 发音和文本清洗经验。

### 6.2 Hikari

不要复制：

- `DATA:`、`QUESTION:`、`SAY:true/false`。
- 全局 `say` 和 `generating`。
- 固定端口表。
- 直接清理 `_queue`。
- async 中的 `time.sleep()`。
- 一个活动 Unity 连接。
- 巨型硬编码人格 Prompt。

可以重建：

- Process Supervisor。
- Worker Control UI。
- Streaming TTS。
- Interruption Coordinator。
- Avatar action capability manifest。
- Ambient state machine。
- Log stream。
- Legacy Unity adapter。

---

## 7. 工作方式

### 7.1 开始任务前

每次开始前：

1. 读取相关架构章节。
2. 检查 `docs/implementation-status.yaml`。
3. 检查已有 ADR。
4. 列出本次明确范围。
5. 列出不会做的内容。
6. 检查是否需要新 ADR。

### 7.2 实现顺序

```text
schema/test
  -> interface
  -> fake implementation
  -> production implementation
  -> failure tests
  -> integration test
  -> docs
```

### 7.3 结束任务时

输出：

- 修改文件。
- 设计决定。
- 测试命令和真实结果。
- 已知限制。
- 下一阶段阻塞项。
- 是否更新 ADR。

不要只说“已完成”。

---

## 8. 历史首轮任务范围（已完成，不再约束当前任务）

第一轮只能实现 Phase 0 和 Phase 1。不要接 Pipecat、Live2D、Tauri sidecar 或任何真实模型。

### 8.1 Phase 0 交付物

```text
Monorepo directory tree
Python/TS/Rust tooling
Makefile commands
CI workflows
README/CONTRIBUTING/SECURITY
10 ADR documents
implementation-status.yaml
```

### 8.2 Phase 1 交付物

```text
Pydantic protocol package
JSON Schema generator
Generated schemas
TypeScript protocol package
Runtime validation
Cross-language golden fixtures
Contract tests
```

### 8.3 第一轮禁止事项

- 不添加模型 SDK。
- 不下载模型。
- 不引入 Live2D Core。
- 不实现数据库业务表。
- 不实现 WebRTC。
- 不创建巨型空类。
- 不为未来微服务引入 Kafka、Redis、Postgres 或 Kubernetes。

---

## 9. 历史首轮建议命令

根据实际平台调整，但目标命令必须保持：

```bash
make bootstrap
make format
make lint
make typecheck
make generate-protocol
make test
make test-contract
```

所有命令必须在干净 clone 中可复现。

---

## 10. 协议实现要求

### 10.1 Python

包名：

```text
chatwaifu-protocol
import chatwaifu_protocol
```

建议布局：

```text
packages/protocol-python/
├── pyproject.toml
├── src/chatwaifu_protocol/
│   ├── __init__.py
│   ├── base.py
│   ├── events.py
│   ├── commands.py
│   ├── media.py
│   ├── session.py
│   ├── avatar.py
│   ├── skills.py
│   ├── memory.py
│   ├── models.py
│   ├── permissions.py
│   ├── errors.py
│   └── version.py
└── tests/
```

### 10.2 TypeScript

包名：

```text
@chatwaifu/protocol
```

布局：

```text
packages/protocol-typescript/
├── package.json
├── src/
│   ├── generated/
│   ├── parsers/
│   ├── index.ts
│   └── version.ts
└── tests/
```

### 10.3 Schema

输出：

```text
schemas/domain/v1/
```

协议生成必须 deterministic。CI 运行 generator 后检查 working tree 是否变化。

### 10.4 Event Type 命名

使用小写 dot namespace：

```text
user.speech_started
assistant.playback_stopped
skill.run_completed
```

不要使用 Python 类名作为 wire event type。

### 10.5 Payload

高价值事件优先定义强类型 payload，避免所有 payload 永远是 `dict[str, Any]`。

EventEnvelope 可泛型化：

```python
class EventEnvelope(BaseModel, Generic[PayloadT]):
    payload: PayloadT
```

Wire parser 根据 `event_type` 分发。

---

## 11. 错误模型

所有跨边界错误使用：

```python
class StructuredError(BaseModel):
    code: str
    message: str
    retryable: bool
    component: str
    details: dict[str, JsonValue]
    correlation_id: UUID | None
```

错误码示例：

```text
protocol.unsupported_version
session.invalid_transition
generation.not_active
worker.start_timeout
worker.out_of_memory
permission.denied
skill.activation_failed
memory.provenance_missing
avatar.capability_missing
```

禁止将 Python traceback 直接发给普通用户。内部日志可以关联 error ID。

---

## 12. 测试要求

### 12.1 测试命名

使用行为描述：

```text
test_rejects_audio_frame_for_invalid_generation
test_memory_proposal_requires_existing_source_event
it_drops_avatar_cue_when_generation_is_cancelled
```

### 12.2 不使用真实外部服务

单元和普通 E2E 不访问：

- OpenAI。
- Gemini。
- Hugging Face。
- ModelScope。
- Live2D 下载服务。
- 用户本地模型。

### 12.3 固定随机性

测试中的 UUID、时间和随机动作可注入 clock/id generator，避免 flaky test。

### 12.4 Async 测试

- 使用 AnyIO test backend。
- 每个异步测试有 timeout。
- 不用真实 sleep 等待竞态，使用事件或 fake clock。

---

## 13. 代码质量禁令

不得出现：

```python
except:
    pass
```

不得出现无限队列：

```python
asyncio.Queue()
```

必须给出 maxsize 和满载策略。

不得通过私有字段控制：

```python
queue._queue.clear()
```

不得让跨模块状态依赖：

```python
global is_speaking
```

不得在业务代码硬编码：

```python
ws://localhost:8765
```

不得在前端：

```typescript
const apiKey = localStorage.getItem("openai-key")
```

---

## 14. 依赖选择规则

添加依赖前回答：

1. 标准库或现有依赖能否完成？
2. 维护状态如何？
3. 许可证是否兼容？
4. 是否进入 Runtime 主依赖树？
5. 是否会带来模型框架冲突？
6. 是否可在 Windows/macOS/Linux 构建？
7. 是否需要 ADR？

重模型依赖只允许进入对应 Worker。

---

## 15. 需要新 ADR 的情况

以下变更必须先写 ADR：

- 更换 Pipecat。
- 更换 Tauri。
- 更换 Live2D 为 Unity 主路径。
- 更换 SQLite 为其他数据库。
- 将 Worker protocol 改为 gRPC。
- 引入外部分布式消息队列。
- 改变 memory truth source。
- 允许插件任意 UI JavaScript。
- 改变 cloud egress 默认策略。
- 改变 generation cancellation 语义。

---

## 16. Live2D 实现提醒

到 Phase 2 时：

- 使用官方 Web SDK 稳定版。
- CI 中使用 FakeAvatarRenderer。
- Live2D render loop 不进入 React state。
- `AvatarCue` 是业务边界。
- 口型使用实际播放音频。
- SDK/Core 许可和下载方式写清楚。
- 不把未知许可的模型资源提交到公开仓库。

---

## 17. Pipecat 实现提醒

到 Phase 5 时：

- 用 adapter 包住 Pipecat API。
- Domain Event 不直接继承 Pipecat Frame。
- SmallWebRTC 只是 transport 实现。
- 先做 loopback，再做 Fake Cascade。
- interruption 必须叠加 generation invalidation。
- 不把 Pipecat context 当长期记忆。

---

## 18. Memory 实现提醒

到 Phase 11 时：

- 原始事件是唯一真相源。
- LLM 只提交 proposal。
- Committer 确定性检查。
- 每条长期记忆有来源。
- 区分 generated、queued、played、spoken。
- 用户删除后同步 FTS 和向量索引。
- 多角色命名空间隔离。

---

## 19. Skill 实现提醒

到 Phase 10 时：

- 兼容 Agent Skills `SKILL.md`。
- `chatwaifu.yaml` 承担机器策略。
- MCP 是工具/资源协议，不是实时媒体协议。
- 每次只向模型投影 active skill 允许的工具。
- 副作用必须经过 Permission Broker。
- SkillResult 不直接生成最终音频。

---

## 20. 真实模型接入提醒

真实模型接入顺序：

```text
Fake external worker
  -> stable ASR baseline
  -> OpenAI-compatible text LLM
  -> GPT-SoVITS compatibility TTS
  -> Qwen3 ASR/TTS candidates
  -> Cloud Realtime
  -> Local Omni
```

每个模型适配器必须：

- 有 manifest。
- 有 health。
- 有 cancel。
- 有资源指标。
- 有 fixture 或 mock contract test。
- 有 license 字段。
- 不污染 Runtime 环境。

---

## 21. Codex 首次执行 Prompt

可以将下面内容作为第一次执行指令：

```text
你正在实现 ChatWaifu Next。先完整阅读根目录中的：
1. CODEX_HANDOFF.md
2. CHATWAIFU_NEXT_ARCHITECTURE.md
3. CHATWAIFU_NEXT_IMPLEMENTATION_PLAN.md

本轮只完成 Implementation Plan 的 Phase 0 和 Phase 1，不实现任何后续阶段。

要求：
- 从空仓库建立 Monorepo 工程地基。
- 建立 Python、TypeScript、Rust/Tauri 的基础质量门，但暂不实现 Tauri 业务。
- 创建并填写 10 个基础 ADR。
- 实现 versioned domain protocol 的 Python Pydantic 包。
- 生成 deterministic JSON Schema。
- 生成或实现 TypeScript 类型与运行时校验。
- 建立 Python 与 TypeScript 双向 golden contract tests。
- 所有 make 命令必须真实可运行。
- 不接模型、不下载模型、不接 Pipecat、不引入 Live2D Core、不实现数据库业务。
- 不使用 ad hoc 字符串协议。

工作步骤：
1. 先输出你理解的范围和文件计划。
2. 检查并创建目录。
3. 先写测试和 schema fixtures。
4. 完成实现。
5. 运行 make format、make lint、make typecheck、make test、make test-contract。
6. 修复所有错误。
7. 更新 docs/implementation-status.yaml。
8. 最后列出真实执行结果、修改文件、已知限制和下一阶段依赖。
```

---

## 22. Phase 2 Prompt

```text
基于已完成的 Phase 0 和 Phase 1，实现 Phase 2 Live2D Avatar Lab。

只实现 Avatar Lab，不接 Runtime、Pipecat 或真实模型。

要求：
- 建立 AvatarRenderer、FakeAvatarRenderer、AvatarController、CueScheduler。
- 使用官方 Live2D Cubism SDK for Web 的稳定版本，Core 通过 vendor 指南由开发者提供。
- CI 不依赖专有 Core。
- React 只发送高层 AvatarCue，60 FPS render loop 独立运行。
- 实现 listening、thinking、speaking、happy、curious、nod、wave、interrupt。
- 实现音频 analyser 口型 fallback 和本地 WAV 测试。
- 实现 hit test 到 AvatarInteractionEvent。
- 提供 Debug Panel、performance telemetry、Vitest 与 Playwright smoke test。
- 缺少 Live2D Core 时显示可操作错误，不崩溃。
```

---

## 23. Phase 4 Prompt

```text
实现 Phase 4 Runtime 基础内核。

要求：
- FastAPI Runtime。
- Pydantic Settings，TOML + env override，secret 脱敏。
- SQLite WAL、Alembic、events/outbox/sessions/turns/generations。
- append + outbox 同事务。
- AnyIO bounded EventHub。
- Session/Turn/Generation 状态机。
- health/version/config/session/event stream API。
- graceful shutdown。
- structlog 和 trace correlation。
- 并发 sequence、outbox recovery、非法状态转换、slow subscriber 测试。

不要实现 Pipecat、模型、Skills 或长期记忆 projection。
```

---

## 24. Phase 7 Prompt

```text
实现 Phase 7 端到端打断与 Generation 语义。

这是高风险阶段，请先写设计和 race test，再写实现。

要求：
- GenerationCancellation 和 InvalidationRegistry。
- InterruptionCoordinator。
- Pipecat、LLM、TTS、playback、avatar 并发取消。
- 前端 playback_started/progress/stopped/queue_cleared ACK。
- 使用实际播放范围提交 spoken text。
- 所有输出点丢弃 invalid generation 的迟到数据。
- 覆盖 late token、late audio、double interrupt、worker crash、session close 等竞态。
- 建立 1000 次 deterministic race simulation。
- 输出端到端打断延迟测试 artifact。

不得通过 queue 私有字段或全局 speaking 布尔量实现。
```

---

## 25. 最终提醒

这个项目的价值不在于“接了多少模型”，而在于它是否能长期保存以下边界：

```text
实时语音可以替换
模型可以替换
角色渲染器可以替换
插件可以安装和移除
记忆可以追溯和遗忘
人格不会随着后端切换而碎裂
```

任何让演示更快、但破坏这些边界的捷径，都应被拒绝。
