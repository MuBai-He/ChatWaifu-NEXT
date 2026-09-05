# ChatWaifu Next 详细实现方案与顺序表

> 配套架构：`docs/architecture/master-architecture.md`  
> 执行对象：Codex 与项目开发者  
> 原则：先协议、再假实现、再纵向链路、最后接真实模型  
> 工作量标记：S / M / L / XL，仅表示相对复杂度，不表示日历时间

---

## 0. 使用方式

Codex 每次只实现一个明确阶段或一个可验收子任务。不得一次性生成整个项目的伪代码骨架后宣称完成。

每个任务的固定循环：

```text
读取架构与相关 ADR
  -> 检查已有代码和测试
  -> 写/更新设计说明
  -> 先写失败测试
  -> 最小实现
  -> 运行 lint/typecheck/test
  -> 运行阶段验收脚本
  -> 更新 CHANGELOG 与阶段状态
```

阶段完成后必须存在：

- 可运行代码。
- 自动化测试。
- 明确失败路径。
- 文档。
- 结构化日志。
- 验收记录。

---

## 1. 总体实施顺序

| 阶段 | 名称                       | 核心产物                                            | 依赖   | 相对工作量 |
| ---: | -------------------------- | --------------------------------------------------- | ------ | ---------- |
|    0 | 仓库与工程地基             | Monorepo、CI、开发命令、ADR                         | 无     | M          |
|    1 | 协议与代码生成             | Event、Command、Audio、Avatar、Skill、Memory schema | 0      | L          |
|    2 | Live2D Avatar Lab          | 官方 Web SDK、Cue、口型假信号、动作分层             | 0,1    | L          |
|    3 | Tauri 桌面宿主             | 双窗口、托盘、Sidecar 管理、设置存储                | 2      | L          |
|    4 | Runtime 基础内核           | Config、EventHub、EventStore、Session 管理          | 0,1    | L          |
|    5 | Pipecat 实时传输           | SmallWebRTC、RTVI、麦克风与播放 loopback            | 3,4    | L          |
|    6 | Fake Cascade 垂直切片      | FakeASR→FakeLLM→FakeTTS→Live2D                      | 1-5    | L          |
|    7 | 打断与 Generation 语义     | 端到端取消、迟到帧丢弃、实际播放记录                | 6      | XL         |
|    8 | Model Worker 与 Supervisor | Worker RPC、健康、显存策略、崩溃回退                | 4,7    | XL         |
|    9 | 真实 Cascade 模型          | ASR、LLM、TTS Adapter 与基准                        | 8      | XL         |
|   10 | Skill 与 Plugin Runtime    | Agent Skills、MCP、权限、异步 Job                   | 4,7    | XL         |
|   11 | Memory Kernel              | Event projection、多类型记忆、检索、删除            | 4,7    | XL         |
|   12 | Character Kernel           | Persona、关系、情绪、Prompt Compiler、Cue Planner   | 10,11  | L          |
|   13 | Cloud Realtime             | OpenAI、Gemini Adapter、Shadow Transcript           | 7,8,12 | XL         |
|   14 | 双脑与 Local Omni          | delegate_reasoning、MiniCPM-o Adapter、fallback     | 10-13  | XL         |
|   15 | 主动行为与 Ambient         | Scheduler、quiet hours、场景事件、频控              | 10-14  | L          |
|   16 | 发布工程                   | 安装、更新、模型管理、签名、隐私审计                | 0-15   | XL         |

并行建议：

```text
Track A: Protocol + Runtime
Track B: React/Tauri/Live2D
Track C: Fake Providers + Test Harness
Track D: Model Worker Prototypes
```

只有在阶段 6 合流。真实模型不得阻塞 Track A 和 Track B。

---

# 阶段 0：仓库与工程地基

## 0.1 目标

建立一个所有后续模块都可依赖的干净 Monorepo。此阶段不接模型、不接麦克风、不实现业务。

## 0.2 创建目录

```text
apps/web
apps/desktop
services/runtime
packages/protocol-python
packages/protocol-typescript
packages/avatar-sdk
packages/character
packages/memory
packages/skills
packages/model-router
packages/plugin-sdk-python
packages/plugin-sdk-typescript
workers
adapters
skills/builtin
skills/examples
plugins/examples
characters/default
models/manifests
docs/adr
docs/protocols
docs/operations
tests/contract
tests/e2e
tests/latency
tests/fixtures
```

## 0.3 根配置

创建：

```text
.editorconfig
.gitattributes
.gitignore
.env.example
README.md
CONTRIBUTING.md
SECURITY.md
LICENSES.md
CHANGELOG.md
Makefile
justfile 或 taskfile，二选一
pyproject.toml
uv.lock
pnpm-workspace.yaml
package.json
pnpm-lock.yaml
Cargo.toml
rust-toolchain.toml
.pre-commit-config.yaml
```

## 0.4 标准命令

必须支持：

```bash
make bootstrap
make lint
make typecheck
make test
make test-contract
make test-e2e
make dev-runtime
make dev-web
make dev-desktop
make format
make clean
```

所有命令在 README 中说明。

## 0.5 Python 规则

- Python 3.12。
- `src/` layout。
- Ruff format + lint。
- Pyright strict 或接近 strict。
- pytest + pytest-asyncio/AnyIO。
- 不允许裸 `except:`。
- 公共接口有类型注解。
- 异步代码不得调用阻塞 I/O，除非通过线程池且有测试。

## 0.6 TypeScript 规则

- `strict: true`。
- 不允许公共协议出现 `any`。
- ESLint + Prettier。
- Vitest。
- React 组件与 Avatar Engine 分层。

## 0.7 Rust/Tauri 规则

- Rust stable pinned。
- `cargo fmt --check`。
- `cargo clippy -- -D warnings`。
- Tauri command 只做 OS 能力与 sidecar 管理，不承载业务人格逻辑。

## 0.8 CI

建立 GitHub Actions：

```text
ci-python.yml
ci-web.yml
ci-rust.yml
ci-contract.yml
ci-e2e-fake.yml
security.yml
```

基础矩阵：

- Linux 跑全部无 UI 测试。
- Windows 跑 Python、TS、Rust 构建。
- macOS 跑 Tauri compile smoke test。
- GPU 测试单独手动触发，不进入每次 PR。

## 0.9 ADR

创建并填写：

```text
0001-modular-monolith.md
0002-pipecat-media-plane.md
0003-domain-event-envelope.md
0004-generation-id-cancellation.md
0005-react-tauri-live2d.md
0006-worker-process-protocol.md
0007-agent-skills-and-mcp.md
0008-event-sourced-memory.md
0009-cloud-egress-policy.md
0010-sqlite-first.md
```

## 0.10 验收

```bash
make bootstrap
make lint
make typecheck
make test
```

全部通过。空仓库代码覆盖率不设目标，但 CI 必须真正执行而不是 echo。

### 阶段门

未满足以下条件不得进入阶段 1：

- Windows、Linux 构建通过。
- lockfile 已提交。
- README 可让新环境完成 bootstrap。
- 无 secret 或模型权重进入 Git。

---

# 阶段 1：协议与代码生成

## 1.1 目标

先定义系统语言。所有模块使用同一组领域协议，不再出现 `DATA:`、`SAY:true` 之类字符串拼接。

## 1.2 Python 包

在 `packages/protocol-python/src/chatwaifu_protocol/` 创建：

```text
base.py
events.py
commands.py
media.py
session.py
conversation.py
avatar.py
skills.py
memory.py
models.py
permissions.py
errors.py
version.py
```

## 1.3 协议模型

至少实现：

- `EventEnvelope`
- `CommandEnvelope`
- `AudioFrameHeader`
- `VideoFrameHeader`
- `SessionSnapshot`
- `TurnSnapshot`
- `GenerationSnapshot`
- `AvatarCue`
- `AvatarCapabilityManifest`
- `AvatarInteractionEvent`
- `SkillDefinition`
- `SkillRunSnapshot`
- `SkillResult`
- `MemoryRecord`
- `MemoryProposal`
- `MemoryContextPacket`
- `ModelManifest`
- `RouteDecision`
- `PermissionRequest`
- `PermissionDecision`
- `StructuredError`

## 1.4 JSON Schema

从 Pydantic 导出到：

```text
schemas/domain/*.schema.json
```

生成脚本：

```text
tools/generate_protocol_schemas.py
```

要求：

- 生成结果可重复。
- CI 检查生成文件与源码是否同步。
- schema 有 `$id`、title、version。

## 1.5 TypeScript 包

在 `packages/protocol-typescript`：

- 从 JSON Schema 生成 TypeScript 类型。
- 对 Runtime 输入使用 Zod 或等价运行时校验。
- 提供 `parseEventEnvelope()` 等入口。
- 禁止前端直接信任任意 JSON。

## 1.6 版本兼容

实现：

```python
class SchemaRegistry:
    def register(...): ...
    def parse(...): ...
    def upgrade(...): ...
```

第一版只需 `1.0`，但测试必须证明未知 major version 被拒绝，未知 optional 字段可忽略。

## 1.7 Contract Tests

测试至少包含：

- Python serialize -> TS parse。
- TS serialize -> Python parse。
- UUID、datetime、enum 一致。
- 不合法 payload 被拒绝。
- 未知 event type 被包装为 `UnknownEvent` 或明确拒绝。
- binary audio header roundtrip。

建立 golden fixtures：

```text
tests/fixtures/protocol/v1/*.json
```

## 1.8 验收

```bash
make generate-protocol
make test-contract
```

生成前后 Git diff 为空，Python 与 TS contract test 全过。

### 阶段门

任何跨进程或前后端消息在阶段 1 后不得新建独立 ad hoc JSON。

---

# 阶段 2：Live2D Avatar Lab

## 2.1 目标

在不依赖后端和模型的情况下，验证官方 Live2D Web SDK、动作分层、表情、点击、口型和资源生命周期。

## 2.2 应用

在 `apps/web/src/features/avatar-lab/` 创建独立路由：

```text
AvatarLabPage.tsx
AvatarViewport.tsx
AvatarDebugPanel.tsx
AudioDebugPanel.tsx
CueTimelinePanel.tsx
```

## 2.3 Avatar SDK

在 `packages/avatar-sdk/src/` 创建：

```text
renderer.ts
controller.ts
cue-scheduler.ts
capability-registry.ts
audio-clock.ts
lip-sync.ts
interaction.ts
telemetry.ts
live2d/
  live2d-renderer.ts
  live2d-model-loader.ts
  live2d-motion-layer.ts
  live2d-expression-mixer.ts
  live2d-gaze-controller.ts
  live2d-lip-sync-driver.ts
  live2d-hit-test.ts
```

## 2.4 SDK Vendor 管理

- 不把未允许再分发的 Live2D Core 随意提交到公开仓库。
- `vendor/live2d/README.md` 说明获取和放置方式。
- 开发脚本检测缺失 SDK 并给出清晰错误。
- CI 使用 mock renderer，不要求含专有 Core。

## 2.5 高层 Cue

Avatar Lab 可手动发送：

```text
state:listening
state:thinking
state:speaking
expression:happy
expression:curious
motion:nod
motion:wave
gaze:pointer
override:interrupt
```

## 2.6 Cue Scheduler

实现：

- priority。
- duration。
- interruptible。
- start anchor。
- 同层替换与跨层混合。
- generation invalidation。
- motion start/end callback。

## 2.7 口型实验

提供三种 Debug Source：

1. 正弦或随机 envelope。
2. 本地 WAV。
3. 麦克风 loopback。

验证：

- AudioContext 时间驱动。
- analyser fallback。
- MotionSync adapter 接口。
- stop 时嘴巴回 neutral。
- 切换音频时无累积 node 泄漏。

## 2.8 点击与命中

- 鼠标坐标转换到模型坐标。
- hit area 映射为语义事件。
- Debug Panel 显示命中区域。
- 高级参数不直接暴露给业务。

## 2.9 性能

采集：

- FPS。
- frame time。
- dropped frames。
- WebGL context loss。
- 内存快照。

## 2.10 测试

- `FakeAvatarRenderer` 单元测试。
- Cue scheduler deterministic test。
- 缺失 motion 时 fallback 到 idle。
- 重复 load/unload 50 次无资源残留。
- Playwright 截图与交互 smoke test。

## 2.11 验收场景

运行 `make dev-avatar-lab`，依次：

1. 加载模型。
2. listening。
3. thinking。
4. speaking + 口型。
5. happy + nod。
6. interrupt。
7. 回 idle。
8. 点击 head，UI 收到 `touched_head`。

### 阶段门

Avatar Lab 必须不依赖 Runtime 才可运行。React re-render 不能驱动 60 FPS 参数更新。

---

# 阶段 3：Tauri 桌面宿主

## 3.1 目标

构建双窗口桌面应用，并建立 sidecar 生命周期和本地设置通道。

## 3.2 窗口

### Overlay

- label: `avatar-overlay`
- transparent profile 可选。
- undecorated。
- always-on-top 可配置。
- skip taskbar 可配置。
- click-through 可切换。
- position/size 持久化。

### Control Center

- label: `control-center`
- 普通窗口。
- 打开设置、日志、记忆和插件页。

## 3.3 Tray

菜单：

```text
显示/隐藏角色
打开控制中心
开始/暂停会话
静音
切换透明区域穿透
退出
```

## 3.4 Sidecar Manager

Rust 层创建：

```text
src-tauri/src/sidecar.rs
src-tauri/src/runtime_health.rs
src-tauri/src/app_state.rs
```

接口：

```rust
start_runtime()
stop_runtime()
restart_runtime()
get_runtime_status()
read_runtime_bootstrap_info()
```

开发阶段 sidecar 可以指向 `uv run chatwaifu-runtime`。发布阶段支持冻结可执行文件。

## 3.5 Bootstrap Handshake

Runtime 启动后向 stdout 输出一行机器可读握手：

```json
{
  "type": "runtime.ready",
  "port": 43127,
  "auth_token": "ephemeral-token",
  "pid": 12345,
  "version": "0.1.0"
}
```

Tauri 读取后存入内存，不写磁盘日志。普通日志不得混用该握手格式，建议前缀或独立 IPC。

## 3.6 设置存储

Tauri 只保存 UI 与 OS 级设置：

- 窗口位置。
- 自启动。
- overlay 模式。
- 最近 Runtime 地址。

业务设置仍由 Runtime 管理。

## 3.7 安全

- Tauri command allowlist。
- CSP。
- 禁止任意 shell 执行。
- 外部链接由系统浏览器打开。
- sidecar 参数不接受未经校验的 UI 字符串。

## 3.8 测试

- Rust sidecar parser 单元测试。
- runtime 意外退出后状态更新。
- 双窗口打开/关闭。
- tray actions。
- Windows 与 macOS build smoke test。

## 3.9 验收

- 启动桌面应用自动启动 Fake Runtime。
- Overlay 和 Control Center 同时工作。
- Runtime crash 后显示 degraded 并可手动重启。
- 退出应用后无孤儿进程。

---

# 阶段 4：Runtime 基础内核

## 4.1 目标

实现不含真实媒体和模型的核心 Runtime：配置、事件、数据库、Session、health 和 API。

## 4.2 包结构

```text
services/runtime/src/chatwaifu_runtime/
├── main.py
├── bootstrap/
├── api/
├── config/
├── eventing/
├── persistence/
├── sessions/
├── scheduler/
├── supervisor/
└── observability/
```

## 4.3 启动顺序

```text
load config
  -> initialize logging
  -> open database
  -> run migrations
  -> create event store/outbox
  -> create event hub
  -> create session registry
  -> create scheduler
  -> start API
  -> emit runtime.ready
```

关闭顺序反向执行，并设置超时。

## 4.4 配置

实现 `Settings`：

- Pydantic Settings。
- TOML + env override。
- secret 字段标记。
- 配置热更新只允许白名单字段。
- schema 导出给前端。

## 4.5 Event Store

实现 SQLite：

```text
events
outbox
sessions
turns
generations
```

要求：

- append 与 outbox 同事务。
- session sequence 原子递增。
- event ID unique。
- read stream 分页。
- retention policy 先留接口。

## 4.6 Event Hub

实现：

- publish。
- subscribe。
- bounded queues。
- filter。
- shutdown。
- subscriber lag metrics。

## 4.7 Session Registry

接口：

```python
create_session()
get_session()
close_session()
transition_session()
create_turn()
commit_user_turn()
create_generation()
invalidate_generation()
```

状态转换必须由表驱动验证，非法转换抛结构化错误。

## 4.8 API

实现基础：

```text
GET /v1/runtime/health
GET /v1/runtime/version
GET /v1/config
PATCH /v1/config
POST /v1/sessions
GET /v1/sessions/{id}
DELETE /v1/sessions/{id}
WS /v1/events
```

## 4.9 Observability

- structlog JSON。
- request ID。
- OpenTelemetry trace 基础。
- Prometheus 或内建 metrics endpoint，选择一个并写 ADR。

## 4.10 测试

- 并发 append sequence。
- 非法 Session transition。
- outbox crash recovery。
- slow subscriber 不阻塞 critical path。
- config secret 不出现在序列化输出。
- graceful shutdown。

## 4.11 验收

使用 API 创建 session，订阅事件，进行状态转换，重启 Runtime 后仍可读取事件流。

---

# 阶段 5：Pipecat 实时传输

## 5.1 目标

先打通媒体管道，不接 ASR、LLM、TTS。验证浏览器/Tauri 麦克风、WebRTC、远端播放和控制事件。

## 5.2 Pipecat Adapter

```text
services/runtime/src/chatwaifu_runtime/realtime/pipecat/
├── transport_factory.py
├── session_pipeline.py
├── rtvi_adapter.py
├── processors/
│   ├── domain_event_bridge.py
│   ├── audio_probe.py
│   └── interruption_probe.py
└── tests/
```

## 5.3 SmallWebRTC

API：

```text
POST /v1/webrtc/offer
POST /v1/webrtc/ice
DELETE /v1/webrtc/sessions/{id}
```

实际接口按当前 Pipecat API 调整，但不得让前端依赖 Pipecat Python 内部类名。

## 5.4 Echo Pipeline

第一条 Pipeline：

```text
mic audio
  -> probe
  -> delay 50ms
  -> return audio
```

用于验证：

- 采样率。
- channels。
- chunk size。
- WebRTC reconnect。
- AudioContext 分析。

必须提供耳返开关，避免默认产生啸叫。

## 5.5 Device UI

Control Center：

- 输入设备列表。
- 输出设备列表。
- 音量计。
- 权限状态。
- 测试录音。
- 采样率与连接状态。

## 5.6 RTVI 映射

创建 `RtviDomainAdapter`：

- RTVI 事件转 Domain Event。
- Domain command 转 Pipecat control。
- 记录 schema version。

## 5.7 测试

- Fake transport。
- offer/answer。
- reconnect。
- permission denied。
- device disappeared。
- audio frame ordering。
- frontend AudioContext resume after autoplay restriction。

## 5.8 验收

桌面端能：

1. 选择麦克风。
2. 建立 WebRTC。
3. 显示音量。
4. 可选听到 loopback。
5. 断开后重连。
6. 前端能接收 Runtime 事件。

---

# 阶段 6：Fake Cascade 垂直切片

## 6.1 目标

不用 GPU 和外部 API，打通完整产品路径。

```text
Mic
 -> FakeASR
 -> Conversation Director
 -> FakeLLM
 -> FakeTTS
 -> WebRTC audio
 -> Live2D lip sync
 -> Event Store
```

## 6.2 Fake Providers

### FakeASR

- 根据音量或测试命令返回固定 partial/final。
- 支持可配置延迟。
- 支持错误和断开注入。

### FakeLLM

- token 流。
- punctuation segments。
- tool-call fixture。
- 无限流模式用于取消测试。

### FakeTTS

- 文本映射为不同频率音调或预生成 WAV。
- 输出 chunk。
- 提供 word boundary。
- 支持 cancel ACK。

## 6.3 Conversation Director v0

职责：

- 接收 `user.transcript_final`。
- 创建 User Turn。
- 创建 Generation。
- 调 FakeLLM。
- 按句分段给 FakeTTS。
- 产生 Avatar listening/thinking/speaking cue。
- 完成 generation。

不做 Skills、Memory 和复杂路由。

## 6.4 前端

- 实时 partial 字幕。
- final user message。
- assistant text delta。
- thinking/speaking 状态。
- 音频播放。
- Live2D 口型。
- interrupt 按钮。

## 6.5 E2E Harness

`tests/e2e/fake_conversation.spec.ts`：

- 启动 Runtime。
- 建 session。
- 注入 fake transcript。
- 检查 assistant text。
- 检查 audio started。
- 检查 Avatar cue。
- 检查 event DB。

## 6.6 验收

在无 GPU、无网络、无 API Key 环境完成 10 轮对话，事件顺序正确，前端无错误。

---

# 阶段 7：打断与 Generation 语义

## 7.1 目标

把旧 Hikari 的 `SAY:true/false` 思路重建为可靠的端到端取消系统。这是项目最重要的工程阶段之一。

## 7.2 CancellationToken

```python
class GenerationCancellation:
    generation_id: UUID
    requested_at: datetime | None
    reason: str | None

    async def request(...): ...
    async def wait(): ...
    def is_cancelled(): ...
```

每个 generation 创建 AnyIO cancel scope，但外部 Worker 还需显式 cancel RPC。

## 7.3 Invalidation Registry

即使 cancel 没有及时生效，所有输出点都检查：

```python
if not generation_registry.is_active(generation_id):
    drop(frame_or_event)
```

检查位置：

- LLM delta bridge。
- TTS enqueue。
- TTS chunk bridge。
- playback queue。
- frontend event reducer。
- Avatar cue scheduler。

## 7.4 Interruption Coordinator

```python
class InterruptionCoordinator:
    async def interrupt(
        self,
        session_id: UUID,
        reason: str,
        source: str,
    ) -> InterruptionReport: ...
```

并发执行：

- Pipecat interruption。
- LLM cancel。
- TTS cancel。
- audio queue clear。
- frontend stop command。
- avatar override cue。

收集每个 ACK 和延迟。

## 7.5 Playback ACK

前端必须回报：

```text
playback_started
playback_progress
playback_stopped
queue_cleared
```

`playback_progress` 至少含：

```text
generation_id
stream_id
played_pts_ms
buffered_ms
client_clock_ms
```

## 7.6 Spoken Text Commit

有 word boundary 时：

```text
played_pts_ms -> latest completed word boundary -> spoken_text
```

无 boundary 时：

- 每个 TTS segment 作为最小提交单元。
- 只有整个 segment 播放完才 commit。

## 7.7 自动打断

用户 speech started 时：

- 先判断是否允许 barge-in。
- current generation 可打断则调用 coordinator。
- 系统提示音等不可打断音频单独处理。

## 7.8 Race Tests

必须覆盖：

1. LLM cancel 后仍发一个 token。
2. TTS cancel 后仍发三个 chunk。
3. 前端 queue clear 迟到。
4. 新 generation 已开始，旧 chunk 到达。
5. 用户连续两次打断。
6. Worker 在 cancel 时崩溃。
7. Session 在 interrupt 中关闭。
8. Background Skill 不被误杀。

## 7.9 延迟测试

固定本地环境测：

```text
speech_started -> old audio silence
interrupt command -> queue cleared
cancel request -> TTS ack
```

将结果保存为测试 artifact。

## 7.10 验收

- P95 打断到静音达到架构目标或记录明确差距。
- 1000 次随机 race simulation 不出现旧音频复活。
- Event Store 中已生成、已排队、已播放文本明确区分。

### 阶段门

没有通过阶段 7，不得接云端 Realtime 或长期记忆。

---

# 阶段 8：Model Worker 与 Supervisor

## 8.1 目标

将重模型隔离到独立进程，建立统一能力、健康、加载、流式和取消协议。

## 8.2 Worker SDK

在 `packages/plugin-sdk-python` 之外单独建立：

```text
packages/model-worker-sdk-python/
```

内容：

```text
manifest.py
server.py
auth.py
jobs.py
streaming.py
health.py
metrics.py
errors.py
```

## 8.3 Worker Transport

第一版：

- HTTP JSON 控制。
- WebSocket binary 流。
- Loopback + ephemeral bearer token。
- 随机端口。

控制帧统一含：

```text
request_id
session_id
turn_id
generation_id
job_id
schema_version
```

## 8.4 Generic Fake Worker

先做一个外部进程 Fake Worker，证明：

- Runtime 启动。
- 握手。
- health。
- stream。
- cancel。
- crash/restart。

## 8.5 Supervisor 数据模型

```python
class WorkerInstance:
    instance_id
    manifest_id
    pid
    endpoint
    state
    loaded_model
    device
    last_health_at
    restart_count
```

状态：

```text
DISCOVERED
STARTING
READY
LOADING
LOADED
BUSY
DEGRADED
STOPPING
STOPPED
CRASHED
BACKOFF
```

## 8.6 Resource Manager

- 获取 GPU 列表和显存。
- 获取 RAM、磁盘。
- Manifest 估算。
- 防止超过预算。
- 允许用户 pin 模型。
- idle timeout 卸载。
- 不依赖 NVIDIA 时也能运行。

## 8.7 Health

Worker health 返回：

```json
{
  "status": "ready",
  "model_loaded": true,
  "queue_depth": 0,
  "device": "cuda:0",
  "memory": { "vram_used_mb": 4200 },
  "capabilities": ["tts.streaming", "tts.cancel"]
}
```

## 8.8 Crash Policy

- 指数退避。
- 最大连续重启次数。
- 熔断状态。
- 用户手动 reset。
- crash log 路径。

## 8.9 测试

- 非法 token。
- Worker 协议版本不兼容。
- 端口冲突。
- 启动超时。
- 健康检查超时。
- 模型加载 OOM。
- 流中崩溃。
- cancel 后正常复用 Worker。

## 8.10 验收

杀死 Fake Worker，Runtime 仍运行，UI 显示 crash，Supervisor 回退并重启。

---

# 阶段 9：真实 Cascade 模型

## 9.1 原则

每个真实模型 Adapter 都必须先通过统一 Worker contract。不得为了快速跑通而把模型 import 回 Runtime。

## 9.2 ASR Worker 顺序

推荐顺序：

1. `asr-faster-whisper` 或已有 FunASR adapter，作为稳定基线。
2. `asr-qwen3`，作为当前重点候选。
3. SenseVoice 情绪/事件辅助 adapter。

### ASR 输出

```text
partial transcript
final transcript
language
confidence if available
word timestamps if available
speech event metadata
```

### ASR 测试集

准备本地 fixture：

- 中文普通话。
- 天津口音。
- 日语。
- 英语。
- 中英混说。
- 键盘噪声。
- 音乐背景。
- 短促语气词。

不得将私人录音提交到公开仓库。

## 9.3 LLM Adapter

第一版统一 OpenAI-compatible：

```text
chat completions / responses-like internal abstraction
streaming text
structured tool calls
cancel
usage
```

支持：

- vLLM。
- llama.cpp server。
- Ollama adapter。
- 云端兼容接口。

业务层只依赖 `TextReasoningBackend`。

## 9.4 TTS Worker 顺序

推荐：

1. GPT-SoVITS 兼容 Worker，用于已有音色快速验证。
2. Qwen3-TTS Worker。
3. 另一模型作为 A/B 对照。

### TTS 输入

```text
text
language
voice_profile
style instructions
speed
pitch if supported
generation_id
segment_id
```

### TTS 输出

```text
audio chunk
sample rate
sequence
word/phoneme boundary if available
end-of-stream
cancel ack
```

## 9.5 Text Segmenter

不能只按中文标点 split。实现：

- 中英日标点。
- abbreviation。
- 数字和小数。
- URL。
- 最小/最大字符长度。
- 超时 flush。
- code block 和 markdown 简化。

Text Segmenter 必须独立测试。

## 9.6 Voice Profile

```yaml
id: hikari-voice
language: zh
backend_preferences:
  - qwen3-tts-0.6b-base
  - gpt-sovits-hikari
reference_audio: assets/voice/reference.wav
style_prompt: 温柔、自然、不过度播音腔
speed: 1.0
pronunciation_lexicon:
  ChatWaifu: chat waifu
```

## 9.7 Benchmark Harness

`tools/benchmark_speech_pipeline.py` 输出：

- ASR partial/final latency。
- LLM first token。
- TTS first chunk。
- end-to-first-audio。
- real-time factor。
- VRAM/RAM。
- cancel latency。

## 9.8 验收

- 完成中文 20 轮连续语音。
- 用户打断可靠。
- TTS 声线稳定。
- Worker 可卸载和重新加载。
- 无模型代码进入 Runtime 依赖树。

---

# 阶段 10：Skill 与 Plugin Runtime

## 10.1 目标

实现可发现、渐进加载、受权限控制、可取消、可后台运行的 Skills。

## 10.2 Skill Registry

扫描范围：

```text
builtin
user
project
plugin
```

优先级：

```text
project > user > plugin > builtin
```

同名冲突必须记录并在 UI 显示。

## 10.3 Validator

- 校验 Agent Skills frontmatter。
- 校验 `chatwaifu.yaml`。
- 校验文件大小和路径穿越。
- 禁止软链接逃逸 Skill root。
- SKILL.md 超长时警告。

## 10.4 Skill Router

第一版组合：

```text
intent keyword
+ metadata embedding
+ active context rules
+ optional selector LLM
```

只返回 Top-K。

建立 eval dataset：

```text
utterance
expected_skill
must_not_trigger
context
```

## 10.5 Skill Activation

`activate_skill(skill_id)`：

- 读取正文。
- 解析 references 清单。
- 获取允许工具。
- 创建隔离 SkillContext。
- 产生 activation event。

## 10.6 MCP Gateway

已实现：

- 官方 MCP SDK 的 stdio、Streamable HTTP 与兼容 SSE Host。
- SQLite 连接配置与独立只写 Bearer secret storage。
- Tools、Resources、Resource Templates、Prompts 分页发现与显式读取。
- 远程工具映射为 Runtime Skill，复用权限、确认、Schema、超时、取消和审计。
- OpenAI-compatible structured tool calls 通过确定性 Top-K Router 映射到同一 Runtime Skill Gateway；
  已连接 MCP tool 因此可由普通对话自然语言触发，但 resources/prompts 仍只允许显式读取。
- 每次连接前 DNS/SSRF 校验，禁止凭证 URL、重定向和系统代理继承。
- macOS Seatbelt、Linux bubblewrap 的强隔离；required 模式无后端时 fail closed。
- Runtime 自身在 loopback `/mcp` 公开受策略过滤的 Streamable HTTP Server。

仍然明确不在本阶段范围：MCP Apps、sampling、elicitation、filesystem roots、自动 Prompt 注入、
Windows AppContainer 和实时媒体经 MCP 传输。

## 10.7 Permission Broker

接口：

```python
check_permission(subject, permission, context)
request_confirmation(...)
record_grant(...)
revoke_grant(...)
```

Grant scope：

```text
once
session
character
plugin
always
```

敏感权限不能默认 always。

## 10.8 Job Runtime

SkillRun 和 ToolCall 持久化。支持：

- foreground/background。
- progress。
- cancellation。
- timeout。
- retry policy。
- idempotency key。
- confirmation wait。
- resume after restart，第一版只对声明支持的 Skill 开启。

## 10.9 第一个真实 Skill

推荐 `system-status`，因为无副作用且可测试：

```text
用户问：当前用的是哪个模型？本地模型是否在运行？
```

工具：

```text
runtime.get_health
runtime.get_active_route
runtime.get_worker_status
```

第二个 Skill 再做有副作用能力，例如媒体播放或提醒。

## 10.10 UI

Control Center：

- Skill list。
- 来源和版本。
- 权限。
- 最近运行。
- 日志。
- enable/disable。
- uninstall。

## 10.11 测试

- 恶意相对路径。
- 重名冲突。
- tool schema 不匹配。
- permission deny。
- confirmation timeout。
- MCP server crash。
- Skill cancel。
- background job 完成但用户在说话，不自动插话。

## 10.12 验收

- 安装一个示例 Plugin。
- 通过自然语言触发 Skill。
- 只加载对应 SKILL.md。
- 需要权限时前端确认。
- 可中途取消。
- 完成结果进入 UI 和会话。

---

# 阶段 11：Memory Kernel

## 11.1 目标

构建可追溯、多类型、可更正和可遗忘的记忆系统。

## 11.2 Migration

新增表：

```text
memories
memory_sources
memory_proposals
memory_embeddings
entities
entity_aliases
entity_edges
episodes
relationship_states
prospective_tasks
playback_segments
```

## 11.3 Projection Worker

监听：

```text
user.turn_committed
assistant.spoken_text_committed
tool.call_completed
skill.run_completed
avatar.interaction_received
```

不直接处理 partial 或未播放文本。

## 11.4 Episode Segmenter

第一版规则：

- Session close。
- 长时间间隔。
- 话题明显切换。
- Skill completion。
- 用户明确“这件事结束了”。

LLM 可建议边界，但规则层最终决定。

## 11.5 Candidate Extractor

输出固定 JSON schema。候选类型：

```text
semantic.fact
semantic.preference
episodic.shared_event
procedural.preference
relationship.signal
prospective.commitment
```

## 11.6 Entity Resolver

- alias table。
- user/character/system reserved entities。
- 同名不自动合并。
- 低置信度创建 unresolved entity。

## 11.7 Committer

确定性检查：

- source exists。
- namespace allowed。
- confidence threshold。
- duplicate。
- contradiction。
- valid time。
- sensitivity。
- explicit remember/forget override。

## 11.8 Retrieval

接口：

```python
retrieve_context(query, session_context, token_budget)
search_memories(query, filters, limit)
get_memory(id)
forget_memory(id)
export_memories(scope)
```

先实现 FTS + 简单 embedding adapter。图数据库不进入此阶段。

## 11.9 Memory UI

- 按类型、角色、时间、敏感度筛选。
- 查看来源事件。
- 编辑。
- 删除。
- 锁定为 Core Memory。
- 查看 supersede 链。
- 导出。

## 11.10 Privacy

- 敏感记忆默认不进入云端 Context Packet。
- Egress UI 显示本轮发出的记忆摘要。
- 记忆导出有明确范围。

## 11.11 测试数据集

至少包含：

- “我喜欢 A”后改成“不喜欢 A”。
- 临时计划和长期偏好区分。
- 玩笑、反问、引用他人观点。
- 角色回答被中途打断。
- 用户明确要求记住。
- 用户要求忘记。
- 不同角色的秘密隔离。
- 日期变更。

## 11.12 评估指标

```text
proposal precision
proposal recall
duplicate rate
contradiction resolution accuracy
false recall rate
provenance coverage
forget compliance
```

## 11.13 验收

连续会话后，用户可查看来源、纠正偏好、删除记忆。角色在新 Session 中准确召回被允许的信息，不能召回已删除内容。

---

# 阶段 12：Character Kernel

## 12.1 目标

将角色身份从模型 Prompt 和 Avatar 动作中独立出来。

## 12.2 Character Loader

加载并验证：

```text
character.yaml
persona.md
voice.yaml
avatar.yaml
relationship-policy.yaml
lexicon.yaml
```

## 12.3 Prompt Compiler

实现 token budget 分区：

```text
safety/product
persona
state
relationship
memory
skill
scene
conversation
```

当超预算时按策略裁剪，不允许直接截断 System 最前面。

## 12.4 Affect Reducer

输入：

- 用户语气信号。
- 对话事件。
- Skill 结果。
- 触摸事件。
- 时间衰减。

输出 `CharacterStateChanged`。

所有数值 clamp，更新有最大步长。

## 12.5 Relationship Reducer

- 事件权重。
- 置信度。
- 更新速率。
- 衰减。
- 用户边界。

关系状态不能由一句模型输出直接跳变。

## 12.6 Response Planner

产生：

```text
response intent
speech style
whether to use skill
whether to delegate
avatar cues
memory context budget
```

## 12.7 Cue Planner

只从 Avatar Manifest 已声明能力中选择。不存在的动作回退到语义相近或 neutral。

## 12.8 多模型一致性测试

同一 persona/context 输入：

- 本地 LLM。
- 云端 LLM。
- Fake LLM。

比较：

- 自称。
- 称呼用户。
- 语气。
- 事实一致性。
- 不伪造记忆。

## 12.9 验收

切换模型后角色身份、关系信息和声音配置不丢失。模型不可直接写 Character Canon。

---

# 阶段 13：Cloud Realtime

## 13.1 目标

接入云端原生语音模型，同时保持 ChatWaifu 的领域状态、Skills、Memory 和 Avatar 独立。

## 13.2 Adapter 接口

实现：

```text
OpenAIRealtimeBackend
GeminiLiveBackend
```

具体模型名来自配置和 capability discovery，不写死在业务代码。

## 13.3 Session Mirroring

Realtime Provider 事件映射到：

- user transcript。
- assistant transcript。
- audio output。
- tool call。
- interruption。
- usage。
- error。

Provider-specific event 不泄漏到 Character、Memory 或 Frontend store。

## 13.4 Shadow Transcript

单独 ASR 或 provider transcription：

- 不阻塞原生 audio output。
- 标记 transcript source。
- provider transcript 与 shadow transcript 差异可记录。
- Memory 默认使用更可靠且已 commit 的版本。

## 13.5 Tool Bridge

- Provider tool call 已转入独立 AgentTurnOrchestrator，再经 Skill/Tool Gateway 执行；Provider
  不持有 MCP 或权限对象。
- 工具决策轮先缓冲，权限拒绝、过期、取消和失败作为结构化 tool result 回灌；只有禁用
  tools 的最终角色轮进入字幕与 TTS。
- SkillRun 持久化 turn/generation/provider call lineage，抢话会取消本轮前台 SkillRun，迟到结果
  由 CAS 终态和 generation gate 双重丢弃。
- 长任务返回 job handle，而不是阻塞 realtime socket。
- 不支持异步 function call 的 Provider 使用前脑确认 + 后脑 job。

## 13.6 Context Sync

Context patch 只发送必要信息：

- persona summary。
- current relationship summary。
- selected memory excerpts。
- active skill context。

每次 patch 记录 egress event。

## 13.7 Reconnect

- Provider session 断开后不尝试重播旧音频。
- 新建 Provider session。
- 从 Runtime snapshot 恢复必要上下文。
- 用户可继续，必要时退回 Cascade。

## 13.8 Voice Identity

UI 明确显示：

- 原生 provider voice。
- Character TTS voice。

若 provider 不支持角色自定义声线，不得在配置中伪装成同一声线。可以将其命名为“自然实时模式”。

## 13.9 测试

- Mock provider event stream。
- socket reconnect。
- tool call。
- user barge-in。
- provider late audio。
- usage and cost event。
- cloud egress deny。

## 13.10 验收

用户可在新会话选择 Cascade 或 Realtime。切换时 Memory 和 Character 保持一致，断网可退回本地。

---

# 阶段 14：双脑与 Local Omni

## 14.1 双脑 Job

实现 `delegate_reasoning`：

- Frontbrain 发起。
- Backbrain 在后台运行。
- 返回 progress。
- 用户可继续说话。
- 结果准备好后由 Director 决定何时表达。

## 14.2 Backbrain Context

只传：

- 明确任务。
- 所需记忆引用。
- 工具权限。
- 输出 schema。

不传整段 realtime raw audio。

## 14.3 Result Integration

如果用户话题已切换：

- 不立即打断。
- 放入 pending insight。
- 在合适时机提示，或显示 UI card。

## 14.4 Local Omni Worker

实现 MiniCPM-o Adapter：

- audio input stream。
- optional video frame stream。
- text/audio concurrent output。
- interruption。
- capability and resource reporting。

## 14.5 Omni Policy

第一版仅手动选择，不做全自动长期驻留。原因：

- 资源重。
- 输出稳定性需要实测。
- 全双工行为需要独立评估。

## 14.6 Fallback

```text
Local Omni failure
  -> Character Cascade local
  -> Character Cascade cloud text
  -> text-only mode
```

## 14.7 A/B Harness

同一组录音对比：

```text
Cascade
Cloud Realtime
Local Omni
```

记录：

- 首音。
- 打断。
- 自然度人工评分。
- 声线一致性。
- 工具成功率。
- 混合语言率。
- 记忆正确率。
- VRAM。

## 14.8 验收

本地 Omni 可独立启动和卸载，失败不影响 Runtime。双脑推理期间实时会话不冻结。

---

# 阶段 15：主动行为与 Ambient

## 15.1 目标

让角色能够基于事件、计划和环境主动说话，同时不成为桌面弹窗精灵。

## 15.2 Event Sources

- Prospective Memory。
- Skill completion。
- 日历或系统事件。
- 用户长时间沉默。
- Avatar touch。
- 可选摄像头或屏幕场景。

## 15.3 Proactive Policy

输入：

```text
quiet hours
last interruption
current user activity
importance
urgency
confidence
privacy
user preference
```

输出：

```text
speak_now
show_silent_card
defer
ignore
```

## 15.4 频率限制

- 每类主动行为独立 cooldown。
- 全局打扰预算。
- 被用户忽略后降低频率。
- 用户可完全关闭。

## 15.5 Ambient Skill

必须声明：

```yaml
ambient:
  enabled_by_default: false
  minimum_confidence: 0.9
  cooldown_seconds: 1800
  allowed_hours: ["09:00-23:00"]
```

## 15.6 Scene Privacy

- 摄像头和屏幕观察默认关闭。
- UI 有常驻指示灯。
- 原始帧默认不持久化。
- 场景摘要与图像分开权限。

## 15.7 测试

模拟一天事件流：

- 不重复提醒。
- quiet hours 不说话。
- 用户正在会议时静默。
- Skill completion 在用户说话时延后。
- 被拒绝后不立即再次询问。

## 15.8 验收

主动行为全部可追溯、可关闭、可延后，并符合频控。

---

# 阶段 16：发布工程

## 16.1 安装器

- Windows installer。
- macOS app bundle。
- Linux 作为后续 profile。
- 明确区分 overlay build 与 store-compatible build。

## 16.2 首次启动向导

```text
选择语言
选择角色
选择麦克风/扬声器
选择本地或云端模式
配置 API Key，存入 Keychain
下载可选模型
隐私与云端说明
语音测试
```

## 16.3 Model Manager

- catalog manifest。
- 下载进度。
- hash 校验。
- license 展示和确认。
- 磁盘空间检查。
- pause/resume。
- delete。
- version rollback。

## 16.4 更新

- App 更新。
- Runtime schema migration。
- Worker 更新。
- Model manifest 更新。
- Plugin update。

每项可独立回滚。

## 16.5 Crash Recovery

- 上次异常退出检测。
- 安全模式，不自动加载第三方插件和重模型。
- 导出诊断包，默认脱敏。

## 16.6 Security Review

- Tauri capability review。
- CSP。
- local auth。
- plugin path traversal。
- secret scanning。
- dependency audit。
- license inventory。
- model license review。
- Live2D SDK and asset distribution review。

## 16.7 发布验收

在干净机器上：

1. 安装。
2. 首次启动。
3. 下载或选择模型。
4. 完成本地 Fake/真实基础对话。
5. 完成一次打断。
6. 安装示例 Skill。
7. 写入并删除一条记忆。
8. 关闭后无孤儿进程。
9. 卸载后保留/删除用户数据按选择执行。

---

# 17. 首条纵向切片的精确实现清单

阶段 0 到 7 的最终最小产品路径：

```text
1. Tauri 启动 Runtime sidecar
2. React 创建 Session
3. SmallWebRTC 建立麦克风和播放通道
4. Pipecat 产生 user speech events
5. FakeASR 产生 transcript
6. Director 创建 Turn 和 Generation
7. FakeLLM 流式输出
8. Segmenter 产生文本段
9. FakeTTS 产生 audio chunk + boundary
10. 客户端播放音频
11. Web Audio 驱动 Live2D 口型
12. AvatarCue 切换 listening/thinking/speaking
13. 用户开口触发 interruption
14. generation invalidation 丢弃迟到 chunk
15. 前端回报 playback stop
16. Runtime commit 实际 spoken text
17. 所有 critical event 写入 SQLite
```

这一切必须在无 GPU 和无云端 Key 环境工作。

---

# 18. 建议的 Issue/Epic 划分

## Epic A：Foundation

```text
A-001 Monorepo scaffold
A-002 Python quality gates
A-003 TypeScript quality gates
A-004 Tauri quality gates
A-005 CI matrices
A-006 ADR baseline
```

## Epic B：Contracts

```text
B-001 Event envelope
B-002 Command envelope
B-003 Session/Turn/Generation schemas
B-004 Audio frame schema
B-005 Avatar schemas
B-006 Skill schemas
B-007 Memory schemas
B-008 Model schemas
B-009 JSON schema generation
B-010 TS runtime validation
B-011 Cross-language golden tests
```

## Epic C：Avatar

```text
C-001 AvatarRenderer interface
C-002 Fake renderer
C-003 Live2D vendor bootstrap
C-004 Model loader
C-005 Cue scheduler
C-006 Layer mixer
C-007 Expression mixer
C-008 Motion callbacks
C-009 Gaze
C-010 Lip sync
C-011 Hit test
C-012 Telemetry
C-013 Avatar Lab
```

## Epic D：Runtime

```text
D-001 Config
D-002 Logging
D-003 Event store
D-004 Outbox
D-005 Event hub
D-006 Session registry
D-007 State machine
D-008 Control API
D-009 Event stream
D-010 Graceful shutdown
```

## Epic E：Realtime

```text
E-001 Pipecat transport factory
E-002 SmallWebRTC signaling
E-003 RTVI adapter
E-004 Audio probe
E-005 Device UI
E-006 Loopback pipeline
E-007 Reconnect
```

## Epic F：Conversation

```text
F-001 FakeASR
F-002 FakeLLM
F-003 FakeTTS
F-004 Director v0
F-005 Text segmenter
F-006 Generation registry
F-007 Interruption coordinator
F-008 Playback ACK
F-009 Spoken text commit
F-010 Race simulator
```

## Epic G：Workers

```text
G-001 Worker SDK
G-002 Worker auth
G-003 Worker stream framing
G-004 Supervisor
G-005 Resource manager
G-006 Crash policy
G-007 Model manifest registry
```

## Epic H：Skills

```text
H-001 Skill registry
H-002 Skill validator
H-003 Skill router
H-004 Skill activation
H-005 MCP gateway
H-006 Permission broker
H-007 Job runtime
H-008 Declarative UI cards
H-009 Skill eval harness
```

## Epic I：Memory

```text
I-001 Memory migrations
I-002 Projection worker
I-003 Episode segmenter
I-004 Candidate extractor
I-005 Entity resolver
I-006 Committer
I-007 FTS retrieval
I-008 Vector adapter
I-009 Context packager
I-010 Memory UI
I-011 Forget/export
I-012 Memory eval harness
```

## Epic J：Character

```text
J-001 Character loader
J-002 Prompt compiler
J-003 Affect reducer
J-004 Relationship reducer
J-005 Response planner
J-006 Cue planner
J-007 Voice profile
J-008 Cross-model consistency eval
```

---

# 19. Codex 提交规则

每个 commit：

- 只完成一个逻辑目标。
- 测试与实现同 commit。
- commit message 使用 Conventional Commits。

示例：

```text
feat(protocol): add versioned event envelope
feat(avatar): implement layered cue scheduler
fix(runtime): reject late audio from invalid generation
test(memory): cover superseded preference retrieval
docs(adr): choose loopback worker transport
```

Pull Request 模板必须包含：

```text
What changed
Why
Architecture impact
Security/privacy impact
Tests run
Manual verification
Known limitations
Screenshots/traces if UI or latency related
```

---

# 20. Definition of Done

任一 Issue 完成需满足：

- 代码可运行。
- 类型检查通过。
- lint 通过。
- 单元测试通过。
- 涉及协议则 contract test 通过。
- 涉及跨模块则 E2E 通过。
- 错误有结构化类型。
- 日志不含 secret。
- 有取消或 timeout。
- 文档更新。
- 不引入未解释的全局状态。
- 不引入无限队列。
- 不引入硬编码本地绝对路径。

---

# 21. 风险登记表

| 风险                     | 影响             | 预防措施                            | 触发后的处理                 |
| ------------------------ | ---------------- | ----------------------------------- | ---------------------------- |
| Pipecat API 升级         | 实时层破坏       | pin 版本、Adapter、contract test    | 独立升级 PR                  |
| Live2D 许可或分发限制    | 无法公开打包     | Vendor 隔离、发布前审查             | 提供用户自行放置 SDK 方案    |
| Tauri macOS 透明窗口限制 | App Store 不兼容 | 多发行 profile                      | Store build 关闭透明 overlay |
| 模型依赖冲突             | Runtime 无法安装 | 独立 Worker 环境                    | Worker 回退或禁用            |
| GPU OOM                  | 会话中断         | Resource Manager、懒加载            | 退回小模型/CPU/云端          |
| 打断竞态                 | 旧语音复活       | generation invalidation             | 强制新 Session 或清队列      |
| 记忆幻觉写入             | 角色长期误记     | proposal + provenance + committer   | UI 更正、supersede、评估集   |
| Skill 工具过多           | 模型选择混乱     | 渐进加载、Top-K                     | 限制 active tools            |
| 插件越权                 | 数据或系统风险   | Permission Broker、子进程、信任等级 | 禁用插件、撤销 grant         |
| 云端上下文泄漏           | 隐私风险         | Egress Policy、敏感过滤             | 阻断 provider、审计事件      |
| Realtime provider 断线   | 对话冻结         | Runtime 真相源、fallback            | 新建 session 或 Cascade      |
| 音频口型漂移             | 角色违和         | actual playback clock               | analyser fallback / resync   |
| 过度主动                 | 用户反感         | quiet hours、频控、默认关闭 ambient | 自动降低频率、全局关闭       |

---

# 22. 不允许 Codex 采用的捷径

1. 不得在 Runtime 中直接 import 所有模型库。
2. 不得把 `session_id`、`turn_id`、`generation_id` 省略成全局变量。
3. 不得用字符串前缀代替 schema。
4. 不得在 async function 中直接 `time.sleep()`。
5. 不得通过清理 `queue._queue` 实现取消。
6. 不得让 LLM 返回任意 Live2D 参数。
7. 不得让插件直接访问 SQLite 文件。
8. 不得把所有 Skills 和 Tools 放进每轮 Prompt。
9. 不得把生成文本当成已播放文本。
10. 不得使用无限队列。
11. 不得把密钥放入示例配置真实值。
12. 不得以 `except Exception: pass` 隐藏故障。
13. 不得在没有测试的情况下接入第二个真实模型。
14. 不得用浏览器 localStorage 保存 API Key。
15. 不得为了“先跑起来”绕过 Permission Broker。

---

# 23. 阶段状态文件

创建 `docs/implementation-status.yaml`：

```yaml
architecture_version: "1.0"
current_phase: 0

phases:
  0:
    status: not_started
    acceptance: []
  1:
    status: blocked
    blocked_by: [0]
  2:
    status: blocked
    blocked_by: [0, 1]
```

Codex 每完成阶段，更新：

- status。
- commit。
- tests。
- known gaps。
- benchmark artifact path。

不能只把 status 改为 done 而没有验收证据。

---

# 24. 首批执行顺序

Codex 首轮只执行：

```text
Phase 0.1 to 0.10
Phase 1.1 to 1.8
```

首轮完成后输出：

- 仓库树。
- 所有命令执行结果。
- 协议 schema 清单。
- Contract test 报告。
- 尚未实现项。

第二轮执行：

```text
Phase 2 Avatar Lab
Phase 4 Runtime foundation
```

这两条可并行。

第三轮：

```text
Phase 3 Tauri
Phase 5 Pipecat
Phase 6 Fake vertical slice
```

第四轮重点只做 Phase 7。打断系统通过后，再开始真实模型、Skills 和 Memory。

---

# 25. 最终验收路线

完整项目发布前必须按顺序通过：

```text
Contract Gate
  -> Avatar Gate
  -> Runtime Persistence Gate
  -> WebRTC Gate
  -> Fake Vertical Slice Gate
  -> Interruption Race Gate
  -> Worker Crash Gate
  -> Real Cascade Gate
  -> Skill Permission Gate
  -> Memory Provenance Gate
  -> Character Consistency Gate
  -> Realtime Fallback Gate
  -> Local Omni Gate
  -> Proactive Policy Gate
  -> Clean Machine Install Gate
```

任何一关失败，都不能通过 UI 演示掩盖。

## Phase 17.1B-2 — Native WeChat Typing

Smallest complete slice: opt-in typing while an admitted reply is generating or waiting between
bubbles, through the existing channel configuration and native iLink adapter. Keep typing transient
and independently cancellable; provider errors must not delay text. Excludes new channels, media,
proactive messages, and changes to canonical turns or durable delivery semantics.

- Verify the official Tencent getconfig/sendtyping wire contract and private ticket boundary.
- Implement one bounded per-connection worker with turn/generation identity, refresh, supersession,
  terminal cleanup, connection teardown, and restart reconciliation from durable reply state.
- Test missing/failed/hanging provider calls, in-flight cancellation, late completion, stale terminal
  events, feature disabled, and complete ingress-to-text delivery while typing is unavailable.
- Run protocol, Runtime, type/format/lint, and client builds; then verify visible typing, completion,
  bubble cadence, and interruption with the owner's real WeChat account before marking accepted.

## Phase 17.1C — Cross-channel Memory Provenance UI

Smallest complete slice: expose the channel provenance already persisted on `MemorySource` through a
friendly Memory Center projection. Show provider, direct/group conversation type, local receive time,
and at most one untrusted display label. Keep stable routing identifiers and unknown raw protocol values
out of the UI. Preserve the existing event/turn fallback for non-channel sources.

- Reuse the generated `MemorySource` contract; do not add API, schema, database, or retrieval changes.
- Keep display labels presentation-only, normalize whitespace, and rely on React text escaping.
- Test friendly WeChat rendering, label precedence, timestamp ordering, non-channel fallback, and
  suppression of connection, account, principal, conversation, sender, event, and turn identifiers.
- Run Web tests, formatting, lint, type checks, and the production build.
- Before marking accepted, create one real memory through the bound WeChat account and confirm its
  source in the native Memory Center without exposing routing keys.

This slice excludes new channels, group enablement, multiple human principals, memory extraction or
ranking changes, and changes to prompt provenance.

## Phase 17 roadmap continuity

The owner selected messaging behavior as the active product direction in the referenced conversations
[确认当前阶段17.1B-1](https://chatgpt.com/c/6a9b4752-75c4-83ee-afb7-f0ac2fdbb29f)
and [分支 · 查看Chatwaifunext内容](https://chatgpt.com/c/6a989a98-5ff4-83e8-80ad-3bf1a2b81233).
Phase 17.1C is an additional accepted memory-source UI slice, not completion of the entire Phase 17
roadmap. Phase 13.4A remains paused; its lifecycle backlog does not replace the remaining messaging work.

- **17.2 — Preset sticker outbound (macOS accepted, PR 20 pending merge):** local preset asset library and manual tags, Character
  ResponsePlan mapping, sticker selection, iLink image upload, and text plus image delivery parts.
  User-image learning is deferred to 17.3. Real macOS image receipt and stop-reply cancellation
  were accepted on 2026-09-05.
- **17.3 — Inbound images and dynamic sticker learning (next):** receive, understand, and accumulate user
  images/stickers with safe download/decryption/decoding, media limits, deduplication, OCR/VLM,
  embeddings, principal isolation, deletion, and privacy management.
- **17.4 — Shared jokes and adaptive recall:** shared-joke associations, usage history, and adaptive
  retrieval remain planned in the broader roadmap; detailed design is pending.

Completing the owner-direct macOS text slices does not claim group support, other installed platforms,
or the remaining image and sticker capabilities are accepted.
