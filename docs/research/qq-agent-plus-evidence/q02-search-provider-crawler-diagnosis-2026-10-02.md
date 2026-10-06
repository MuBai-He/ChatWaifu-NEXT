# Q02 搜索供应商与爬取链路诊断

日期：2026-10-02。本文记录 1.16.0 Runtime source-tool 评测中来源发现失败的边界，不把网络失败计为模型质量结论。

## 外部参考

用户提供的 LobeHub 页面当前只返回 React Router 迁移占位文本，未包含可执行配置。对应仓库中的原始文档为 [online-search.mdx](https://raw.githubusercontent.com/lobehub/lobe-chat/main/docs/self-hosting/advanced/online-search.mdx)，其设计把 `SEARCH_PROVIDERS` 与 `CRAWLER_IMPLS` 分开配置，支持 SearXNG、Search1API、Google、Brave、Tavily 等搜索或抓取服务，并要求 SearXNG 开启 JSON 输出。

这说明“搜索发现”和“网页正文读取”应当是可替换的供应商边界；它不能证明当前 CW2 已接入 LobeHub 的实现或任一外部服务。

## CW2 当前实现

- `PublicWebSearch` 将发现服务固定为 `https://lite.duckduckgo.com/lite/`，只解析 DuckDuckGo Lite HTML，并把挑战页作为 `web_search_challenge` 失败返回。
- `PublicWebReader` 使用固定 `ChatWaifu-NEXT/0.1 public-source-reader` User-Agent、`trust_env=False` 的直连 HTTP；DNS 只能选系统解析或 Cloudflare DoH。
- `system` 解析会复用当前运行环境的地址结果。若该环境提供代理 Fake-IP，公共 CAAC 域名会被公网地址校验拒绝；Cloudflare 解析只解决这一层，不解决搜索供应商挑战。
- Runtime 在搜索失败或原文读取失败时不把片段或记忆当成已核实事实，返回明确的未核实回退。这是正确的安全边界，但会使 Q02 的事实覆盖保持失败。

## 可复现实测

同一查询 `民航局 充电宝 3C 召回`、同一网络和直连请求：

| 请求指纹 | HTTP | 页面特征 | 结论 |
|---|---:|---|---|
| `ChatWaifu-NEXT/0.1 public-source-reader` | 202 | `challenge-form`、`/anomaly.js`、无 `result-link` | 供应商挑战，不能当作空结果 |
| 普通 Safari 浏览器 User-Agent | 200 | 有 `result-link`、无挑战标记 | 供应商允许该请求形态 |

评测批次还观察到：系统 DNS 解析 CAAC 时返回 `web_url_forbidden`；切换 Cloudflare DNS 后 `web.read` 可成功读取官方页面，但 DuckDuckGo Lite 仍会在不同请求中返回挑战。1.16.0 的 Cloudflare Runtime 批次因此出现 `2/3` 充电宝轮次搜索成功、随后批次 `0/3` 搜索成功的供应商不稳定性；最新批次的 `fact-audit --check` 为 `0/3` 覆盖。对应原始产物保存在被忽略的 `.local/research/q02/` 目录。

## 候选服务探针（2026-10-02）

本节是从当前主机发出的独立网络探针，不是供应商 SLA，也不代表已经授权 CW2
把用户查询发送给这些服务。探针只使用公开页面和不含密钥的试用路径，没有读取或
写入任何项目凭据。

### 搜索发现

| 服务 | 接口与能力 | 当前主机探针 | 成本/限制依据 | 判断 |
| --- | --- | --- | --- | --- |
| **Firecrawl Search** | `POST https://api.firecrawl.dev/v2/search`；返回 URL、标题、描述；可用 `scrapeOptions` 同步抓正文；支持 `includeDomains`/`excludeDomains` | 同一查询 `CAAC 充电宝 3C 召回`：HTTP 200，约 2.08 s，3 条结果中 CAAC 官方页第一 | 官方计费文档：搜索每 10 条 2 credits；无密钥试用路径按 IP 限流；注册免费计划 1,000 credits/月、搜索 10 req/min | **当前最快可落地的发现主路**；生产仍应配置密钥、额度上限和重试退避 |
| **Tavily Search** | `POST https://api.tavily.com/search`；`basic/advanced` 深度、时间范围、国家、硬域名过滤；可返回最多 3 个相关片段或 `include_raw_content` | 不带密钥 HTTP 401；没有在本机验证带密钥路径 | 官方文档：basic 1 credit、advanced 2 credits；免费 1,000 credits/月；直接 REST 请求需要 API key | 质量和域名过滤合适，但必须先取得可用密钥；不能把文档中的 keyless 说明当成当前 REST 已验证事实 |
| **Brave Search API** | `GET https://api.search.brave.com/res/v1/web/search`；独立索引、freshness、国家/语言、最多 20 条、额外片段与 Goggles 重排 | 未发送无密钥请求，避免把 401 当成质量结论 | 官方页面当前标价 $5/1,000 requests，并给每月 $5 免费额度；搜索只返回索引结果/片段，正文需另行读取 | **适合作为第二搜索源**；需要密钥和独立 crawler |
| **自托管 SearXNG** | `/search?q=...&format=json`；聚合多个引擎，无供应商 API 费；需在 `settings.yml` 明确启用 JSON | 公开实例探针 5 个中出现 418 challenge、403、429 和安全检查页；即使 `searx.space` 显示 100% 搜索成功，也不能复现 JSON | 官方文档明确：未启用 JSON 的实例会返回 403，公共实例常禁用该格式 | **只推荐自托管或受控实例**，不把随机公共实例写入默认配置 |
| **Exa Search** | AI 定向搜索，可选 `contents`/highlights 和 livecrawl；搜索、正文在同一 API 边界 | 未发送无密钥请求 | 官方价格页：Instant Search $4/1,000 请求（最多 10 条），Contents $1/1,000 页；新账户每月 $10 credits | 技术文档/语义检索有吸引力，但对 Q02 的通用法规发现成本和集成复杂度高于 Firecrawl |

### 正文抓取

| 服务 | 能力 | 当前主机探针 | 成本/限制依据 | 判断 |
| --- | --- | --- | --- | --- |
| **Jina Reader** | `https://r.jina.ai/<URL>` 转换成 Markdown；支持等待 CSS 选择器、超时、token budget、缓存控制和动态页面等待 | CAAC 官方首页 HTTP 200、约 17.20 s；同一官方文章 HTTP 200、约 6.31 s、12,969 bytes；正文保留 `3C`、召回型号/批次和 `6月28日` | 官方 Reader 页面说明可无密钥使用，API key 只提高 rate limit；默认会缓存，可用 `X-No-Cache`/`X-Cache-Tolerance` 控制 | **首选轻量正文 reader**；必须记录缓存时间并允许法规查询强制 fresh |
| **Firecrawl Scrape** | `/v2/scrape`；Markdown/HTML/links/screenshot/JSON，供应商负责代理、缓存和 JS-rendered 页面 | 同一 CAAC 文章 HTTP 200、约 15.30 s，返回 `statusCode=200`、`creditsUsed=1`；无密钥路径可用 | 官方计费文档：Scrape 1 credit/page；免费计划 1,000 credits/月、2 并发浏览器，匿名路径按 IP/日限流 | **Jina 失败或页面需要更强渲染时的回退**；正文和搜索也可由同一供应商承担，但成本较高 |
| **Tavily Extract** | `/extract`；单次 1--20 个 URL，basic/advanced，返回 `results` 与逐 URL `failed_results` | 未发送无密钥请求 | 官方文档：basic 每 5 个成功 URL 1 credit，advanced 每 5 个 2 credits；单 URL 超时 10/30 s 默认 | 适合已知 URL 的批量读取，但当前没有可用密钥证据 |
| **Browserless** | 托管或自托管 Chromium；适合需要真实浏览器、交互和复杂 JS 的页面 | 未作浏览器会话探针 | 官方价格页：Free 1,000 units/月、2 并发；Prototyping $25/月 20k units；1 unit 最多 30 秒浏览器连接 | **最后一级回退**；对普通政府公告过重，成本和运行面大 |

### 同一来源的正文对照

对官方文章
`https://www.caac.gov.cn/XWZX/MHYW/202506/t20250626_227805.html`，Jina Reader 和
Firecrawl Scrape 都返回了标题、来源日期和正文条件；Firecrawl 的 Search+Scrape
组合也把官方页排第一，但一次 5 条结果的探针消耗了 7 credits（搜索 2 + 每页抓取
1 的计费规则）。这证明候选服务能把资料送入模型，不能证明供应商长期稳定或模型
已经通过 Q02；仍需在 Runtime 的域名过滤、来源收据、截断和事实审计边界内复测。

## 推荐落地顺序

1. **Q02 快速收口组合**：Firecrawl Search 做发现，Jina Reader 做首选正文读取，
   Firecrawl Scrape 只在 Jina 超时、内容为空、需要 JS 等明确失败类型时回退。搜索和
   读取仍走同一套 `https`、公网地址校验、重定向上限、域名 allowlist 和来源收据，
   供应商失败必须保留为可审计错误，不能降级成“没有结果”。
2. **本地优先组合**：准备稳定机器后自托管 SearXNG JSON，把它作为可切换的搜索源；
   不使用随机公共实例。正文仍沿用 Jina/Firecrawl 的有界 crawler，直到自托管浏览器
   或抓取服务有实际可重复的成功率证据。
3. **可选第二搜索源**：有 Brave key 时接入 Brave 作为 Firecrawl 的交叉发现源；
   有 Tavily key 时接入 Tavily 作为带片段/正文的另一条路径。两者都不能覆盖当前
   未配置密钥的生产默认。

代码落地采用了同一边界：`PublicWebConfig` 通过 `CHATWAIFU_PUBLIC_WEB__...` 或
`config/default.toml` 选择 provider，默认仍为 DuckDuckGo Lite + 内置 reader。当前
已接入 Firecrawl Search、Jina Reader 和 Firecrawl Scrape；供应商 SDK 对象没有穿过
Runtime 技能层，结果统一为来源 URL、检索时间、响应指纹和有界正文。Jina 是外部
reader 的首选；当它以超时、空正文、网络/临时不可用或格式变化失败，且已经配置
Firecrawl key 时，才回退到 Firecrawl Scrape。认证、限流和配置错误不会静默转移到
另一个供应商，避免把额度问题误报成“没有结果”。

当前运行环境的系统 DNS 会把外部域名映射到 198.18/保留地址，因此隔离 smoke 显式
选择 `dns_resolver=cloudflare` 时，适配器现在同时用 Cloudflare 解析来源 URL 和
供应商 endpoint；默认仍为 `system`，不会静默改变生产路由。Jina 的 `Title:` 响应
元数据也会填入统一的 `title` 收据字段。真实收据见
[Jina Runtime smoke](q02-jina-runtime-smoke-2026-10-02.md)。

适配器在发请求前分别校验 provider endpoint 和目标来源的 HTTPS/公网地址，使用
固定 transport、`trust_env=False`、响应上限与取消传播；401/403、429、5xx、坏 JSON、
超时、私网 endpoint 和格式变化都有独立错误码。`fresh: true` 对 Jina 发送
`X-No-Cache: true`，对 Firecrawl Scrape 使用 `maxAge: 0`；外部 reader 的链接提取
暂不宣称支持，若请求 `max_links` 会在收据中标记为截断。受控 MockTransport 测试已
覆盖成功、鉴权、429/5xx、坏响应、无 key、目标校验、响应上限、超时与取消。

已完成一次真实的 Firecrawl 匿名试用 Runtime smoke 和最小评测器批次，但仍未完成带独立 key 的 Firecrawl 生产 smoke，也没有把默认 provider 切换到外部服务；
批次结果见 [Firecrawl 匿名 Runtime 批次](q02-firecrawl-anonymous-runtime-batch-2026-10-02.md)。
因此本实现解决的是可替换和可审计的网络边界，不能单独宣称 Q02 事实质量或渠道验收
通过。

### 官方依据

- [LobeHub online-search.mdx](https://raw.githubusercontent.com/lobehub/lobe-chat/main/docs/self-hosting/advanced/online-search.mdx)：`SEARCH_PROVIDERS`/`CRAWLER_IMPLS` 边界。
- [Firecrawl Search](https://docs.firecrawl.dev/features/search.md)、[Scrape](https://docs.firecrawl.dev/features/scrape.md)、[Billing](https://docs.firecrawl.dev/billing.md)：接口、动态页面处理、credits 和免费限额。
- [Jina Reader](https://jina.ai/reader)：Markdown reader、缓存、动态等待和 token budget。
- [SearXNG Search API](https://docs.searxng.org/dev/search_api.html)：JSON 输出必须由实例管理员启用。
- [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search.md)、[Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract.md)、[Credits](https://docs.tavily.com/documentation/api-credits.md)：深度、域名过滤和计费。
- [Brave Web Search API](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started) 与 [价格页](https://brave.com/search/api/)；[Exa 价格页](https://exa.ai/pricing)；[Browserless 价格页](https://www.browserless.io/pricing)。

## 判断与后续方案

当前缺口同时涉及供应商和爬取适配器：

1. 不能把 DuckDuckGo Lite 的挑战页当作稳定搜索 API；当前解析器只支持一个易变化的 HTML 供应商。
2. 固定应用 User-Agent 会触发该供应商的挑战，但改成浏览器伪装会变成绕过供应商风控，不作为修复。
3. `trust_env=False` 与系统 Fake-IP 让当前网络环境的默认解析不可用；Cloudflare 解析是可审计的临时诊断参数，不应写入生产默认。
4. 适合的代码修复是增加明确的搜索供应商端口和配置（例如自托管 SearXNG JSON，或用户已有的 Brave/Tavily/Search1API 凭据），并把正文 crawler 作为独立可选实现；每个供应商保留真实 URL、抓取时间、指纹、截断和失败类型。
5. 没有实际配置和凭据前，不新增公共 SearXNG 默认地址、不修改生产模型/数据库/搜索默认，也不以更换 User-Agent 宣称 Q02 通过。修复完成后需分别验证 search discovery、official `web.read`、来源事实覆盖，再进行完整模型批次。

本诊断不改变 Q02 的未通过结论，也不替代真实 TTS、渠道投递、播放完成 ACK 和用户 ACK 验收。
