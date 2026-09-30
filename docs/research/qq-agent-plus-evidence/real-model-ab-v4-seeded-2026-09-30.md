# v4 实际关系状态下的真实模型复核

2026-09-30。新夹具为 `1a2281443a1c0fc5`，运行器 1.2.0，[v3](persona-baseline-v3-evaluated-2026-09-30.md.txt)/[v4](persona-candidate-v4-evaluated-2026-09-30.md.txt) persona 分别为 `7f9daf3c6cefd57f` / `3ab060352dcb8e25`，PromptCompiler 为 `7c85a6ef6515d89c`。使用当前 CW2 配置的 HTTPS 8318 端点和用户授权的无美元费用上限；每个模型三批、每批最多 96 次逻辑请求，完成记录原目录续跑。没有设置 temperature、top_p 或 seed，采样由端点默认值决定。失败/重试可能另有账单，不能由成功回复的 token 反推全部费用。

三个模型均已完成 288/288 条，共 864 条、432 对。每模型 168 条 familiar、120 条 acquaintance，全部实际状态/计划等于独立生产 reducer 的 48 轮预期值，配对键完整且唯一；raw JSONL 为原批次字节串联，没有补写或替换旧状态样本。主代理逐条读取全部回复，使用同一任务、边界和口吻标准复核；看过版本标签，因此均为**非盲模型判断**，不能冒充独立盲评或人工通过。新一轮生成和复核未调用 AGY。

| 模型                     | 候选优先 / 基线优先 / 平手 | 任务失败 v3 / v4 | 选定法规完整覆盖 |
| ------------------------ | -------------------------- | ---------------- | ---------------- |
| Gemini 3.8 Flash High    | 6 / 10 / 128               | 11 / 12          | 0/6              |
| Claude Sonnet 4.6        | 4 / 8 / 132                | 15 / 17          | 0/6              |
| Claude Opus 4.6 Thinking | 2 / 9 / 133                | 13 / 15          | 0/6              |

每个版本每模型 144 条回复。明确现实动作边界失败为 Gemini 基线 1 条（无来源的日常进食习惯），其他模型与候选没有在本样本出现明确失败；模糊暗示另列风险。没有据此宣称任意长期对话都安全。所有模型都有已知产品架构被拒答、法规来源漏项及技术说明问题，**Q02 未通过**，默认角色卡质量不得由 CI 通过推定。

初轮每模型三批、三个模型同时生成，发生连接中断后显式续跑先降至最多两批、再降至一批。保留的成功回复混合了不同负载与时间段，以下延迟只能作观察数据，不能用于因果速度排名或证明 persona 降低延迟。失败尝试原文另存，供应商是否对失败/重试收费未知。

## Gemini 完整复核

[原始回复](character_scenarios_ab_gemini_3_8_flash_high_v4_seeded/results.jsonl)、[144 对判断](character_scenarios_ab_gemini_3_8_flash_high_v4_seeded/primary-review.json)和[代码执行](character_scenarios_ab_gemini_3_8_flash_high_v4_seeded/primary-code-execution.json)均已保存。六份 `return_exceptions=True` 完整示例均实测正确；候选两条默认 gather 说明错误声称其他子任务的后续异常可能因未检索而告警。基线一条 Raft 回答将收到投票请求误作重置条件，候选一条数值范围不能支持自述的数量级间隔。基线还将可哈希与不可变直接等同，字典推导式本身正确。按 [Python 可哈希定义](https://docs.python.org/3.12/glossary.html#term-hashable)、[gather 文档](https://docs.python.org/3.12/library/asyncio-task.html)和 [Raft 论文](https://raft.github.io/raft.pdf)逐项区分核心正确部分与附加错误。

候选三次在用户转为感谢后继续抗议调侃，数条闲聊额外推断用户冷或累；这些口吻/贴合度问题不冒充技术失败。充电宝来源覆盖全部漏项，基线一条在 3.7V 条件下给出错误的 Wh/mAh 换算范围。两条候选 5V 表述仅列人工复核线索，没有凭关键词宣称它们执行了错误算式。

## Sonnet 完整复核

[原始回复](character_scenarios_ab_claude_sonnet_4_6_v4_seeded/results.jsonl)、[144 对判断](character_scenarios_ab_claude_sonnet_4_6_v4_seeded/primary-review.json)和[执行与边缘探测](character_scenarios_ab_claude_sonnet_4_6_v4_seeded/primary-code-execution.json)均已保存。六份完整第三轮程序均与其声称输出相同；这没有使同回复附加技术说明自动通过。五条说明把 `KeyboardInterrupt`（部分还含 `SystemExit`）当作普通 gather 返回结果供 `BaseException` 分拣。主代理的独立隔离探测显示这些异常逃逸 Runner，子任务 `CancelledError` 才作为列表元素返回；依据 [CPython 3.12 Task 实现](https://github.com/python/cpython/blob/3.12/Lib/asyncio/tasks.py)复核，不能将三者混为一谈。

候选另有完整程序在 main 返回后 Runner 取消未结束任务、以及声称 `ValueError.message` 属性存在的问题。基线一条 Raft 提问未答，其余五条虽说明随机化，数值组合仍不满足同时约束。候选一次把商业街/露天摊位列作室内活动类别。六条法规回答中只有候选一条提 3C，全部漏召回；最终 Checklist 仍未补齐，有的还丢掉前文 100–160Wh 需批准的条件。

## Opus 完整复核

`claude-opus-4-6-thinking` 已完成 288/288 条、144 对。主代理逐条验证结果唯一、用户输入与夹具一致、强类型状态快照合法，全部状态与计划等于独立生产 reducer 的 48 轮预期值：168 条 familiar，120 条 acquaintance。没有使用修复前的回复补样本。

原始回复见 [results.jsonl](character_scenarios_ab_claude_opus_4_6_thinking_v4_seeded/results.jsonl)，元数据、失败尝试和完整校验记录同目录。主代理看过版本标签，以下为**非盲模型复核**，不是独立盲评或人工验收；保存 [144 对逐项判断](character_scenarios_ab_claude_opus_4_6_thinking_v4_seeded/primary-review.json)供复核。原匿名表以文本保存，保留模型回复原有代码围栏。

候选优先 2、基线优先 9、平手 133。候选自然表达偏甜的口味，纠正了基线一次以没有实体为由拒答；停止玩笑与告别的直接边界均守住。候选的情绪倾听、部分室内方案和技术附加解释有退步，**Q02 仍未通过**。逐项任务保真失败为基线 13/144、候选 15/144；这些计数按当前复核标准定义，不能与旧 AGY 分数直接相减。

主要失败及证据：

- `technical_help:r1:t1:candidate_v4` 的文字说其他任务不会被 gather 自动取消，但完整代码退出 main 后交由 asyncio.run 关闭。主代理检查并运行该例，只有捕获异常输出，没有声称的 ok 完成。另六份第三轮完整示例均成功产生三个正常/异常结果。见 [执行记录](character_scenarios_ab_claude_opus_4_6_thinking_v4_seeded/primary-code-execution.json)。依据 [Python gather 文档](https://docs.python.org/3.12/library/asyncio-task.html)和 [Runner 文档](https://docs.python.org/3.12/library/asyncio-runner.html)区分 API 行为与完整程序生命周期。
- 六份 Raft 回答解释了随机化选举，但所给心跳与选举数值范围不能保证同时满足约束；候选另有把收到投票请求当成重置条件的问题。按 [Raft 论文图 2 及 §5.6](https://raft.github.io/raft.pdf)复核，不能只因出现随机化或一个不等式就给技术质量通过。
- 六份充电宝回答都漏掉固定来源快照的 3C 标识与召回型号/批次要求；独立覆盖审计为 0/6 完整、6/6 漏项、1 个 5V 换算线索、0 校验错误。它只核对[选定民航局来源](https://www.caac.gov.cn/XWZX/MHYW/202506/t20250626_227805.html)，不宣称覆盖当前全量法规。基线的 5V 示例还混淆了常见 USB 输出电压与电芯额定电压；后续 Checklist 没有补齐漏项。
- 身份第三轮双方都将已知 ChatWaifu NEXT 运行系统与未知模型部署一起称为未知，造成推辞；候选两次把有顶棚/骑楼老街列入完整室内方案。未来现实共同出游的模糊暗示另列风险，未冒充具体行动承诺。

| 版本 | p50 / p95（ms） | Provider 输入 / 输出 / 总 token |
| ---- | --------------- | ------------------------------- |
| v3   | 4552 / 22669    | 399487 / 20409 / 419896         |
| v4   | 4566 / 18415    | 410451 / 20475 / 430926         |

每个版本 144 条成功记录均有 Provider usage；reasoning 未单独报告，价格来源和实际美元账单未知。分位数对成功逻辑请求的延迟排序后做线性插值，包含成功请求内部的重试耗时；此离线 A/B 不能代替实时音频延迟验收。

## 后续候选

主代理准备了 [v5 独立候选](persona-candidate-v5-primary-2026-09-30.md.txt)，582 估算 persona token。规则区分已知产品架构与未知部署，优先情绪倾听，核对完整示例的生命周期、参数、单位及任务条件，要求时效性来源核查。没有将充电宝固定答案写进角色卡。

独立对 48 轮实际状态、即时消息/普通文本/语音三种展示、4096/8192 两个窗口执行 288 次真实 PromptCompiler 编译，候选全文均保留，未发生额外模型调用。见 [编译验证](persona-v5-primary-compile-validation-2026-09-30.json)。它只证明输入装载和预算容纳，不能证明模型遵守规则；新候选未得到真实模型或人工质量通过。

## 运行器估算修复

上述付费生成全部结束并归档后，主代理将运行器更新为 1.2.1：dry-run 现在逐份编译所选 persona，并按实际各场景编译量相加再外推轮数和重复次数。旧实现只编译默认角色卡，会漏算候选长度差；同一回归用例在旧实现失败、修复后通过。39 项运行器测试、Python 全集 1564 项（46 个平台用例跳过）、Ruff 与全量 Pyright 通过。详见[估算修复记录](dry-run-persona-estimate-fix-2026-09-30.md)。

1.2.1 没有回写本报告的 1.2.0 元数据、原始回复或 Provider usage。它仍以每场景首轮、空历史、8192 窗口编译量作粗估，后续历史和完成长度未知，不能当作账单上限。
