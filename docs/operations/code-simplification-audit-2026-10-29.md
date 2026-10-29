# 代码精简与重构前审查（2026-10-29）

本轮是独立只读代码审查，未实施业务代码修复或重构。结论是：优先清理已经被替代的路径和多套返回协议，然后隔离来源回答实验，最后再整理大型协调器与页面状态。文件行数只用于定位热点，不作为删除理由。

> 上文及以下审查章节记录前置审查时的状态。用户随后授权执行核心三批，当前实施情况与新验证证据见文末“核心三批实施记录”。
>
> 2026-10-30 更新：离线文件已恢复，全量 Python 测试及 Web/desktop 前端构建补验通过，详见文末“阻塞恢复与补验”。

## 审查基线与范围

- 工作目录：`/Users/mubai/Desktop/CW2`；HEAD 为 `bbe7c237`。
- 基线包含当前未提交内容：48 个受跟踪文件修改、7 个未跟踪条目。Android、Knowledge、设置页正在变化，不能拿 HEAD 版本代替当前工作区。
- 按当前磁盘上的 `engineering-workflow`、`cw2-workflow` 及审查/重构/验证参考执行；结合 `project-architecture`、`memory-system`、`realtime-voice`、`test-and-eval` 的边界要求。前端部分属于源码结构审查，不是视觉验收。
- 阅读架构、实施计划和状态的相关章节，以及 ADR 0001、0008、0022、0053、0070、0071、0084 等相关决策。
- 对 Runtime、Web、Android、Avatar SDK、协议 Python 包和桌面 Rust 源码做规模与引用扫描；深入追踪渠道投递、来源回答、记忆索引、设置控制器、知识页和 Android 主 Activity。没有逐行审完全部仓库，也没有验证线上部署、真机媒体或模型回答质量。

## 按证据和收益排序的发现

### 1. 渠道事件兜底已经影响幂等性，应先单独修复

**分类：已复现的方法级行为问题；建议 P2。**

调用 `ExternalChannelService.cancel_remaining_delivery_parts()` 取消一个已经处于终态的投递时，SQLite 仓储返回 `applied=False, persisted_events=()`。服务层仅检查事件列表是否为空，随后走旧兜底分支：已经取消的计划会再次产生 `channel.delivery_plan_cancel_requested` 和 `channel.delivery_plan_cancelled`。

证据：

- `services/runtime/src/chatwaifu_runtime/persistence/sqlite_external_channels.py:2286`：终态分支返回未应用、无事件。
- `services/runtime/src/chatwaifu_runtime/external_channels/service.py:2157`：空事件列表触发服务层重新构造事件，没有检查 `applied`。
- `services/runtime/src/chatwaifu_runtime/eventing/publisher.py:15`：`emit()` 会追加持久事件，不只是输出日志。
- 隔离执行当前源码中的完整服务方法，注入上述仓储无操作结果，实际观察到两个新事件；没有连接数据库、渠道或真实账号。该实验不是完整 SQLite/HTTP 验收。

影响是重复取消污染事件记录；本轮没有证明重复发送用户消息。最小修复应明确“状态没有变化”和“事件未持久化”的区别，先为重复取消补充现有服务测试，再删除或约束兜底。原 `test_multipart_tail_cancellation_service_level` 只取消一次，不能覆盖此问题。

建议在 `services/runtime/tests/test_multipart_delivery_service.py` 扩展重复取消用例，同时检查终态不变、持久事件数不增加；必要时另覆盖已投递状态不会被错误记成已取消。不要把行为修复混在大规模文件拆分中。

### 2. 外部渠道仍维护多种内部返回协议，值得整体收口

**分类：确认存在的兼容复杂度。**

- `external_channels/ports.py:128` 的 `complete_turn()` 允许 `CompleteTurnResult | ChannelTurnRecord`。
- 当前生产组合使用 `SQLiteExternalChannelRepository(database, event_store)`，其 `complete_turn()` 已统一返回 `CompleteTurnResult`。
- `external_channels/models.py:283` 起的结果对象同时支持命名字段、元组访问、属性转发；`LeaseRecoveryResult` 还继承 `int`。
- `external_channels/service.py:2632` 继续判断返回类型，随后用 `getattr(..., "persisted_events", ())` 和重新造事件维持旧路径。领取、确认、取消也有事件兜底。

这让调用方必须理解结果对象的多套形态，也把事件的所有权同时放在仓储与服务层。建议先盘点所有直接属性、解包、下标及整数兼容调用，将内部消费者和测试统一迁移到显式结果字段；服务层只发布仓储已提交的事件。仓储无 EventStore 的测试场景也应明确定义，不能把空事件自动解释成“需要补发”。

这是内部契约整理，不需要同时改变对外 HTTP/协议 JSON。保留事务原子性、租约、重试、顺序投递、群成员权限和取消保护。验证复用 delivery repository、multipart service/scheduler、QQ delivery 和 failure notice 测试。

### 3. 已有可退役的旧索引重建路径，适合第一批精简

**分类：当前仓库未找到生产调用方的旧实现。**

`memory/service.py:972` 的 `reindex_all()` 仍按 `list_records(limit=500)` 取数据，通过 `getattr` 探测 `rebuild`，否则循环写索引并忽略单条异常。`memory/semantic_index.py:147` 保留其配套 `rebuild()`。

当前 API 已在 `api/routes.py:713` 和 `:720` 接到 `IndexRebuildService`，后者拥有分页、单任务执行、状态、模型切换和停止生命周期。仓库搜索未发现旧方法的实际生产调用；测试中的 `reindex_all` 仅被构造为 mock，并明确断言不调用。

建议删除旧 `reindex_all()` 和仅由其动态引用的 `rebuild()`，保留索引端口和新的重建协调器。实施前再做一次全仓引用/动态注册核对，以防审查后出现新消费者。不要宣称当前手动重建存在 500 条截断：该问题位于未使用的旧实现。

验证目标是现有 `test_index_rebuild_and_stale_vectors.py` 中分页超过管理页、删除不复活、模型切换取消和单任务执行等用例。

### 4. 来源回答实验仍穿过生产协调器，需要明确隔离边界

**分类：架构复杂度；是否退役属于产品/实验决策。**

- `bootstrap/container.py:191` 和 `conversation/service.py:139` 默认 `source_answer_frames=False`。
- 生产源码搜索没有找到将该开关启用的入口；测试显式启用。
- ADR 0070 是隔离实验，ADR 0071 是默认关闭的候选。不能据此声称来源回答功能已经验收或实验可以无条件删除。
- `AgentTurnOrchestrator.stream()` 为 594 行，既处理通用工具轮次、能力发现和写操作停止，也处理来源补读、质量纠正、帧生成和 fallback 标记。
- `conversation/service.py:1723` 附近还负责来源加载、覆盖状态、完成发布与实验回调；`_run_generation()` 为 353 行。

建议先将来源回答的决策与帧策略集中在一个明确的策略边界，让通用工具循环保留轮次、权限、取消和发布职责。实验的开关、准备、完成/取消状态应统一收口，不能只是把长函数机械搬到另一个文件。

范围必须区分：默认启用的来源上下文/普通来源核对仍有用途；默认关闭的是结构化覆盖候选。不能删除所有 `source_*` 文件，也不能为了变短放松来源、预算或保真要求。若决定退役候选，再按 ADR 0071 的退出路径删除相关挂钩，保留历史研究证据。

验证复用工具调用、source context、source frame/state、conversation source state 和 generation cancellation 测试；模型质量仍需要原有独立验收。

### 5. Memory 的 SQLite 适配器位置与架构检查范围不一致

**分类：边界归属问题与检查缺口，不是已证实的数据故障。**

`memory/semantic_index.py:15` 直接导入 `persistence.database.Database`，并执行 SQL。核心 `MemoryService` 已依赖 `SemanticMemoryIndex` 端口，因此不需要重新发明一套仓储抽象；问题是具体 SQLite 实现仍放在领域目录中。

`tools/check_architecture_boundaries.py:94` 当前仅检查 conversation/runtime_skills 的持久化隔离，漏掉 AGENTS 明确要求的 Memory。隔离输入实验中，向 Memory 放入直接 Database import，检查返回 0 个违规；同样输入放进 Conversation，返回 1 个违规。当前仓库的架构检查通过，并不能证明 Memory 边界全部合规。

建议在退役旧重建方法时，把具体适配器归入 persistence（或项目明确的 adapter 目录），更新组合根/测试 import，保留现有端口，再补上 Memory 检查。这里收益是缩小未来依赖扩散的空间，代码行数不会明显下降。

### 6. 有两组真正重复的映射，以及少量设置兼容导出

**分类：低风险行为保持型整理。**

AST 函数体比较确认以下两组实现相同，已排除只有省略号的 Protocol 假重复：

- `persistence/sqlite_photo_memory.py:195` 与 `persistence/sqlite_photo_semantic.py:323` 的 SavedPhoto 行映射。
- `external_channels/service.py:2860` 与 `external_channels/groups.py:1898` 的 ChannelTurnSnapshot 映射。

各自合并到原有责任域内的单一映射函数即可，不需要通用转换框架。复用照片存储/语义检索及渠道快照测试。不能把照片元数据的兼容默认值顺带删除。

`desktop-settings` 的多个文件只有一条 re-export，并不是第二套设置实现。源码 import 扫描中，`SettingsIcon.tsx`、`mcp/mcpConnectionPolicy.ts`、`mcp/useMcpConnectionsController.ts` 的旧导出路径没有消费者；部分其它导出只剩测试或 desktop registry 消费者。可以统一真实 import 后删除空壳，但收益小，不应成为此次重构的主项目。删除前检查构建入口、动态引用和测试配置；本轮未执行删除。

### 7. UI 应按状态生命周期整理；组合根和实时协调器后置

**分类：有源码依据的重构热点，不作为功能缺陷报告。**

| 热点                            |                      当前规模 | 应减少的复杂度                                                                                    |
| ------------------------------- | ----------------------------: | ------------------------------------------------------------------------------------------------- |
| Android `MainActivity.java`     |                      2,611 行 | 收件箱、会话选择、AI 建议、设置、后台读取共同持有 Activity 字段；应按页面拥有状态、任务和退出清理 |
| Web `KnowledgePage.tsx`         | 1,302 行、29 个 useState 声明 | 图谱/列表/问答/冲突与详情编辑共用一处状态；先统一详情选择与编辑会话，再分离各 tab 的请求生命周期  |
| `bootstrap/container.py`        |               构造函数 525 行 | 分组装配可读性与明确清理所有权；不要引入新的通用 DI 框架                                          |
| `realtime/cloud/coordinator.py` |     `dispatch_event()` 372 行 | 按事件责任整理分派，但保留身份解析、取消、ACK 和迟到输出边界                                      |

当前未提交改动已经增加 `KnowledgeDetail`、`SourceReference`、`SuggestionReview` 等分离点。下一步应沿着这些真实职责继续收口；只抽出 JSX/布局而把全部状态和分支留在父级，收益有限。

Knowledge 的 policy epoch、请求序列、草稿丢弃确认和同步操作锁属于不同保护。Android 的 generation/读取序列、页面退出、发送前策略检查也不能被一个泛化的 busy 标志替代。UI 结构改动需要相应交互与渲染验证；本轮没有截图、浏览器布局或真机验证。

## 明确保留的机制

- `useSettingsOperation` 的 ref 同步互斥与 state 渲染忙状态：保护不同时刻；MCP 的选择 epoch 还防止旧请求覆盖新连接草稿。
- generation/session/turn 标识、各异步边界的 stale 检查、取消传播和有界队列。
- SQLite 历史迁移及 checksum；`migrations.py` 很长不代表可以压缩或改写历史 SQL。
- Android 独立 OpenAI 兼容模式：`AnalysisRouting` 有真实入口，`docs/plans/mobile-information-backend.md:20` 明确保留为默认关闭的高级选项。删除属于产品变化。
- Web/desktop 产品入口隔离、权限确认、删除/遗忘语义、来源引用以及 Runtime Skills 的执行边界。

## 建议实施顺序与完成条件

1. **渠道幂等性修复**：在现有 multipart 服务测试加入重复取消断言，修复空事件兜底。完成条件是无操作不增加事实事件，投递状态与已有发送行为不变。
2. **小范围行为保持精简**：旧索引重建路径、确定的重复映射、无消费者的导出；更新消费者后跑相关现有测试。不额外建设测试框架。
3. **渠道内部结果协议统一**：迁移仓储、服务、工具消费者及测试，删除多形态访问/重复事件构造；保持对外协议和数据库结构。
4. **来源回答策略隔离**：先确定候选保留还是退役，再缩小 Agent 与 Conversation 的分支范围；不得扩大功能或改回答标准。
5. **逐个 UI 状态域整理**：Knowledge 与 Android 分开提交，沿当前未提交改动整合；复用现有交互测试，补充具体缺失的生命周期案例。
6. **最后整理组合根、Cloud Realtime 与大 SQL 文件**：只有能减少依赖、状态或双重所有权时才做，不设置“每文件最多多少行”的指标。

每个切片分别记录保留行为、故意修复的行为、实际 diff 与现有验证结果。避免一次改渠道、记忆、语音和 UI 导致回归归因困难。

## 本轮验证记录与限制

| 检查                                                         | 结果与证明范围                                                                                                                           |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 当前工作区架构检查脚本                                       | 通过；仅代表脚本当前覆盖的规则                                                                                                           |
| 设置操作、MCP 控制器、MCP 面板 3 个现有测试文件              | 20 项通过；Vitest 4.1.11，未验证视觉布局或原生环境                                                                                       |
| 取消重复事件隔离实验                                         | 执行当前服务方法、仓储返回合成 no-op，观察到 2 个额外事件；不是 SQLite/HTTP 集成测试                                                     |
| Memory 边界检查对照                                          | Memory 直接 DB import 违规数为 0；Conversation 对照为 1                                                                                  |
| Python 架构/索引/取消/来源状态现有测试                       | 未进入测试执行。pytest 导入阶段阻塞，已中止；检查发现 `.venv` 中 pytest 文件带 macOS `dataless` 标记。不能报告这些测试通过或代码测试失败 |
| 全量测试、Web/desktop 构建、Android 构建、模型/设备/生产验收 | 本轮未执行                                                                                                                               |

验证命令最终通过现有 `node .../vitest.mjs` 运行。最初的 pnpm 命令触发了自动依赖刷新，已停止该入口并改为直接运行；本地 node_modules 有缓存重建，Git 中没有新增 package/lockfile 修改。`uv run --no-sync` 入口也因无进展被停止，没有另建或修复 Python 环境。

取消方法实验对应 `external_channels/service.py` SHA-256：`2fb0e7b13d922ad8043b1db27cabb99ec13dc51c78fc32e951c129e3bc56a81f`。实验没有新增永久测试文件、数据库或运行服务。业务源码未修改；本轮产物为本报告。

## 核心三批实施记录

用户选择核心清理三批，并明确由 Astra 独立执行。本次实施不涉及来源回答候选、UI 状态重构、Memory 适配器搬迁或 Cloud Realtime 整理。

### 已实施变化

1. 在现有 multipart 服务测试中增加重复取消和已投递后取消的持久事件断言。真实临时 SQLite 的重复取消用例在修复前失败，明确多出两条事件；修复后通过。渠道服务现只发布仓储返回的持久事件，空列表表示无事件，不再触发兜底构造。
2. 删除旧 MemoryService.reindex_all 及其专用索引 rebuild；新分页重建协调器保持不变。照片行映射归入 persistence/photo_mapping.py，渠道快照归入 external_channels/snapshots.py；AST 对照证明两个共享函数体与原实现完全一致。删除三个无消费者的 desktop-settings 导出文件。
3. complete_turn/create_delivery_plan 端口统一返回显式结果，删除 CompleteTurnResult/DeliveryTransitionResult 的元组及属性代理，LeaseRecoveryResult 改为含 count 的不可变数据类。迁移服务、语音/文件工具、恢复调度及测试调用方，删除旧事件构造函数。原有状态、租约、顺序和回滚断言保留。

生产组合根继续向 SQLite 渠道仓储注入 EventStore；端口文档明确事务与事件所有权。无 EventStore 的独立仓储测试仍允许执行，但服务不再补造事件。对外 HTTP/协议字段、事件名称和数据库迁移均未更改。

### 验证环境与阶段证据

- 按 uv.lock 使用 Python 3.12.14、隔离环境 `/tmp/cw2-refactor-env` 和 knowledge 可选依赖；原项目 `.venv` 保留。新解释器安装在 `/tmp/cw2-refactor-python`。
- 进一步定位到旧 pytest 缓存读取阻塞；运行时设置 `PYTHONPYCACHEPREFIX=/tmp/cw2-refactor-pycache`、`-o cache_dir=/tmp/cw2-pytest-cache`，未清理用户缓存。
- 第一、二批相关既有测试 55 项通过。
- 扩大渠道检查首次暴露旧结果访问仍在测试中使用，已逐项改为命名字段，保留业务断言；这些失败属于迁移未完成时的中间状态。
- Web TypeScript 检查及设置/MCP 三文件 20 项测试通过。
- Python 严格类型检查 599 个文件零错误；隔离配置复用项目规则，并显式提供原项目与测试/工具目录的模块搜索路径。
- 改动 Python 文件 Ruff lint/format、架构检查、git diff --check 通过；最终严格类型检查仍为 599 文件零错误。全量检查未全部完成，限制见下述记录。
- 迁移完成后的核心仓储/服务 19 项测试通过。另清理照片语义测试中已删除 reindex_all 的 mock，改为验证保存模型路由只通知失效、不自动启动新重建；该文件 21 项通过。代码与测试净减少 332 行（包含两个新增共享映射模块，不计文档）。
- 全量运行期间 Word 真集成用例在文档子进程中超时。复核发现子进程过滤环境变量，不继承 PYTHONPYCACHEPREFIX；仅在隔离环境添加 sitecustomize 指向临时字节码缓存后，该同一用例独立重测 1 项通过，耗时 4.08 秒。没有修改文档业务代码或放宽超时。
- Web 构建完成 2159 个模块转换后，阻塞于复制 `public/vendor/live2d/framework/Shaders/WebGL/fragshadersrcalphablend.frag`；lsof 与文件属性确认该输入带 dataless 标记。已终止本次构建，桌面构建尚未开始，不能记录为构建通过；原资源保留。
- 全量测试中止于 `test_settings.py::test_environment_overrides_toml`：默认配置读取根目录 `.env`，lsof 和属性确认该文件为 dataless。未读取其内容、未删除或替换文件，该用例没有完成。
- 其余用例分段检查记录 893 passed、13 skipped，另有一个 Worker 进程租约测试失败。此失败由临时续跑脚本缺少 multiprocessing spawn 入口保护、子进程再次执行 pytest 引起；标准 `python -m pytest` 独立重测该用例 1 passed。该临时脚本已停用，不属于业务修复。
- 分段检查随后阻塞于训练打包工具复制 `tools/qwen3_tts_training/bundle_assets/scripts/__pycache__/materialize_dataset.cpython-313.pyc`；该离线缓存文件同样带 dataless 标记，已中止扩大检查，没有清理用户文件。因此不能宣称全量测试成功退出。

原有 Android、Knowledge、设置页面等未提交工作保留；没有提交、推送、部署或连接真实渠道。

### 最终验收结果

- 使用标准 `python -m pytest` 对渠道、投递/恢复、照片、表情和索引相关范围验证，**1364 passed**，正常退出，耗时 193.01 秒；该范围按全量收集清单去重核对同为 1364 个用例。
- 包括重复取消、已投递后取消、并发租约、重试恢复、群聊权限、图片/语音/文件投递及新共享映射的现有回归。原有业务断言保留，没有新增测试框架。
- 最终 Python 严格类型检查 **599 文件、0 错误**；19 个改动 Python 文件 lint/format、架构检查、Web 类型检查、设置/MCP 20 项测试、文档格式和 diff 检查通过。
- 核心三批已完成。全量测试仍受本地离线 `.env` 和训练素材缓存文件阻塞；Web/desktop 完整构建仍受离线 Live2D 资源阻塞。这些门禁没有记为通过，也没有通过删除用户文件、修改业务代码或弱化断言来绕过。

## 阻塞恢复与补验（2026-10-30）

- Foundation 资源元数据确认三类阻塞输入均为 iCloud ubiquitous item，状态为未下载。通过系统 FileManager.startDownloadingUbiquitousItem 请求原文件回填，未替换文件内容或修改配置。
- 已恢复 30 个离线文件：根目录 .env 1 个、前端公共资源 26 个、训练素材旧字节码缓存 3 个。恢复后逐文件读取验证通过；.env 仅记录可读状态和字节数，没有展示内容。
- 前端公共目录 38 个文件、训练素材目录 10 个文件及 .env 均无 dataless 标记；本次没有修改业务源码、打包规则或测试断言。
- TypeScript 检查、Web 构建、desktop 前端构建均正常退出；两套产品产物隔离检查与 Live2D vendor 文件检查通过。构建保留包体积及 Tauri 混合静态/动态导入的非阻断警告。
- 全量 Python 测试使用标准命令 `/tmp/cw2-refactor-env/bin/python -m pytest -q -o cache_dir=/tmp/cw2-unblocked-pytest-cache` 重跑，**4119 passed、46 skipped、0 failed**，正常退出，耗时 578.04 秒。没有排除此前阻塞的测试，也没有通过临时续跑脚本分段执行。
- 此前单独受阻的配置读取和训练包可重复生成用例额外补验 **2 passed**，正常退出。
- 原三个阻塞均已解除，本轮仅更新验证记录。本节只涉及本地源码验证，不代表 Tauri 原生安装包或设备验收；平台限定的跳过项仍保留独立验收边界。
