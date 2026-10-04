# Q02 v7 本地测试版本

日期：2026-10-04。按用户选择，在 `mubai/q02-v7-test` 使用 v7 作为默认角色提示词。工作区为 `/Users/mubai/Desktop/CW2/.worktrees/qq-agent-plus-adoption`；仅本地提交，服务器保持其独立测试版本。此次没有调用真实模型、发送渠道消息、启动音频或使用 AGY。

## 版本与回退

- 原实现基点：`e6093459ffa5105892e61172af5ca1804dd89a9b`。
- 优化检查点：`7d8ce899d6c2e4001ceaa6001bf918b7665fda2c`，保存此前 Runtime 改动和既有脱敏评测证据，默认 persona 仍为 v4。22 个隔离评测数据库保留本地，没有纳入提交。
- 本次选择的原始 v7：[冻结文本](persona-candidate-v7-source-preservation-2026-10-01.md.txt)，SHA-256 为 `5dde07c1f242dc1dc197b04240452e17cd56738efbd25e4dbb7d210bcbdaf616`；与全量评测使用的 v7 相同，逐字复制到 `characters/default/persona.md`，未追加提示规则。
- v4 回退只需恢复上述检查点中的 `characters/default/persona.md`；角色包在 Runtime 启动时加载，从本工作区重新启动后才生效。模型、关系状态及其他角色文件未随本次选择修改。

## 使用判断与已知限制

这是用户选择的测试版本，Q02 正式质量结论仍未通过。[既有全量主审](q02-full-primary-review-2026-10-04.md)覆盖 864 条回复：v4 完整符合 266/432、v7 为 269/432；v7 情绪低落改善，但技术求助完整符合从 33/36 降至 25/36，默认语音从 78/144 降至 70/144，新增一次无依据现实活动陈述。保留原始判断，不因启用测试版本改分。

v7 为 1454 字符、727 个编译器参考 token。现有小窗口分项预算为 700，会裁剪 v7；1024/4096 配置不作为完整 v7 的使用配置。8192 默认预算及 32768/8192 输出预留/15% 估算余量的试验预算，在三种 presentation 下均完整保留角色文本。这里只验证包加载和裁剪报告，不证明供应商原生 token 限制或回答质量。

## 整体优化进度

| 任务 | 已实现的范围 | 仍需验收 |
| --- | --- | --- |
| Q01 | 十二场景、四轮、重复 A/B、续跑、实际输入和用量记录 | 运行器正确不等于角色质量通过 |
| Q02 | 角色规则、模型预算、来源发现/读取、跨轮资料和回退流程；既有微信文字及桌面播放确认 | 原第三轮错误细因、检索稳定性/资料适用范围、回答条件和跨轮保真、最终冻结版本完整 A/B |
| Q03 | generation 配置快照、角色包指纹；隔离真实 Provider 切换 | 真实设备/渠道和凭据轮换范围未完整覆盖 |
| Q04 | 只读互动诊断、持久化投影、Web/桌面页面 | 对应真实渠道/播放诊断的完整验收 |
| Q05 | 参与、静默、结束和语音入口确定性检查 | 真实环境语音与主动触达 |
| Q06 | 记忆修订/来源展示、删除围栏、误召回修复及量化评估 | 多账号真实资料边界 |
| Q07 | 表情选择、学习/发送开关、删除、ACK、取消检查 | 本实现分支的真实渠道显示与体验 |
| Q08 | 后续候选 | 可信价格源和成本展示需求另行确定 |

其他 QQ 测试分支及服务器版本的成果没有自动计入本分支。各任务详细证据仍以[实施状态](implementation-status.md)和[原计划](../cw2-qq-agent-plus-adoption-plan.md)为准。

## 主要文件

| 文件组 | 主要文件 | 用途 |
| --- | --- | --- |
| 角色与上下文 | `characters/default/persona.md`、`character_kernel/prompt.py`、`characters/service.py`、`conversation/models.py`、`conversation/service.py` | 角色规则、提示编译、指纹和每轮配置快照 |
| 预算与 Provider | `providers/model_config.py`、`providers/context_budget.py`、`providers/input_estimation.py`、`providers/openai_compatible.py`、`providers/contracts.py`、`agent/input_budget.py` | 分模型预算、输出预留、完整 payload 参考估算与供应商错误/usage |
| 搜索及资料延续 | `runtime_skills/public_web.py`、`public_web_providers.py`、`public_web_search.py`、`agent/tool_calling.py`、`source_context.py`、`source_answer.py`、`conversation/source_answer_context.py` | SearXNG/Crawl4AI、DNS 参数、正文收据、历史选择和失败回退 |
| 诊断与记忆 | `api/interaction_diagnostics_routes.py`、`diagnostics/ports.py`、`persistence/sqlite_interaction_diagnostics.py`、`memory/retrieval.py`、Web `InteractionDiagnosticsPanel.tsx` 与 `MemoryControlCenter.tsx` | 互动事实展示、记忆来源和修订链、召回相关性 |
| 评测与文档 | `tools/evaluate_character_scenarios.py`、`audit_character_facts.py`、`runtime_source_evaluation.py`、场景夹具、Runtime/工具测试、原计划及冻结证据 | 保留成功、失败、回退、省略内容和检查器修订的完整记录 |

Runtime 文件相对 `services/runtime/src/chatwaifu_runtime/`；Web 文件相对 `apps/web/src/features/`。上述是整个优化的主要落点，不表示均由此次 v7 文本切换新增。

## 本地检查

- 角色编译、v7 预算边界及气泡展示：71 通过；原 v4/700-token 控制保留，v7 单独覆盖三种 presentation 的四种预算。
- Runtime、Python 协议、工具与 contract 合跑：2374 通过、46 个 Windows 专属检查跳过。
- 全范围 Ruff 检查及格式检查通过（416 文件）；Pyright 0 错误、0 警告。提交前只修正三个旧测试文件的导入排版，没有修改冻结证据或生产逻辑。
- 源码和说明文档空白检查通过；46 份既有捕获证据的补丁、日志、模型代码或模板保留原始空白。包含这些原始证据的全量 `git diff --check` 仍报告空白，不将其写成全量通过。
- 前端此次未修改，沿用对应既有构建和浏览器证据；没有将本次离线检查替代真实模型质量、渠道和物理播放验收。
