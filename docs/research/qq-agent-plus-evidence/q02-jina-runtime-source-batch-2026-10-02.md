# Q02 Jina Runtime source-tool batch

日期：2026-10-02。此批使用项目主配置的 `chat` 密钥在进程内认证当前 Q02 端点；密钥没有写入结果、日志或仓库。

## Frozen execution

- Endpoint: `https://mubai.website:8318/v1`
- Model: `gemini-3.8-flash-high`（`/models` authenticated response contained this model）
- Evaluator: `1.16.0`
- Scenario: `detailed_answer`, 3 repeats, 12 logical turns
- Runtime source configuration: `duckduckgo_lite` search, `jina` reader, explicit `cloudflare` DNS, `allow_once`
- Budget: context window `32768`, output reserve/request maximum `8192`, 15% estimate margin, scaled sections; estimated input limit `21370`
- Price: unknown; run used the user's explicit no-dollar-ceiling authorization
- Storage: isolated SQLite under `.local/research/q02/q02-jina-runtime-source-20261002/`

## Provider and budget result

All `12/12` logical turns and `12/12` final Provider rounds completed with `stop`; no
incomplete file was produced. Reported usage was prompt `93,272`, completion `6,261`,
reasoning `7,904`, total `107,590`. Latency was mean `11,936.5 ms`, p50 `9,099 ms`,
p95 `35,624 ms` (12 samples; p95 is not a realtime guarantee).

All recorded input reports were below `21370`; the largest reference estimate was `10634`,
and no history or tool-preamble indices were omitted. This batch therefore gives no evidence
that budget clipping caused the factual failure.

## Search and read evidence

DuckDuckGo Lite returned a successful response in all three t2 turns. Its first result was
the 2015 official CAAC announcement:

`https://www.caac.gov.cn/big5/www.caac.gov.cn/XXGK/XXGK/TZTG/201511/t20151105_11173.html`

Two of the three repeats followed the result with a Jina `web.read`; both reads returned
HTTP-successful Jina data with the same title and public source URL, but different response
hashes due retrieval. The read body contained the older 100/160Wh rules and did not contain
`3C`, `召回`, `2025`, or `6月28日`. The third repeat called `web.search` but did not issue a
follow-up `web.read` before answering.

The Jina reads prove the new crawler path works: provider `jina`, bounded/truncated source
receipts, Cloudflare DNS, and retained provenance. They do not prove that discovery selected
the current rule or that the model must read a result before answering.

## Quality audit

All three t2 regulation replies and all three t4 checklists omitted the required
2025-06-28 domestic-flight conditions for missing/unclear 3C marks and recalled models or
batches. All three t2 replies did include 100/160Wh thresholds, which came from the stale
2015 source. One reply also added an unverified mAh-specific claim. This is a source freshness,
tool-follow-through and model-quality failure, not an input-budget failure.

Q02 remains blocked. The next repair should make current/time-sensitive regulation searches
select a current official source and enforce a successful `web.read` before presenting source-
dependent facts, while preserving explicit failure/uncertainty when search or read fails.
Firecrawl Search was not used because no Firecrawl key is configured; production defaults,
persona, model routing, budget and databases remain unchanged.
