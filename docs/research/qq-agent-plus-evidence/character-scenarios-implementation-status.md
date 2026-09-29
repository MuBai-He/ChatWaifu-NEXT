# CW-Q01/Q02 合成评估状态

日期：2026-09-29。基线：`origin/main` 的 `99e62df`；评估器版本 1.1.0。

12 组四轮合成场景、A/B 各重复 3 次，共 288 个本地 Demo 模型逻辑调用。Q03 合并后使用同一基线 persona 与候选 persona 重新运行：dry-run 估算约 787,392 token，费用因没有价格来源而标为未知；显式执行后按本地算法估算 723,450 prompt token 和 18,288 completion token，Demo 不提供供应商用量。结果 288/288 有终态，续跑重复调用 0 次。原始结果、元数据和脱盲映射在 [`character_scenarios_ab_demo_v2/`](character_scenarios_ab_demo_v2/)；完整盲评表可由运行器重建，未将生成模板和隔离 SQLite 数据库纳入仓库，也未保存凭据。

144 个 A/B 配对的回复全部相同。Demo Provider 的回复不依赖 persona，因此这些结果只验证了场景装载、PromptCompiler、Provider 适配器、结果持久化、配对和续跑链路；**不能证明新人设优于旧人设**。合并后基线的提示 token 范围为 2,154–2,493，候选为 2,605–2,944。完成 token 总计 18,288；本地延迟以整数毫秒记录，p50/p95 均为 0 ms，不能当作真实模型时延。

确定性测试覆盖配置不匹配拒绝续跑、已完成轮次的历史/状态重放、超时记录可重试、预计费用耗尽前停止、未知 Provider 拒绝、远程执行在有价格基准时要求端点/模型/请求上限/费用上限/价格来源。当远端模型价格未知且用户显式授权无费用上限时，提供 `--no-cost-ceiling` 模式；该模式仍严格要求 `--max-requests`、`--model` 和 `--base-url`，且拒绝包含价格或费用上限的矛盾参数。`cost_policy`（`capped` 或 `no_cost_ceiling`）记录在续跑元数据中，防止续跑时静默切换费用策略，并保持旧版结果与元数据的向前兼容。

针对观测 token 与估算 token：运行器在远程评估时开启 `OpenAiCompatibleLlmProvider` 的 `request_usage` 选项（请求 `stream_options.include_usage: true`），解析流式 SSE 中于 `finish_reason` 之后、`[DONE]` 之前可能到达的用量块，并提取 prompt、completion、total 以及思考/推理 token（`reasoning_tokens`）。评估样本中同时持久化供应商原始用量（`provider_tokens_*`）与本地估算（`estimated_tokens_*`），并标注明确来源（`tokens_source`）；汇总报告在存在供应商上报时优先使用观测数据，不伪造 0 美元或未知的计费数据。`--max-requests` 统计的是逻辑请求轮次，适配器内部重试可能产生更多 HTTP 尝试。CLI 对未完成或中断后可续跑的执行返回非零状态，不再将零结果误报为成功。

2026-09-29 端点复核：早先不带端口的 URL 在 TLS 握手阶段失败；用户指出实际端口后，HTTPS 8318 端口与 CW2 当前保存配置一致。用户指定的 `gemini-3.8-flash-high`、`claude-sonnet-4-6`、`claude-opus-4-6-thinking` 均完成了短文本、流式用量及隔离 Runtime 交互。三个模型各 288/288 条完整合成 A/B，共 864 条；续跑补齐 Opus 暂时失败的请求。原始输出、每模型 token/p50/p95、Sonnet 盲评和候选 v3 身份修订见[真实模型评估报告](real-model-ab-2026-09-29.md)。端点未提供价格数据，用户授权无美元费用上限；供应商 token 计数不是美元账单。

完整 A/B 使用候选 v2；盲评识别的一次原作制作方误述已在候选 v3 修复，三模型各 24 条身份定向复测通过。因此 v3 的全场景 A/B 仍待进行。更关键的是，18 条国内航班充电宝规定回答全部漏掉 2025 年新增的 3C/召回限制；这项时效性事实准确性门槛未通过。真实 QQ/微信发送、设备音频和用户侧播放确认也没有由隔离测试覆盖，不能把生成完成解释为送达或听见。
