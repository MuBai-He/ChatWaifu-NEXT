# Q02 下一轮执行方案

日期：2026-10-02。执行约束：不调用 AGY；由主代理直接实现、审查和测试。默认 persona v4、生产模型配置和 PR 草稿状态保持不变，直到新的真实模型证据满足放行条件。

## 目标

区分预算不足、上游裁剪、工具/来源消费、模型能力和格式遵循问题，完成 Q02 剩余验收。照片用户注释链路作为独立回归范围验证，不把它的自动化通过当作角色质量通过。

## 阶段 0：冻结与端点确认

1. 在生成前记录工作树提交、评测器版本、角色 persona hash、PromptCompiler/template 版本、预算 JSON、presentation、关系状态和 `prompt_as_of`。
2. 从当前 CW2 运行配置读取聊天端点，只确认端口、模型清单和非敏感路由摘要；不要输出或保存密钥。
3. 当前 Q02 历史证据使用 HTTPS 8318 / `gemini-3.8-flash-high`。若实时端点不可达，停止真实生成，只做离线审查和确定性测试，不用旧结果冒充新复测。
4. 生产 SQLite、默认 persona 和用户数据不得被评测脚本写入；所有真实模型运行使用隔离目录和隔离数据库。

## 阶段 1：预算和裁剪审计

1. 用推荐配置 `context_window=32768`、`output_reserve=8192`、`estimate_margin=15%`、`scaled` 分项重建同一问题和资料。
2. 对每个请求保存：完整编译输入、最终 HTTP JSON、参考 token 估算、供应商 prompt/completion/total/reasoning usage、延迟、角色/历史/记忆/来源/工具结果的省略记录。
3. 检查总窗口扩大后，角色、记忆候选、历史条数、摘要、照片证据、来源账本和工具结果字节限制是否仍独立丢失信息。
4. 预算对照至少包含：当前配置、仅扩大总窗口、扩大总窗口并放宽分项。材料、问题、时钟、关系状态和输出要求必须完全相同。
5. 只把“资料未进入请求”归为预算/裁剪候选；资料已完整进入而答案错误，归为模型能力或格式问题。

## 阶段 2：技术正确性定向复测

先跑 `technical_help`，每个模型至少三重复，优先使用当前有额度的 Gemini 3.8 Flash High。保留原始回复，不做事后改写。

每条回复按以下标准审查：

- `asyncio.gather`：区分默认异常传播、其他任务是否自动取消、`return_exceptions=True` 的返回值，以及外层 `asyncio.run`/取消/特殊 `BaseException` 的边界。
- 完整代码：实际执行代码，比较声称输出和真实输出；语法通过不等于生命周期解释正确。
- Raft：区分 heartbeat interval、broadcast time、election timeout、随机化和论文约束；不能把一个实现示例的数值当作协议固定值。
- 任何未执行或未查证的特殊异常、取消和清理行为都必须标明不确定，不得补充成绝对规则。

如果错误在完整输入下稳定复现，先记录为模型能力问题；只有发现 Runtime 丢失条件、错误工具结果或提示冲突，才修改源码。提示修改必须是通用边界规则，不能写入题目答案或硬编码法规/技术结论。

## 阶段 3：来源条件和清单保真复测

1. 使用真实 Runtime source tool loop，允许一次 READ；保存搜索结果、原文正文、实际 URL、发布时间/生效时间、适用范围、例外和权限状态。
2. 覆盖三个输入形态：`instant_message`、`single_text`、`default_voice`。
3. 覆盖来源已提供、需要继续查证、搜索失败、原文不可读、明确禁止新工具和复合读/写请求。
4. 检查最终清单是否保留每个关键条件、例外、未核实状态和实际来源链接；摘要、锚文本或旧来源不能冒充原文核实。
5. 对法规/当前规则，不因当前时间存在就把旧来源当作现行依据；必须核对后续修改及生效范围。

## 阶段 4：完整 Q02 质量批次

只有阶段 1–3 的软件和定向结果已保存后，才运行完整批次：12 个场景、4 轮、3 重复、当前要求的三种 presentation；如做 persona A/B，先冻结输入和标签，再揭示映射。

必须报告：

- 每模型逻辑回复数、Provider 回合数、失败和重试数；
- 原始回复、输入 hash、工具交换、权限状态、供应商 usage、p50/p95 延迟；
- 完全符合/部分符合/不符合、硬边界失败、任务保真失败、来源失败、技术失败和格式失败；
- 预算不足与模型能力的分层结论；
- 价格未知时保持未知，不从 token 用量推算美元费用。

模型评分只能辅助排序，不能替代人工逐条复核或 Runtime 验收。

## 阶段 5：照片注释独立验收

确认以下路径和边界：

- `photo_memory/annotations.py`：原文 quote、置信度、候选 photo ID、歧义返回 null、纠正替换；
- SQLite：幂等、revision fence、删除后不可写、重启可读、旧注释 superseded；
- 语义索引：注释数量变化时旧 embedding 不被误复用；
- Web：详情对话框只显示未 superseded 注释，删除照片后刷新不可见；
- 不把“可查看注释”误报成“已有手动编辑注释功能”。

当前已知回归基线：Runtime 注释/照片相关测试通过，Web 全套 313 项通过。除非发现新失败，不为注释链路扩大范围或新增协议。

## 修改和放行规则

- 不修改默认 persona、生产端点、生产预算或数据库，除非新的真实证据证明修复并完成同范围回归。
- 每个源码修改先添加确定性回归，再做小范围真实模型复测；源码、评测器和 evidence 版本必须分开记录。
- 端点不可达、来源服务失败、设备播放或真实渠道未验收时，保持 Q02 未通过，明确写出阻塞层级。
- 只有在来源完整性、技术正确性、三种 presentation、完整三重复和受控 Runtime 交互全部达到既定门槛后，才讨论将候选 persona 设为默认；真实 QQ/微信投递、设备播放和用户 ACK 仍需单独验收。

## 推荐执行命令

```sh
uv run pytest services/runtime/tests/test_photo_annotations.py \
  services/runtime/tests/test_inbound_multi_image.py \
  services/runtime/tests/test_photo_memory_repository.py \
  services/runtime/tests/test_photo_memory_semantic.py

uv run pytest tools/tests/test_evaluate_character_scenarios.py \
  services/runtime/tests/test_runtime_source_evaluation.py \
  services/runtime/tests/test_tool_intent.py \
  services/runtime/tests/test_character_kernel.py \
  services/runtime/tests/test_model_context_budget.py

pnpm --filter @chatwaifu/web test
```

真实生成前先执行 dry-run，确认请求数、预算和隔离目录；真实运行必须显式指定模型、端点、最大请求数和费用策略。完成后再运行相关 formatter、Ruff、Pyright、协议生成检查、Runtime/contract 全集以及 Web/桌面构建。

## 收尾状态（2026-10-02）

阶段 0–5 的本轮可执行部分已完成并留存证据：

- 端点修正为认证后的 `https://mubai.website:8318/v1`；`/models` 返回 200，18 个模型清单已核对，未保存密钥。
- 推荐预算下完成 Gemini 3.8 Flash High 的 `technical_help`、`detailed_answer`、`topic_switch` 各 12 条真实回复；供应商 usage、延迟、原始回复和隔离目录均保留。
- 三种 presentation 的来源边界补测完成 9 条；`default_voice` 的 `agenda.manage` native write 实际得到 `permission_denied`，三库任务表为空、持久权限为零。
- 照片注释、Runtime 来源、工具意图、PromptCompiler、模型预算和 Web 注释回归通过；自动注释链路不代表已有手动编辑入口。

放行结论仍为 **Q02 未通过**：充电宝回答遗漏 2025-06-28 的 3C/召回型号或批次条件，Raft 回答仍可能把实现示例参数说成协议固定值；`default_voice` 的真实 TTS、渠道投递、播放和用户 ACK 尚未验收。没有修改默认 persona、生产模型、生产预算或生产数据库。本轮没有调用 AGY。
