# v5 定向真实模型复核

2026-09-30。CW2 的四组弱项（身份、技术求助、话题切换、详细回答）各四轮、三重复、两个版本，三个指定模型各完成 96/96 条，共 288 条、144 对。本批重新生成同批 v4 基线，与 [v5 候选](persona-candidate-v5-primary-2026-09-30.md.txt)比较，没有借用此前 v4 回复。候选优先 28、基线优先 7、平手 109；任务失败由基线 42/144 到候选 28/144。改善集中在回答已知产品架构，**Q02 仍未通过，v5 未替换生产默认 v4**。

| 模型                     | 候选优先 / 基线优先 / 平手 | 任务失败 v4 / v5 | 技术求助失败 v4 / v5 | 选定法规完整覆盖 |
| ------------------------ | -------------------------- | ---------------- | -------------------- | ---------------- |
| Gemini 3.8 Flash High    | 5 / 1 / 42                 | 11 / 6           | 1 / 0                | 0/6              |
| Claude Sonnet 4.6        | 12 / 3 / 33                | 18 / 11          | 6 / 5                | 0/6              |
| Claude Opus 4.6 Thinking | 11 / 3 / 34                | 13 / 11          | 2 / 3                | 0/6              |

每个版本每模型有 48 条回复；失败按回复计数，包含核心正确但附加解释错误的样本。法规审计只选第二轮，共 18 条；Checklist 的来源遗漏还在任务判断中计入第四轮。此四场景结果不能扩展为全部十二场景、长期关系、语音或真实渠道的通过证明。

## 输入与评审方法

生成使用运行器 **1.2.1**、夹具 `1a2281443a1c0fc5`、PromptCompiler `7c85a6ef6515d89c`，persona 原文指纹分别为 v4 `3ab060352dcb8e25`、v5 `3df16760df3f75aa`；实际加载的去空白指纹为 `0bc4a9eecc637dcf` / `554b28bebda60025`。每模型 96 个唯一配对键齐全，96 个实际输入快照均等于独立生产 reducer 的 16 轮预期值，**72 条 acquaintance、24 条 familiar**。原始 JSONL 与生成目录逐字节相同，原始模型、persona、usage 与运行器版本没有回写。

主代理逐条读取全部 288 条回复。每个场景/重复组重新随机 A/B，在四轮内保持该组映射不变；质量包不带版本、时延或 token。先保存 `primary-masked-review.json`，再读取单独映射生成 `primary-review.json`；后者保存前者的 SHA-256 和揭示时间。被冻结的原文件保持原字节。

**评审者知道 v5 的设计，因此 `blind: false`。这是标签隐藏后的主代理模型复核，不是独立盲评、双盲或人工验收。** 旧运行器的匿名模板与键表保留，未用于此次主代理的重新随机质量包。没有调用 AGY。

## 逐项结论与代码执行

- 已知架构：v4 三模型九条身份第三轮回答均整体推辞；v5 九条均区分已知 ChatWaifu NEXT 本地 Runtime 与未知当前模型部署。模型品牌、授权状态和部署版本仍未验证。
- 技术求助：Gemini 候选在选定样本未出现明确技术失败；Sonnet 候选仍有五条，Opus 候选三条。包括其余 gather 异常未检索告警的错误、特殊异常行为的错误概括，以及程序关闭行为与注释矛盾。按 [gather 文档](https://docs.python.org/3.12/library/asyncio-task.html)、[Runner 文档](https://docs.python.org/3.12/library/asyncio-runner.html)及 [CPython Task 实现](https://github.com/python/cpython/blob/3.12/Lib/asyncio/tasks.py)区分普通异常、子任务取消和 `KeyboardInterrupt`/`SystemExit`，不凭代码关键词批准技术正确性。
- 完整代码：检查后隔离执行六份/模型第三轮完整程序，最终十八份均成功且与声称输出相同。Sonnet 一条先展示错误输出后明确自我纠正，保留初版与末版执行记录，按最终修正结果判断。Opus 另有第一轮完整程序实测只打印捕获异常，main 返回后 Runner 取消未完成任务，没有注释声称的 ok_task 完成；它与六份第三轮正确程序分开记录。
- Raft：候选 Sonnet/Gemini 在本批没有明确选举参数任务失败，Opus 候选仍有两条。心跳达到最小选举超时、将收到投票请求一概当成授予投票及数值范围不支持自述间隔，按 [Raft 论文图 2 和 §5.6](https://raft.github.io/raft.pdf)复核；未形成直接矛盾的模糊说明另列风险。
- 法规：第二轮 18/18 条仍缺固定来源的 3C 标识与召回型号/批次条件；第四轮 Checklist 未补齐。v5 多数更明确承认时效限制，但提示用户核查不能代替来源覆盖。审计只核对[选定民航局快照](https://www.caac.gov.cn/XWZX/MHYW/202506/t20250626_227805.html)，不宣称覆盖当前完整法规。三条 5V 线索经语义复核，不把正确乘法或单独关键词自动判为算术错误。
- 详细任务：两版本每模型均完成三类各三条清单；Sonnet 基线一条室内方案不满足场所条件。Opus 的有顶棚/美食街选项及部分时间安排保留风险，未将所有模糊表述当明确失败。

评估器直接调用 Provider，不执行 Runtime 工具循环或联网查证。“要求核实来源”没有变成实际查询，不能以本批证明生产已具备法规检索；把固定答案写入 persona 不能修复这个能力缺口。

## 供应商用量与延迟

使用 CW2 当前配置的 HTTPS 8318 端点和用户授权的无美元费用上限，每模型最多 96 次逻辑请求。未设置 temperature、top_p 或 seed，由端点默认采样。三个模型并发，每模型版本按固定块生成；p50/p95 为成功逻辑请求的线性插值，**不能证明 persona 提速或替代实时音频验收**。

| 模型 / 版本 | p50 / p95（ms）   | Provider 输入 / 输出 / 总 token | 另报 reasoning        |
| ----------- | ----------------- | ------------------------------- | --------------------- |
| Gemini v4   | 6641.5 / 11812.35 | 143939 / 9178 / 177530          | 24413（48/48 条报告） |
| Gemini v5   | 5998 / 11143.75   | 132083 / 8919 / 162231          | 21229（46/48 条报告） |
| Sonnet v4   | 5974.5 / 14476.5  | 149140 / 12816 / 161956         | 未报告                |
| Sonnet v5   | 7075 / 14164.5    | 136120 / 11949 / 148069         | 未报告                |
| Opus v4     | 6371 / 22842.8    | 155893 / 16987 / 172880         | 未报告                |
| Opus v5     | 6527 / 22450.75   | 142547 / 15813 / 158360         | 未报告                |

每组 48/48 条有 Provider 输入、输出、总量；reasoning 已在供应商总量内，不再次相加，未报告不补零。价格来源和实际美元账单未知，失败/内部重试可能另计费。

## 可复核证据与后续门槛

- Gemini：[原始回复](character_scenarios_ab_gemini_3_8_flash_high_v5_targeted_primary/results.jsonl)、[48 对判断](character_scenarios_ab_gemini_3_8_flash_high_v5_targeted_primary/primary-review.json)、[输入与用量校验](character_scenarios_ab_gemini_3_8_flash_high_v5_targeted_primary/validation-summary.json)、[代码执行](character_scenarios_ab_gemini_3_8_flash_high_v5_targeted_primary/primary-code-execution.json)。
- Sonnet：[原始回复](character_scenarios_ab_claude_sonnet_4_6_v5_targeted_primary/results.jsonl)、[48 对判断](character_scenarios_ab_claude_sonnet_4_6_v5_targeted_primary/primary-review.json)、[输入与用量校验](character_scenarios_ab_claude_sonnet_4_6_v5_targeted_primary/validation-summary.json)、[代码执行](character_scenarios_ab_claude_sonnet_4_6_v5_targeted_primary/primary-code-execution.json)。
- Opus：[原始回复](character_scenarios_ab_claude_opus_4_6_thinking_v5_targeted_primary/results.jsonl)、[48 对判断](character_scenarios_ab_claude_opus_4_6_thinking_v5_targeted_primary/primary-review.json)、[输入与用量校验](character_scenarios_ab_claude_opus_4_6_thinking_v5_targeted_primary/validation-summary.json)、[六份代码执行](character_scenarios_ab_claude_opus_4_6_thinking_v5_targeted_primary/primary-code-execution.json)、[额外生命周期失败](character_scenarios_ab_claude_opus_4_6_thinking_v5_targeted_primary/primary-default-gather-execution.json)。

每目录另有固定判断、随机映射、覆盖审计和校验和；[288 次真实编译检查](persona-v5-primary-compile-validation-2026-09-30.json)只证明预算容纳。

下一项质量切片需给时效问题提供有来源和时间的实际工具结果，并在同一 Runtime 工具循环验收；技术问题继续保留完整代码和边缘行为执行核查。来源能力使用现有 Runtime Skill/MCP 边界，不能藏进角色卡或记忆样本冒充真实查询。此前保留 Q02 失败和 PR 草稿，不通过不断增加 persona 版本重复同一能力缺失实验来宣称放行。
