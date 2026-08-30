# 二次开发指南

想换角色、接新的模型或增加一个 Skill，可以从对应模块下手，不必复制整套页面。所有扩展最终都沿着
下面这条路径进入 Runtime：

```text
Frontend → typed application protocol → services → domain ports → adapters / workers
```

第三方 SDK 对象、SQL row、Pipecat frame 和 Live2D 参数都不能穿过自己的 adapter 边界。

## 目录地图

| 路径                                | 责任                                                        |
| ----------------------------------- | ----------------------------------------------------------- |
| `apps/web/`                         | Web Galgame、Desktop Pet UI profile、设置与 Runtime clients |
| `apps/desktop/`                     | Tauri 窗口、托盘、系统偏好、Runtime sidecar 与安装器        |
| `services/runtime/`                 | 会话、Provider、Character Kernel、记忆、Skills/MCP、持久化  |
| `packages/protocol-python/`         | Python 拥有的跨端协议唯一源                                 |
| `packages/protocol-typescript/`     | 生成的 TS 类型与 Zod 边界 parser                            |
| `packages/avatar-sdk/`              | 语义 cue 调度、动作状态机、口型与 renderer contract         |
| `packages/model-worker-sdk-python/` | 模型 Worker DTO、PCM v2 与 `.cwpack` 合约                   |
| `workers/`                          | 隔离的 STT/TTS 模型进程                                     |
| `characters/`                       | 可审计角色包，不含权重与私有媒体                            |
| `skills/`                           | 产品 Runtime Skills；不是 Codex 开发 Skill                  |

## 新角色

复制 `characters/default/` 为 `characters/<character_id>/`，让目录名与 `character.yaml` 中 ID 一致：

```text
character.yaml
persona.md
voice.yaml
avatar.yaml
relationship-policy.yaml
lexicon.yaml
```

- Persona 描述角色规则，不复制原作长对白。
- `voice.yaml` 只放逻辑 voice profile，不放参考 WAV、权重、Key 或本机绝对路径。
- `avatar.yaml` 描述语义能力，不暴露模型参数 ID 给 LLM。
- 关系由 reducer 管理，不能让模型随口改数据库状态。
- `content_notice`、来源和许可必须与实际资产权利一致。

严格加载入口在 `services/runtime/src/chatwaifu_runtime/characters/service.py`；Character Kernel 和 Prompt
预算分别位于 `character_kernel/service.py`、`character_kernel/prompt.py`。

## 新模型 Provider

兼容 OpenAI Chat Completions/Embeddings 的服务通常无需写 adapter：在设置里为四个模型角色分别填写即可。

新增协议时：

1. 在 `services/runtime/src/chatwaifu_runtime/providers/contracts.py` 实现领域接口。
2. 把供应商 SDK、鉴权、请求和错误留在 `providers/` adapter。
3. 在 model config/composition 注册非密钥配置。
4. 覆盖 delta、tool call、timeout、cancel、错误归一化和 Key 不回显测试。
5. 不允许 React 直接 import SDK 或调用供应商 URL。

新增云 TTS 从 `providers/tts_registry.py` 注册一个 `TtsProviderRegistration`，让同一份定义拥有配置
类型、adapter factory、UI fields、credential 与展示信息；不要再复制专属路由和设置页。

## 新本地 STT/TTS Worker

重模型必须留在 Runtime 之外：

1. 复用 `packages/model-worker-sdk-python` 的 health/capabilities/job/PCM contract。
2. 在 `workers/` 建独立环境与锁文件。
3. 透传 `session_id`、`turn_id`、`generation_id`、`job_id` 与 sequence。
4. 实现取消、超时、late chunk 丢弃、卸载与端口关闭。
5. 用真实音频做 smoke；“产生了 WAV”不等于端到端播放通过。
6. Windows 可分发能力使用 checksummed `.cwpack`、离线模型、x64 PE 检查和目标机推理。

只有底层模型在推理期间持续产出 PCM 时才能声明 `native_streaming=true`。完整 WAV 事后切片是兼容层，
不是原生流式。

## 新 Avatar renderer 或动作

Agent/Runtime 只产生版本化 `AvatarCue`：emotion、expression、motion、gaze、lipsync 等语义意图。
`packages/avatar-sdk/` 负责调度与降级，具体 Live2D parameter、motion 文件和 hit area 只存在 renderer
adapter。新增动作需要能力 manifest、优先级/持续时间、取消与未知能力回退测试，不能让 LLM 输出资源 ID。

## 新记忆策略

领域入口在 `services/runtime/src/chatwaifu_runtime/memory/`；SQLite 实现在
`persistence/sqlite_memory_repository.py`。任何长期写入都必须经过 extraction、policy、dedup、
contradiction、provenance 与 privacy。模型只能提出候选，不能绕过策略直接写 SQL。

Embedding 索引是可重建投影，SQLite 结构化记录与来源事件仍是真相源。更换向量模型时必须按
fingerprint 重建，不允许把旧维度向量静默混入新索引。

## Runtime Skill 与 MCP

产品 Skill 位于 `skills/`，至少包含 `SKILL.md` 和 `chatwaifu.yaml`；可安装插件还需要 `plugin.json`。
manifest 必须声明稳定 ID/version、JSON Schema、side effect、permissions、confirmation、timeout 与
interruptibility。

外部 MCP 通过设置管理 stdio、Streamable HTTP 或兼容 SSE。Tools、Resources、Templates、Prompts
必须保留分页/大小限制、DNS 与重定向复验、只写 token；Tool 执行继续经过 immutable ExecutionPlan、
Permission Broker、审计与平台沙箱。`.agents/skills/` 只指导 Codex 开发，不会被产品 Runtime 加载。

## 新设置栏目

桌宠控制中心的唯一组合入口是：

```text
apps/web/src/features/desktop-settings/desktopSettingsRegistry.tsx
```

创建独立 section 后，注册 ID、文案、icon、surface、visibility/availability，复用共享 primitives 与
`useSettingsOperation`。不要给 `DesktopSettingsPage.tsx` 继续堆平台 switch，也不要在控制中心创建
第二条聊天/媒体会话。

## 协议与数据库

跨端协议以 Python 为源：

```bash
make generate-protocol
make check-generated
make test-contract
```

不要手改 `schemas/domain/v1/` 或生成的 TypeScript 文件。网络边界要真实做 Zod/schema parse，不能用
`as SomeType` 信任 JSON。SQLite schema 变更要使用原子、带 checksum 的 migration，并加入故障注入测试。

## 质量门

```bash
make format-check
make lint
make typecheck
make test
make check-generated
make build-web
make build-desktop-ui
```

根据改动增加 Runtime、Avatar、E2E、Windows 与真实媒体专项验收。架构或生命周期的大改动还要新增 ADR，
记录责任、排除项、失败行为、取消语义、可观测性、迁移方式与回滚边界。

## 二开发行清单

1. 在 `release/products.json` 分别维护 Web/Desktop 版本，保留一个 `main` 与两条 tag train。
2. 替换产品名、Tauri identifier、publisher、图标和角色声明；已有安装后不要随意改 identifier。
3. 选择仓库发行许可证，并逐项审查代码、字体、模型、声音、Cubism 与角色 IP。
4. 从全新 checkout 构建，确认不依赖 `.local/`、开发 venv、绝对路径或未跟踪资产。
5. 在目标 OS/硬件走完整安装、启动、交互、退出、升级/重装与卸载验收。
6. 扫描源码与产物，确认没有 Key、token、用户记忆、参考音频、模型权重、数据库或私有角色资产。
