# ChatWaifu Next Codex 执行交接书

> 本文件可以直接作为 Codex 的项目级任务说明。  
> 必读顺序：本文件 -> `CHATWAIFU_NEXT_ARCHITECTURE.md` -> `CHATWAIFU_NEXT_IMPLEMENTATION_PLAN.md`。

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

## 8. 首轮任务范围

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

## 9. 首轮建议命令

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
