# Q02 来源辅助三展示复测

日期：2026-10-03。执行不调用 AGY，由主代理直接执行和审查。模型请求使用当前 CW2 配置中的 `chat` 密钥在进程内认证 `https://mubai.website:8318/v1`；密钥没有写入仓库、请求日志或证据。

## 执行范围

使用当前 `character_scenarios.json` 中带有来源快照的 `detailed_answer` 与 `topic_switch`，只替换展示 profile，分别执行 `instant_message`、`single_text` 和 `default_voice`。每个 profile 为两个场景、四轮、三重复，共 24 条逻辑回复；合计 72 条。三组使用同一 Gemini 3.8 Flash High、同一 persona、同一提示时间和同一推荐预算。所有数据库和请求产物位于被忽略的隔离目录：

`.local/research/q02/q02-source-assisted-three-presentations-20261002/`

预算为上下文窗口 32768、输出预留/上限 8192、估算余量 15%、`scaled` 分项、历史 32、记忆候选 24、工具结果 131072 字节。价格元数据不可用，按用户授权使用 `--no-cost-ceiling`，不从 token 反推费用。

## 机械结果和用量

| 展示 | 回复 | 终态 | prompt / completion / reasoning / total | p50 / p95 延迟 |
| --- | ---: | --- | ---: | ---: |
| `instant_message` | 24/24 | 24 `stop` | 69858 / 7191 / 15472 / 92521 | 8802 / 11538 ms |
| `single_text` | 24/24 | 24 `stop` | 66581 / 8034 / 16634 / 91249 | 9075 / 17843 ms |
| `default_voice` | 24/24 | 24 `stop` | 68165 / 7960 / 19926 / 96051 | 8376 / 15061 ms |
| **合计** | **72/72** | **72 `stop`** | **204604 / 23185 / 52032 / 279821** | 不跨展示合并 |

全部回复都有供应商 usage，没有空回复、`incomplete` 或评测器终态失败。端点期间出现的连接错误均由适配器重试后成功，不改变样本终态。

## 质量复核

- 三个 profile 的法规轮均保留 `2025-06-28`、3C/CCC、召回型号或批次、额定能量和 100/160Wh 分支。
- 三个 profile 的换算说明均明确使用电芯标称电压，明确禁止使用 USB 输出端 5V 计算；审计器将“出现 5V”保留为人工复核线索，但逐条复核确认是正确的否定性警告。
- 三个 profile 的 Checklist 轮均保留 3C、召回和能量分支；`single_text` 的个别清单省略日期，但没有丢失生效规则或条件分支。
- 三个 profile 的 Raft 首轮均区分 `broadcastTime`、heartbeat interval 和 election timeout，并明确论文不规定固定毫秒数；150–300ms 只作为实现示例。
- 合并事实审计现在按 `(provider, model, presentation_profile, sample_key)` 去重，避免跨展示误报重复。结果为 `9/9` 完整覆盖、`missing=0`、`invalid=0`、`validation_errors=0`、`check_passed=true`。审计摘要的 `factual_correctness=not_assessed` 仍表示关键词审计不替代人工法律复核。

## 软件验证

- `uv run pytest`：1973 passed，46 个 Windows 专属跳过，1 个既有 `audioop` 弃用警告。
- Q02 定向回归：199 passed；事实审计测试：24 passed。
- `uv run pyright`：0 errors, 0 warnings。
- Ruff check/format 和 `git diff --check`：通过。

## 边界

这批收口了来源辅助内容在三种文本展示中的保真和审计身份问题。它不等于完整 Q02 发布批准：`default_voice` 仍是文本 profile；真实 TTS 已有独立模型→Runtime→CosyVoice→PlaybackService ACK 证据，但物理扬声器实际播放、用户听见/确认和 QQ/微信外部投递仍未通过；模型自动生成和关键词覆盖也不替代人工最终判断。默认 persona、生产模型路由、生产预算和生产数据库未修改。
