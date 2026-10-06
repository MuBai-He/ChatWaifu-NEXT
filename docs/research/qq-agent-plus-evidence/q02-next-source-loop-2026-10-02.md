# Q02 下一轮来源条件与清单保真复测

日期：2026-10-02。执行约束：不调用 AGY，由主代理直接执行和审查。源码提交 `e6093459ffa5105892e61172af5ca1804dd89a9b`，评测器 1.14.0，默认 persona v4，目标模型 `gemini-3.8-flash-high`，端点为当前 CW2 配置的 HTTPS 8318。生产数据库、生产配置、默认 persona 和凭据均未写入或保存。

## 执行范围

本批使用三个相互独立的隔离 Runtime/SQLite，会话顺序相同，每种展示方式 4 轮，共 12 条逻辑回复和 18 个 Provider 回合：

| 展示 | 轮次 |
| --- | --- |
| `instant_message` | 目录读取、读取目录实际返回的 HTTPS“信息公开”链接、禁止新工具的证据缺口、禁止新工具的清单 |
| `single_text` | 同上 |
| `default_voice`（`origin=voice`，只输出文字） | 同上 |

来源读取只允许用户指定的民航局目录和目录返回的实际 HTTPS 链接。每次 READ 都经过独立 `allow_once`，没有持久授权；搜索、写入、提醒和其他适配器在本批隔离。没有真实渠道投递、麦克风、TTS、扬声器或用户 ACK 验收。

端点 `/v1/models` 返回 200，清单包含 `gemini-3.8-flash-high`。本批使用冻结预算：上下文窗口 32768、输出预留 8192、请求 `max_tokens=8192`、估算余量 15%、`scaled` 分项、历史上限 32、记忆候选 24、工具结果上限 131072 字节。由此得到实际估算输入上限 `floor((32768-8192)/1.15)=21370`。

## 证据结果

- 12/12 逻辑回复为 `completed`，18/18 Provider 回合有终态；所有最终可见文本都等于最后一轮实际 `LlmTextDelta` 拼接结果。
- 6 次 `web.read` 成功：3 次民航要闻目录、3 次该目录实际返回的 `https://www.caac.gov.cn/XXGK/XXGK/` 信息公开页。没有猜测 URL 或改写目录返回的 `http://` 新闻链接。
- 目录结果保留 20 个链接并标记 `links_truncated=true`；模型三种展示方式均明确说明新闻正文没有被读取，且 HTTP 链接不满足当前 reader 的 `read_url_schemes=["https"]` 限制。
- 两个明确禁止新工具的后续问题均为单一 `auto` 请求，没有工具 schema 或新 Runtime 操作；回复保留“仅覆盖目录/信息公开门户、现行充电宝规则缺少原文”的未核实边界。
- 6/6 权限决定为 `allow_once`，3 个隔离库的 `permission_grants` 均为 0；18 次被记录的网络请求均没有 Authorization 或 Cookie。原文正文没有进入 durable `skill_runs` 审计字段。

输入审计中，18 个请求的 `estimated_input_tokens` 全部不超过 21370；最大参考估算 9626，最大供应商 prompt 8329。`omitted_history_indices` 和 `omitted_tool_preamble_indices` 均为空。目录/信息公开页的实际正文、标题、URL、发布时间字段和 HTTPS 能力字段都进入对应的 tool exchange；`body_sha256` 是原始响应体哈希，提取文本另行保留，不能混为同一哈希口径。

供应商返回 usage 合计为 prompt 111855、completion 4710、total 120606 token；reasoning 4041，仅 8/18 回合报告，已包含在 total，不补零。端点没有可信价格元数据，本批不推算美元费用。

| 展示 | 回复耗时 p50 / p95（毫秒） | Provider prompt / completion / total |
| --- | ---: | ---: |
| `instant_message` | 6519.5 / 10333 | 37465 / 1489 / 39742 |
| `single_text` | 7061 / 10109 | 36639 / 1620 / 39280 |
| `default_voice` | 8620 / 10907 | 37751 / 1601 / 41584 |

p95 使用 4 个样本的 nearest-rank，不能作为实时语音性能或 presentation 的因果排名。

## 归因与边界

本批没有发现 Runtime 丢失来源正文、错误复用来源链接、权限越权或预算导致来源字段从请求中消失的问题。来源已完整进入请求而模型仍然出现的格式/表达差异，应归入模型能力或输出质量，而不是预算不足。本批的手工复核知道设计，属于主代理非盲复核，不是独立盲评或人工批准。搜索失败、原文不可读及读取后复合写入的补充结果见[边界补充复测](q02-next-source-edge-2026-10-02.md)。

本批直接覆盖了“来源已提供/需要继续查证”和“明确禁止新工具”。搜索失败、原文不可读和“读取后复合写入”的实时分支已在边界补充批次中单独受控验证，但 `default_voice` 的 native write 没有实际发起，因此不能宣称三种展示的写入拒绝均已覆盖。结合完整三种 presentation 批次仍存在的来源规则遗漏和技术表达风险，Q02 仍未通过，不能据此批准默认 persona 或宣布真实渠道验收完成。

原始请求、工具交换、Provider delta/usage、权限决定、网络记录和隔离数据库位于忽略目录：`.local/research/q02/q02-next-source-loop-20261002/`。关键文件 SHA-256：`results.jsonl` `f6f7ab5b0cf979e7d0698ce84888afb450b502cd4f752831f90dfeeb06ed833b`；`actual-requests.jsonl` `dbaaba6c0485cea2257e2cecda34c165f303908908461411f3ea06c2daaa77a6`；`provider-events.jsonl` `8e520c8b6ffd404950b2cee94de663b4b943909b12103e13a4c5acda7643e277`；`source-executions.json` `9fcda775e02f2a14d9e972daeaa9ca0698dd3a802608ad7634d0752e66d911d4`。

## 软件验证

```text
uv run pytest services/runtime/tests/test_runtime_source_evaluation.py \
  services/runtime/tests/test_tool_intent.py \
  tools/tests/test_evaluate_character_scenarios.py
144 passed, 1 existing audioop deprecation warning
```
