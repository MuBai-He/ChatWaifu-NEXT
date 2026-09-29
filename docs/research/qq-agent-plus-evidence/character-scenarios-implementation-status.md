# CW-Q01/Q02 合成评估状态

日期：2026-09-29。基线：`origin/main` 的 `99e62df`；评估器版本 1.1.0。

12 组四轮合成场景、A/B 各重复 3 次，共 288 个本地 Demo 模型逻辑调用。Q03 合并后使用同一基线 persona 与候选 persona 重新运行：dry-run 估算约 787,392 token，费用因没有价格来源而标为未知；显式执行后按本地算法估算 723,450 prompt token 和 18,288 completion token，Demo 不提供供应商用量。结果 288/288 有终态，续跑重复调用 0 次。原始结果、元数据和脱盲映射在 [`character_scenarios_ab_demo_v2/`](character_scenarios_ab_demo_v2/)；完整盲评表可由运行器重建，未将生成模板和隔离 SQLite 数据库纳入仓库，也未保存凭据。

144 个 A/B 配对的回复全部相同。Demo Provider 的回复不依赖 persona，因此这些结果只验证了场景装载、PromptCompiler、Provider 适配器、结果持久化、配对和续跑链路；**不能证明新人设优于旧人设**。合并后基线的提示 token 范围为 2,154–2,493，候选为 2,605–2,944。完成 token 总计 18,288；本地延迟以整数毫秒记录，p50/p95 均为 0 ms，不能当作真实模型时延。

确定性测试覆盖配置不匹配拒绝续跑、已完成轮次的历史/状态重放、超时记录可重试、预计费用耗尽前停止、未知 Provider 拒绝、远程执行在有价格基准时要求端点/模型/请求上限/费用上限/价格来源。当远端模型价格未知且用户显式授权无费用上限时，提供 `--no-cost-ceiling` 模式；该模式仍严格要求 `--max-requests`、`--model` 和 `--base-url`，且拒绝包含价格或费用上限的矛盾参数。`cost_policy`（`capped` 或 `no_cost_ceiling`）记录在续跑元数据中，防止续跑时静默切换费用策略，并保持旧版结果与元数据的向前兼容。

针对观测 token 与估算 token：运行器在远程评估时开启 `OpenAiCompatibleLlmProvider` 的 `request_usage` 选项（请求 `stream_options.include_usage: true`），解析流式 SSE 中于 `finish_reason` 之后、`[DONE]` 之前可能到达的用量块，并提取 prompt、completion、total 以及思考/推理 token（`reasoning_tokens`）。评估样本中同时持久化供应商原始用量（`provider_tokens_*`）与本地估算（`estimated_tokens_*`），并标注明确来源（`tokens_source`）；汇总报告在存在供应商上报时优先使用观测数据，不伪造 0 美元或未知的计费数据。`--max-requests` 统计的是逻辑请求轮次，适配器内部重试可能产生更多 HTTP 尝试。CLI 对未完成或中断后可续跑的执行返回非零状态，不再将零结果误报为成功。

2026-09-29 端点探针：用户指定的 `gemini-3.8-flash-high`、`claude-sonnet-4-6`、`claude-opus-4-6-thinking` 均完成一次短文本请求，HTTP 200 且返回文本；三者的流式 `include_usage` 也返回了用量块。随后首次 Gemini A/B 小样本在连接阶段三次失败，结果为 0/8；同机 Python 与 curl 对当前 CW2 端点的 TLS 握手也失败，其他 HTTPS 站点可访问。此证据仅定位到当前端点连接路径，不足以判断服务端或代理哪一侧故障；尚无真实 A/B 结果。端点没有公开价格数据，用户授权无美元费用上限，后续仍需按逻辑请求上限和实际用量记录执行。

尚需在同一指定真实模型、同一参数及受控请求上限（或费用上限）下执行 A/B 并完成盲评和受控 Runtime 交互验收，才能判断 Q02 的角色改善及发布门槛。当前没有真实模型人格结论，也没有真实渠道发送或播放验收。
