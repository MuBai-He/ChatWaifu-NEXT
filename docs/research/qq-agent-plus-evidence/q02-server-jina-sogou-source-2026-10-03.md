# Q02 服务器 Jina/Sogou 来源链路复测

日期：2026-10-03。执行位置为 `192.168.1.103`（`mubai-6133`），没有在 Mac 上承担公网来源读取。生产 Runtime、生产数据库和生产搜索配置均未修改；当前实现放在服务器 `/tmp/cw2-jina-sogou-probe` 临时目录中。没有调用 AGY。

## 服务器和传输边界

- `https://127.0.0.1:8765/v1/runtime/health` 返回 HTTP `200`，Runtime 状态为 `ok`，数据库为 `ready`。
- CLIProxy 的本机端口为 `8317`，公网 OpenAI-compatible 端点为 `8318`；来源探测阶段不向模型端点发送请求，后面的三次受控问答单独计入模型证据。
- SearXNG 为 `18080`，Crawl4AI 为 `11235`；本批使用显式 `jina_sogou`，实际路径为 Jina Reader 对 Sogou 搜索页的 Markdown 投影。
- 每次搜索和读取均传递 `dns_resolver=cloudflare`，符合服务器 Mihomo Fake-IP DNS 运行条件。

## 搜索结果

工作树实现用 `PublicWebSearch(search_provider="jina_sogou")` 在服务器进程内运行。`web.search` 对模型仍最多返回 5 条；Jina/Sogou 内部先保留最多 20 条候选，再做 HTTPS、媒体、导航和域名过滤，避免官方结果排在第 6 条时被提前截掉。

| 原始查询 | 有效查询 | 结果 | 观察 |
| --- | --- | --- | --- |
| `民航局 充电宝 3C 召回`，域名 `caac.gov.cn` | 同原始查询 | 1 条 CAAC：2025-06-26 公告，`XWZX/MHYW/202506/t20250626_227805.html` | `3C`/召回公告稳定命中 |
| `充电宝 100Wh 160Wh 不得托运 民航局`，域名 `caac.gov.cn` | 同原始查询 | 3 条 CAAC：2015-08、2014-08、2015-11 公告 | 官方结果在扩大候选池后不再被噪声截掉，来源仍需结合当前公告读取 |
| `民航局 旅客 携带 充电宝 规定 额定能量`，域名 `caac.gov.cn` | `民航局 充电宝 旅客 携带 规定 额定能量 3C 召回 最新 修订 生效` | 2025-06-26 公告 + 2015 官方公告 | 当前查询同时发现新 3C/召回公告和 Wh 规则公告 |

查询响应的 provider body hash 会因 Sogou/Jina 每次检索变化，最后一次三查询运行分别记录为 `3e667cd1d012035e3588f79f3473af2c0d7ab27c5accc3997a14a2961b150`、`60058c474faf7e42b066ea7d40e52e2a0c9ca3afc978b1e5afd77bc4d0ea2190` 和 `2927d27b49020aaad417ed9111c57f33d1df1a0c41d6c6d42f0386d52987acab`。搜索延迟约 6.3–17.4 秒；这不是实时体验保证。

## 原文读取

服务器 `PublicWebReader(reader_provider="builtin")` 对搜索发现的两页 CAAC 原文使用 `dns_resolver=cloudflare`、`fresh=true` 读取成功：

- 2025-06-26 公告：HTTP 成功，正文 845 字符，body SHA-256 `d26587c6427671abdc5c2dd0df69f44e18545bb90dd6cb14b47099e73683f4ec`；正文含 `3C`、`召回`，没有把旧 Wh 条款误算成该公告内容。
- 2015-11-05 公告：HTTP 成功，正文 725 字符，body SHA-256 `6637211634261e536f642e315ba1088a873375fa258c193b484e020bda301cf5`；正文含 `100Wh`、`160Wh`、`严禁托运`、`每名旅客不得携带超过两个` 和能量换算公式。

这两页合起来覆盖本次 Q02 审计要求的来源条件，但 2015 页属于历史公告；模型必须把 2025 现行安全通知与仍适用的 Wh 规则分开标注，不能仅凭搜索片段合并为未经核实的“新规”。

## 服务器本机 Gemini 受控问答

在同一服务器上把上述两页完整正文交给 `gemini-3.8-flash-high`，要求分开标注来源日期、URL 和规则，连续三次均 `finish_reason=stop`，三次都覆盖 `3C`、召回、`100Wh`、`160Wh`、托运、数量和飞行中禁用。回复摘要哈希与供应商 usage 如下：

| 重复 | 回复 SHA-256 | 字符数 | prompt / completion / reasoning tokens | 延迟 |
| --- | --- | ---: | ---: | ---: |
| 0 | `d3621c26b4b12d6fab32ba5bd16ecb13ef71ac0aa533de7d8e0b8fd3d49e978e` | 793 | 1375 / 1343 / 817 | 5728 ms |
| 1 | `89047eb645dd3b04fc9484365ee20444fc7e338aeeb9098ebdcfe3efa31b8e8c` | 846 | 1375 / 1382 / 840 | 5128 ms |
| 2 | `4d39a626ef515206149fb98a443f8527fc8cd84b05332e6e3cce697796967013` | 813 | 1375 / 1313 / 786 | 5219 ms |

这是来源齐全、单问题、无工具决策压力的受控检查；它支持“当前来源可被模型正确合并”的局部结论，不替代完整十二场景、多轮工具交换、事实审计和三种 presentation 的独立复核。

## 结论和后续边界

本批证明服务器网络、Cloudflare DNS、Jina/Sogou 发现、官方 CAAC 原文读取和来源收据均可工作；它修复了候选池过早截断、搜索图片噪声和 QQ 隐私页误收录。Raft 查询仍可能返回微信文章等低权威候选，需在真实模型批次中按正文来源和技术审计单独判断。

这不是 Q02 放行证据：本批没有完成真实 Gemini 对全部场景的质量复评，也没有替代充电宝事实审计、Raft 参数正确性、三种 presentation、真实 QQ/微信投递、TTS 播放完成 ACK 和用户 ACK。默认 persona、生产模型预算和生产数据库保持不变。
