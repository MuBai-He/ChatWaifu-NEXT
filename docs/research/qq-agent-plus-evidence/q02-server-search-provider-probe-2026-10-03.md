# Q02 服务器搜索与爬取探针

日期：2026-10-03。执行约束：不调用 AGY；只连接已配置的
`192.168.1.103`（主机名 `mubai-6133`），不读取或写出任何密钥值，不修改生产
Runtime、数据库或模型路由。

## 连接与运行状态

- SSH 连接成功。
- CLIProxy 模型入口监听 `0.0.0.0:8317`，公网反代监听 `0.0.0.0:8318`。
- Runtime 健康检查 `https://127.0.0.1:8765/v1/runtime/health` 返回
  `status=ok`、`database=ready`。
- 本机公开来源服务监听：SearXNG `127.0.0.1:18080`、Crawl4AI
  `127.0.0.1:11235`。
- 运行中的 Runtime 环境选择 `search_provider=searxng`、
  `reader_provider=crawl4ai`，SearXNG 地址为 `http://127.0.0.1:18080`。

## 固定查询探针

对以下三个查询分别请求 SearXNG JSON 接口：

1. `民航局 充电宝 3C 召回`
2. `充电宝 100Wh 160Wh 不得托运 民航局`
3. `Raft leader election timeout heartbeat parameter`

三次 HTTP 请求均快速返回，但结果数组均为 0。SearXNG 容器日志同时记录了下游
DuckDuckGo 超时、Brave 触发 `Too many request`、Google CSE 触发异常流量限流；
因此不能把空数组解释为“没有相关来源”。当前 SearXNG 实例没有给出可供 Runtime
使用的稳定发现结果。

## Crawl4AI 正文读取

Crawl4AI `/health` 返回 `status=ok`、版本 `0.9.2`。使用运行中的服务令牌在服务器
进程内读取官方民航局页面：

`https://www.caac.gov.cn/XWZX/MHYW/202506/t20250626_227805.html`

结果：HTTP 成功，Markdown 9,763 字节，正文 SHA-256 为
`3210ee2033a07a04cf9d49391cbf94ea71414708cba969926b9d425e6dfaafe1`。正文保留
`3C`、`召回` 和 `2025`，没有 `100Wh`、`160Wh` 或托运条件。这是正文范围的真实
证据，不能扩展成该页面支持全部充电宝规则。

## 其他发现服务对照

- Firecrawl 无密钥试用路径对三个固定查询均返回 HTTP `429`；服务器当前公开来源
  配置没有 Firecrawl/Jina key，不能把匿名探针当作可用生产额度。
- So360 浏览器请求返回 HTTP `200`，但最终主机为 `qcaptcha.so.com`，正文带挑战标记，
  没有结果卡；现有适配器拒绝该外部验证跳转是正确行为。
- Google 请求返回重定向提示页，没有可解析的结果卡。
- Bing 请求虽然返回 HTML，但结果列表包含与查询无关的内容（例如 Pokémon 页面），
  且不能稳定解出 `caac.gov.cn` 的真实结果 URL，不能接入来源发现主路。

## 结论与下一步

本次证明服务器网络、Runtime 和正文 crawler 均可工作；Q02 当前阻塞点是没有稳定、
可审计的来源发现服务。继续重跑完整模型批次不会修复这个问题，反而会把来源失败和
模型质量混在一起。下一步需要取得一个可重复的搜索发现路径（受控 SearXNG 引擎、
带配额的 Firecrawl/Brave/Tavily 等），先用相同三个查询验证官方域名命中率，再重跑
小批 `detailed_answer`，通过事实审计后才允许完整 Q02 批次。

