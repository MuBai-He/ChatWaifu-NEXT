# Q02：Gemini v4 三种 presentation 完整场景复测

日期：2026-10-02。执行约束：不调用 AGY，由主代理直接执行和审查。源码提交 `e6093459ffa5105892e61172af5ca1804dd89a9b`，评测器 1.14.0，默认 persona v4，目标模型 `gemini-3.8-flash-high`，端点为当前 CW2 配置的 HTTPS 8318。生产数据库、生产配置、默认 persona 和凭据均未写入或保存。

## 执行范围

以同一份十二场景、四轮 fixture 为基础，只替换 `presentation_profile`，分别执行 `instant_message`、`single_text` 和 `default_voice`（后者只收集文本，不启动 TTS、麦克风、扬声器或真实渠道）。每种展示 12 场景 × 4 轮 × 3 重复，共 **432 条逻辑回复**。三组均使用同一 persona hash `3ab060352dcb8e25`、模型、端点、提示时间 `2026-10-02T00:00:00+00:00` 和隔离数据库。

冻结预算为上下文窗口 32768、输出预留/上限 8192、估算余量 15%、`scaled` 分项、历史上限 32、记忆候选 24、工具结果上限 131072 字节，对应估算输入上限 21370。三组 dry-run 均确认 144 条请求；端点没有可信价格元数据，本批按用户授权使用 `--no-cost-ceiling`，不由 token 用量推算美元费用。

## 机械完整性与供应商 usage

| 展示 | 回复 / 唯一 key | 终态 | prompt / completion / total token | reasoning token | 延迟 p50 / p95（ms） |
| --- | ---: | --- | ---: | ---: | ---: |
| `instant_message` | 144 / 144 | 144 `stop` | 374433 / 14161 / 464383 | 75789 | 4536 / 7120 |
| `single_text` | 144 / 144 | 144 `stop` | 331408 / 18884 / 418271 | 67979 | 4439 / 6858 |
| `default_voice` | 144 / 144 | 144 `stop` | 329689 / 19563 / 414379 | 65127 | 4371 / 6296 |
| **合计** | **432 / 432** | **432 `stop`** | **1035530 / 52608 / 1297033** | **208895** | 不跨展示合并 |

三组均没有 `incomplete.jsonl`，没有空回复或超时。Provider usage 是供应商返回值，reasoning 已包含在 total；不报告的字段不会补零。评测器 metadata 保存了 fixture hash、persona hash、预算身份、模型、提示时间和 base URL digest；每个结果保存用户输入、原始回复、状态快照、估算 token、供应商 token 和延迟。

本批是无 Runtime source tool 的角色质量批次，因此没有工具交换或权限写入；来源/权限/裁剪证据以 [来源主批](q02-next-source-loop-2026-10-02.md) 和 [edge-case 补充](q02-next-source-edge-2026-10-02.md) 为准。当前执行器没有把完整编译 system/context/history 序列化到 `results.jsonl`，所以本报告不声称拥有三组每请求的完整 HTTP payload/hash；预算与分项裁剪的逐请求审计仍以来源主批中已保存的 `actual-requests.jsonl`、`provider-events.jsonl` 和 prompt report 为准。

## 质量复核

### 通过或基本稳定的部分

- `technical_help` 的 36 条三展示回复都保留了 `asyncio.gather(..., return_exceptions=True)`、异常作为列表元素和完整代码结构；从本批 9 个代码示例（每展示 3 重复）抽取后实际执行为 **9/9 通过**。这证明代码样例在本批可运行，不替代对所有文字解释的审查。
- `goodbye` 三展示共 36 条均收口，没有追加问题；`identity` 保留了非官方 Demo 和当前部署未知的边界；`insufficient_shared_history` 没有把现实共同经历写成已确认事实。
- `teasing`、`stop_joking`、`low_mood` 和 `topic_switch` 的大多数回复能顺接角色语气；即时消息偏好没有把明确技术请求压缩成短句。
- `detailed_answer` 的三个类别、每类三条旅行建议，以及两个室内方案的活动/用时/注意事项结构在三种展示均保留；最终轮均输出了可勾选的 Checklist 结构。

### 仍然失败或有风险的部分

- `detailed_answer` 的充电宝规则在三种展示的 9 条关键回复中均没有提到来源快照要求的 **2025-06-28 境内航班 3C 标识和召回型号/批次限制**。同时多次把 `20000mAh` 当成可直接替代 Wh 的经验门槛；部分回复使用 5V 换算或补充“飞机上不能给充电宝充电”等未由本轮来源核实的说法。它们是来源完整性和事实谨慎问题，不是预算裁剪，因为本批没有来源工具且完整性字段已在独立来源复测中证明可进入请求。
- `topic_switch` 的 Raft 首轮普遍给出 `150ms~300ms`、`20ms~50ms` 等具体范围，并把“广播时间/心跳间隔/选举超时”混在一起。论文约束是关系式和实现条件，具体数值必须标为实现示例；这些回复的过度具体化仍需人工修正或再次定向复测。
- `default_voice` 只是 `origin=voice` 的文本 presentation 复测，不能说明语音合成、播放、真实渠道投递或用户 ACK。来源 edge-case 还显示该展示的 native write 没有实际发起，因此写入拒绝覆盖不完整。

## 结论

本批证明三种 presentation 的完整十二场景生成和供应商 usage 记录均可重复保存，预算没有导致生成中断或明显的分项丢失；它没有证明角色质量通过。来源规则遗漏、Raft 参数表述风险、`default_voice` 写入分支和真实渠道/播放层仍是开放门槛。Q02 保持未通过，默认 persona v4 不变，生产端点、生产预算和生产数据库未修改。

完整原始批次位于 `.local/research/q02/q02-full-gemini-v4-20261002/`，每个展示目录包含 `results.jsonl`、`metadata.json` 和隔离 `isolated_db/eval_isolated.db`；预算文件与三份 presentation fixture 位于该批次的 `fixtures/` 和根目录。
