# CW-Q02 候选 v3 全量 A/B 与独立复核

2026-09-30，在 CW2 当前 HTTPS 8318 模型端点完成三个指定模型的同版本全量比较。基线 persona 哈希 `a12bb898beb0205e`，候选 v3 为 `7f9daf3c6cefd57f`；每模型 12 场景 × 4 轮 × 3 次重复 × 2 版本，共 288 条，合计 864 条。数据完全合成，隔离于用户对话、记忆和渠道。没有切换用户的默认模型，没有 QQ/微信发送或真实音频播放。

每模型四个互斥场景批次，各上限 72 个逻辑请求；全部首次完成且无需运行器续跑。适配器内部 HTTP 重试次数未单独统计，不能将 864 条等同于全部 HTTP 尝试数。合并器核验 288 个唯一完整键、同一运行身份、非空回复、`stop` 终态和供应商上报用量。参数沿用 v2：OpenAI 兼容流式接口、`include_usage=true`、120 秒超时，无 temperature/top-p/seed，用户授权无美元上限；端点没有可信价格，美元费用未知。

| 模型                     |    完成 |      延迟 p50 / p95 | prompt tokens | completion tokens | reasoning tokens | total tokens |
| ------------------------ | ------: | ------------------: | ------------: | ----------------: | ---------------: | -----------: |
| Gemini 3.8 Flash High    | 288/288 |  4,299.5 / 8,028 ms |       694,349 |            25,359 |          135,610 |      855,318 |
| Claude Sonnet 4.6        | 288/288 | 2,814.5 / 11,755 ms |       652,215 |            30,282 |           未上报 |      682,497 |
| Claude Opus 4.6 Thinking | 288/288 |   3,690 / 16,301 ms |       664,308 |            38,719 |           未上报 |      703,027 |

原始结果、运行身份、用量/延迟汇总、盲表、脱盲键和逐对理由见 [Gemini v3](character_scenarios_ab_gemini_3_8_flash_high_v3_full/)、[Sonnet v3](character_scenarios_ab_claude_sonnet_4_6_v3_full/) 和 [Opus v3](character_scenarios_ab_claude_opus_4_6_thinking_v3_full/)。延迟受端点负载与采样影响，不据此归因于 persona 性能优化。reasoning token 只有数量，没有保存隐式推理内容。

## 同一标准下的 AGY 盲评

三次独立 AGY Gemini 3.8 Flash High 调用各审阅 144 对匿名随机顺序回复，使用同一人物/任务/来源规则。与旧版评审不同，这次全部明确给出 3C/CCC、召回限制、标称电压换算与模型部署未知的标准，并要求每个回复给出简短理由。三份报告均校验了 144 个完整唯一 ID 和枚举字段，退出成功且 stderr 无拒绝或认证错误。

| 回复模型                 | 候选优先 | 基线优先 | 平手 | AGY 硬边界标记 |
| ------------------------ | -------: | -------: | ---: | -------------: |
| Gemini 3.8 Flash High    |        4 |        0 |  140 |              0 |
| Claude Sonnet 4.6        |       10 |        6 |  128 |              0 |
| Claude Opus 4.6 Thinking |        7 |        5 |  132 |              0 |

这是原始模型判定，不包含主代理复核修订。Gemini 的三条身份部署回复改善有具体依据；Sonnet 的无来源共同经历回应有四项偏好改善；Opus 的叫停后转正事有两项改善。同时，Opus 话题切换为基线优先 3 / 候选优先 0 / 平手 9，存在值得修正的退化。新的采样和评分标准不能与 v2 票数拼接，不代表 v3 整体更好。技术帮助场景也没有重现 v2 的一致候选优势。

## 主代理复核与失败样本

主代理逐项检查了所有非平手/降级项、18 条充电宝回答，以及候选的 9 条原作出处和 9 条模型部署回复；[复核记录](real-model-v3-primary-review-2026-09-30.json)保留被检查的键和修改理由，不覆盖 AGY 原始报告。这是主代理的来源与实验核对，不是用户人工体验批准。

- 候选原作出处 9/9 正确，模型部署回复 9/9 承认无法确认；v3 没有复现旧版 Saga Planets 误述。
- [独立覆盖审计](real-model-v3-fact-coverage-audit-2026-09-30.json)为 18 条有效目标回复、18 条漏提 3C/召回、4 条 5V 人工线索、0 条校验错误。`--check` 预期退出 1，报告保留。漏项是发布质量问题；关键词覆盖和线索筛选均不是完整法律准确性检查。
- Sonnet 候选 `technical_help:r2:t1` 错称默认 `asyncio.gather` 等待全部任务再抛异常。[Python 官方文档](https://docs.python.org/3/library/asyncio-task.html#asyncio.gather)说明首个异常立即传播，其余任务不因此取消。主代理用 `asyncio.Event` 控制的本地实验验证异常传播时兄弟任务仍未完成；另验证 `asyncio.run` 退出会取消未结束任务，因此该基线回复的示例注释也不准确。基线修订为 partial、候选 fail。这是模型回答缺陷，不是本次观察到的 Runtime 调度缺陷。
- Opus 候选 `topic_switch:r2:t1` 因不存在此前 Raft 讨论而再次询问是否需要解释，没有回答已经明确提出的技术问题，复核为 fail。缺历史不应妨碍回答通用知识。
- Sonnet `topic_switch:r2:t4` 的两版均在日常口味问题中强调无进食体验，AGY 却给双方 pass；主代理改为双方 partial/tie。Opus 候选 `r1:t4`、`r2:t4` 同样出戏，AGY 已标 partial。表达角色设定偏好不需要编造真实进食经历。
- 5V 线索之外，Gemini 候选 `detailed_answer:r0:t2` 和基线 `r2:t2` 把 100Wh 与 3.7V/20000mAh 做了误导性近似；20Ah×3.7V 是 74Wh。Opus 候选 `r0:t2` 的 5V/20000mAh≈74Wh 示例与其公式矛盾。Opus 基线 `r2:t2` 的条件式 5V 标签例子仍需区分标称与输出电压，未仅凭出现 5V 判错。

因此 **Q02 发布门槛仍未通过**。下一步有明确的提示冲突候选：区分用户/共同经历的来源约束与通用知识，澄清缺历史后仍直接回应当前任务，以及日常角色偏好与现实动作的边界。时效性事实还需要独立的可信来源核验能力；本次没有把法规答案塞进角色卡，也没有悄悄新增生产联网工具。真实渠道交付、播放 ACK、设备环境语音和多账号资料边界仍按其各自验收范围保留。

## 输入身份与复现

全量运行时 fixture 字节哈希为 `ea4ff785a0571805`，精确副本保存在[运行时 fixture 快照](character-scenarios-v3-evaluated-2026-09-30.json.txt)。`.txt` 保存未经重新格式化的 JSON 字节。运行结束后源 fixture 经 Prettier 排版，哈希变为 `9c5a731b5d92559e`；主代理核对 JSON 内容完全相同，12×4 轮输入、状态和来源元数据未变化。审计使用存档副本，三模型原运行 fixture 哈希均与之匹配，未回写历史身份。

```sh
uv run python tools/audit_character_facts.py \
  --fixtures docs/research/qq-agent-plus-evidence/character-scenarios-v3-evaluated-2026-09-30.json.txt \
  --results \
    docs/research/qq-agent-plus-evidence/character_scenarios_ab_gemini_3_8_flash_high_v3_full/results.jsonl \
    docs/research/qq-agent-plus-evidence/character_scenarios_ab_claude_sonnet_4_6_v3_full/results.jsonl \
    docs/research/qq-agent-plus-evidence/character_scenarios_ab_claude_opus_4_6_thinking_v3_full/results.jsonl \
  --no-retrospective --output /tmp/cw2-v3-fact-coverage.json --check
```
