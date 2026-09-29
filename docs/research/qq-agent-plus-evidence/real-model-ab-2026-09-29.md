# CW-Q01/Q02 真实模型评估与事实审计

日期：2026-09-29。评估只使用 12 组合成四轮场景和隔离 Runtime 数据；没有向 QQ/微信发送消息，也没有读取用户的真实对话或记忆。CW2 当前保存的模型路由使用 HTTPS 8318 端口；应用聊天角色仍配置为 `gemini-3.1-flash-lite`，本次没有替用户切换默认模型。评估端点的 `/models` 提供所选三个模型 ID，但未提供可核对的价格。因此用户授权的无美元上限模式仍以每批 72 个逻辑请求限制执行，供应商 token 用量是原始报告值，**不是账单或美元费用**。

## 完整 A/B：候选角色卡 v2

基线为实施前 persona，候选为修复“本地模型驱动”误述、移除与考题重叠示例后的角色卡 v2。每模型 12 场景 × 4 轮 × 3 次重复 × 2 版本 = 288 条，共 864 条。四个互斥场景批次各限制最多 72 个逻辑请求；合并器核验了完整的 288 个唯一配对键、同一 fixtures/persona 哈希、模型 ID 和终态。端点偶发错误使 Opus 一批在 51/72 和 62/72 时中断，续跑只跳过已持久化的成功样本，第三次完成；失败请求与适配器内部重试可能增加实际 HTTP 尝试数。每条结果均有非空回复、`stop` 终态和供应商上报用量。

| 模型                       |    完成 |    延迟 p50 / p95 | prompt tokens | completion tokens | reasoning tokens | total tokens |
| -------------------------- | ------: | ----------------: | ------------: | ----------------: | ---------------: | -----------: |
| `gemini-3.8-flash-high`    | 288/288 | 5,885 / 10,827 ms |       686,321 |            24,633 |          133,020 |      843,974 |
| `claude-sonnet-4-6`        | 288/288 | 3,628 / 12,308 ms |       651,696 |            30,893 |           未上报 |      682,589 |
| `claude-opus-4-6-thinking` | 288/288 | 4,741 / 18,073 ms |       663,665 |            38,554 |           未上报 |      702,219 |

请求使用 OpenAI 兼容流式接口，`stream_options.include_usage=true`，单请求超时 120 秒；没有发送 temperature、top-p 或 seed。因此重复采样不能保证逐字重现。记录中的 `base_url_hash` 和角色卡哈希可用于比对配置，原始 URL 和凭据没有进入仓库。各模型的 [Gemini 原始回复与盲表](character_scenarios_ab_gemini_3_8_flash_high/)、[Sonnet 原始回复与盲表](character_scenarios_ab_claude_sonnet_4_6/)、[Opus 原始回复与盲表](character_scenarios_ab_claude_opus_4_6_thinking/) 均包含 `results.jsonl`、`metadata.json`、`summary.json`、`blinded_review_template.md` 和脱盲映射。评分前应先阅读盲表，再打开映射。

## 角色质量与后续修订

AGY Gemini 3.8 Flash High 对 Sonnet 的 144 对 v2 回复做了匿名配对评审：候选优先 30、基线优先 19、平手 95；技术求助为候选 6 / 基线 0 / 平手 6，停止玩笑为候选 4 / 基线 0 / 平手 8，被调侃则为候选 3 / 基线 5 / 平手 4。完整逐场景等级与配对判断见 [Sonnet 首次盲评报告](character_scenarios_ab_claude_sonnet_4_6/blind_judge_report.json)。当时 Gemini 的独立 AGY 盲评因认证服务器 DNS 失败退出，Opus 尚未评分，后续复评如下。模型评分只是辅助，不等于人工通过。

9 月 30 日网络恢复后，AGY High 对三模型的固定 `r0` 子集重新做匿名配对评审，每模型覆盖 12 场景 × 4 轮 = 48 对，合计 144 对；这**不是**对全部 432 对的复评。原始判定与脱盲后的逐对结果见 [分层复评 JSON](real-model-ab-stratified-rejudge-2026-09-30.json)。

| v2 回复模型              | 候选优先 | 基线优先 | 平手 | 硬边界违规 |
| ------------------------ | -------: | -------: | ---: | ---------: |
| Gemini 3.8 Flash High    |        7 |        1 |   40 |          0 |
| Claude Sonnet 4.6        |        4 |        6 |   38 |          0 |
| Claude Opus 4.6 Thinking |        5 |        4 |   39 |          0 |

Sonnet 相同 `r0` 样本与第一次评审相比有 13/48 项判定不同，主要是偏好和平手之间的主观取舍；第一次的 `r0` 为候选 9 / 基线 7 / 平手 32，本次为 4 / 6 / 38。因此不能用“30 比 19”断言候选角色整体胜出，更不能跨模型合并票数。手工复核被调侃样本时，两版大多均符合边界，偏好差距不足以证明系统性退化。新盲评识别出 Gemini 基线与 Sonnet 候选各一个以 USB 输出 `5V` 推算额定能量的错误，也指出 Sonnet 候选在 `topic_switch:r0:t4` 突然自称没有进食体验，偏离了该场景的角色口吻；这些判断可按逐对回复复核。

但新盲评**仍漏判了其审阅的 6 条充电宝回答都缺少 3C/CCC 与召回限制**，尽管评审提示已给出该事实；下文的独立审计覆盖全部 18 条。JSON 保存的是 AGY 原始等级，不能作为事实准确性或发布门槛的通过记录。官方来源审计优先于模型给出的 `pass`；三模型 `r0` 子样本无硬边界违规只说明此次有限样本未观察到，不代表完整 A/B 或线上对话已通过。

盲评发现 Sonnet 候选在 `identity:r2:t2` 一次将原作错误归给 Saga Planets。[原作官方作品页](https://www.yuzu-soft.com/products/sothewitch/)确认《サノバウィッチ》属于ゆずソフト。角色卡随后补入正确来源，形成候选 v3。v3 没有伪称与 v2 完整 A/B 相同：仅针对身份场景重新跑 3 模型 × 24 条 = 72 条，结果保存在 [Gemini v3 身份复测](character_scenarios_ab_gemini_3_8_flash_high_identity_post_audit/)、[Sonnet v3 身份复测](character_scenarios_ab_claude_sonnet_4_6_identity_post_audit/) 与 [Opus v3 身份复测](character_scenarios_ab_claude_opus_4_6_thinking_identity_post_audit/)。候选 v3 的制作方回答为 9/9 正确；“本地还是云端”回答 9/9 未再断言本地模型。三个模型各 24/24 完成并上报用量。候选 v3 persona 哈希为 `7f9daf3c6cefd57f`；完整 A/B 的候选 v2 哈希为 `b477415eb9903b0d`。

在隔离的真实 Runtime/API 对话中，三个模型均生成 `completed`、写入一条互动诊断且诊断模型路由与请求一致。候选 v3 的 Gemini 身份提问也在完整 Runtime 路径下完成，角色明确承认不能确认部署方式。这里使用临时 SQLite 和假 TTS；`delivery_status=null`，不能当作真实渠道送达或用户已听见。

## 未通过的事实准确性审计

三模型的完整 v2 A/B 在 `detailed_answer` 第 2 轮各给出 3 次基线、3 次候选，共 18 条“国内航班充电宝规定”回答；**18/18 均未提到 3C/CCC 标识或被召回型号限制**。而[中国民航局 2025 年公告](https://www.caac.gov.cn/XWZX/MHYW/202506/t20250626_227805.html)规定，自 2025 年 6 月 28 日起无 3C 标识、标识不清或被召回型号/批次的充电宝不得乘坐境内航班。当前合成场景的预期项没有覆盖这一新规，因此格式完整、代码完整与模型盲评分都不能替代时效性事实核验。一个 Sonnet 候选样本还用 `5V × 20000mAh` 直接估计额定能量；[民航局换算说明](https://www.caac.gov.cn/big5/www.caac.gov.cn/XXGK/XXGK/TZTG/201511/t20151105_11173.html)要求使用产品标称电压和标称容量，不应把输出电压直接当作电池标称电压。

这些发现说明 Q02 的人格规则和链路已有可检验改进，但**不能宣称发布门槛已全部通过**：v3 仍需一次同版本的完整盲评和人工分歧复核；涉及最新法规的任务还需要可信来源校验方案。真实 QQ/微信投递、客户端播放、设备环境语音和多账号资料边界也未由这些隔离测试覆盖。
