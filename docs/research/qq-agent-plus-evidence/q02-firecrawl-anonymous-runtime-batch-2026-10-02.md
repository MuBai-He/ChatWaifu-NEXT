# Q02 Firecrawl 匿名发现 + Jina 正文 Runtime 批次

日期：2026-10-02。此记录使用当前 CW2 配置中的 `chat` 密钥认证 8318 模型端点；搜索和正文供应商请求均未携带该模型密钥。所有 Runtime 数据写入隔离 `.local/research/q02/real-firecrawl-source-batch-20261002-v1/`。

## 直接配置探针

- 当前配置密钥角色只有 `chat`、`embedding`、`memory_extraction`、`memory_summary`，没有 Firecrawl 或 Jina Search 独立密钥。
- `GET https://mubai.website:8318/v1/models`：HTTP 200，返回 18 个模型，包含 `gemini-3.8-flash-high`。
- 使用当前 `chat` 密钥调用 `gemini-3.8-flash-high`：HTTP 200；供应商返回模型名为 `gemini-3.8-flash`，usage 可读。
- 不带认证调用 `POST https://api.firecrawl.dev/v2/search`：HTTP 200；查询“民航局 充电宝 3C 召回”时，CAAC 官方页排第一。该匿名路径没有租户额度、计费或长期稳定性保证。

## Runtime 最小链路

隔离 Runtime 显式配置 `search_provider=firecrawl`、`reader_provider=jina`、`firecrawl_allow_anonymous=true`，并在用户请求中要求 `dns_resolver=cloudflare`、`fresh=true`。一次真实对话中：

- `web.search` 成功，Firecrawl 返回 CAAC 官方 2025-06-26 页面；
- `web.read` 成功，Jina 返回同一 HTTPS 原文，正文保留 `3C`、`召回`、型号/批次和 `6月28日`；
- 两次调用均经过现有确认、来源 URL、检索时间、响应指纹和正文截断收据；
- 这证明了“模型 -> Runtime source skill -> Firecrawl/Jina -> 来源收据”的可运行链路，不证明法规完整性或 Q02 通过。

## 评测器批次

使用现有 `tools/evaluate_character_scenarios.py`，`detailed_answer` 场景、1 次重复、4 个逻辑回合，实际模型为 `gemini-3.8-flash-high`，上下文窗口 32768、输出预留 8192、15% 估算余量，source-tools `allow_once`，Firecrawl 匿名模式显式开启。

- `4/4` 逻辑回合完成，12 个供应商回合均有终态；
- 供应商 usage：prompt `36,033`、completion `2,248`、reasoning `2,752`、total `41,033`；
- 延迟均值 `14,819.8 ms`，p50 `13,334 ms`，p95 `24,583 ms`；
- 预算报告最大估算输入 `10,475`，低于 `21,370` 的实际输入上限，没有发生历史或工具前缀裁剪。

## 仍暴露的质量问题

第 2 轮模型搜索词为“民航局 旅客 携带 充电宝 规定 额定能量”，虽然 Runtime 将 `最新 修订 生效` 追加到 `effective_query`，Firecrawl 仍把 2015 年官方公告排在第一；模型随后只读取了该旧页面。该正文包含 100/160Wh 规则，但不包含 2025 年 3C、召回型号/批次和 6 月 28 日条件，模型却在回答中补出了这些内容。

因此，Firecrawl 解决了 DuckDuckGo challenge 导致的“无法发现来源”问题，但没有单独解决“当前规则发现、旧来源识别和未证据化内容禁止输出”。Q02 仍未通过；这次失败属于来源选择与事实质量，不属于输入预算不足。

## 代码边界

新增 `PublicWebConfig.firecrawl_allow_anonymous`，默认 `false`。只有评测器的 `--source-firecrawl-anonymous` 或显式 Runtime 配置才允许无 key 请求；认证模式仍要求独立 Firecrawl key。Jina 失败回退到 Firecrawl 时也遵守该显式开关。生产默认仍为 DuckDuckGo Lite + 内置 reader，未修改模型、persona、数据库或默认 provider。
