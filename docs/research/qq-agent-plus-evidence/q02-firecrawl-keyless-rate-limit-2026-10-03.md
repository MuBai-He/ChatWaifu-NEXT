# Q02 Firecrawl 匿名搜索限流复核（2026-10-03）

本次探针只验证来源供应商状态，没有调用模型，也没有读取或写入项目密钥。

## 请求

- Endpoint：`https://api.firecrawl.dev/v2/search`
- Query：`民航局 充电宝 3C 召回 最新 修订 生效`
- Limit：`3`
- 请求体要求 `scrapeOptions.formats=["markdown"]`
- 请求次数：连续 4 次；请求没有 Authorization header
- 时间：`2026-10-02T19:07:13Z`（本机 UTC）

## 供应商返回

四次请求均返回供应商的 keyless free-tier 限流错误，核心字段为：

```json
{
  "success": false,
  "error": "You've hit Firecrawl's keyless free tier rate limit.",
  "reason": "credits",
  "retry_after_seconds": 72616
}
```

响应建议创建 Firecrawl API key，但本次没有创建账号、读取注册链接中的状态或把任何 key 写入项目。

## 结论

Firecrawl 匿名试用可以作为此前隔离批次的短期实验路径，但当前不能作为 Q02 的稳定生产来源发现证据。当前配置没有可用的 Firecrawl、Brave 或 Tavily 搜索密钥；DuckDuckGo Lite 仍可能返回 `web_search_challenge`。现有 Runtime 会把这些状态作为来源失败/未核实返回，不会把搜索摘要当作事实。

本次复核同时触发了 Runtime 适配器修正：Firecrawl 以 HTTP 200 返回 `success=false` 时，现在会按 `reason=credits` 归类为可重试的 `web_provider_rate_limit`，保留有限的 `retry_after_seconds`，不再误报为格式变化或泄露供应商原始注册链接。对应来源/搜索/评测回归为 `45 passed`。

因此本次没有切换生产默认 provider，也没有宣称 Q02 通过。要完成稳定来源门槛，需要在受控配置中提供一个有配额的搜索 provider，再用同一十二场景、三重复、三种 presentation 复测。
