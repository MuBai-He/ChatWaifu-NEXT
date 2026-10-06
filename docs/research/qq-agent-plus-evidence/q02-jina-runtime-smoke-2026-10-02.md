# Q02 Jina Runtime smoke

日期：2026-10-02。此记录只证明外部来源适配器在当前网络条件下能取得一份可审计的官方正文，不证明 Gemini 回复质量或 Q02 通过。

## Request

- Runtime checkout：`mubai/qq-agent-plus-implementation`，未改生产 persona、模型、预算或数据库。
- Reader：`PublicWebReader`，`PublicWebConfig(reader_provider="jina")`，无 Jina API key。
- Source：`https://www.caac.gov.cn/XWZX/MHYW/202506/t20250626_227805.html`
- Arguments：`fresh=true`、`focus="3C"`、`max_characters=6000`、`max_links=0`、`dns_resolver="cloudflare"`。
- Provider endpoint：`https://r.jina.ai`；请求使用 HTTPS、固定地址 transport、`X-No-Cache: true`，没有读取或保存凭据。

## Results

After the title parser fix, the Runtime adapter returned HTTP 200 content through Jina in `20.147` seconds:

| Field | Value |
|---|---|
| provider | `jina` |
| retrieved_at | `2026-10-02T10:57:24.814542+00:00` |
| body_sha256 | `0a87f68ab6e6ef38a9da204f6de78c14acb51e1dd58e1e69b0488c846e23fc0f` |
| provider body characters | `8271` after adapter cleaning |
| returned characters | `6000` |
| truncated | `true` |
| focus_matched | `true` |
| title | `民航局：禁止旅客携带无3C标识及被召回的充电宝乘坐境内航班` |

The retained excerpt contained all of `3C`, `召回`, `型号`, `批次`, and `6月28日`. The official text states that, from June 28, passengers are prohibited from carrying power banks without a 3C mark, with an unclear 3C mark, or models/batches subject to recall on domestic flights. This is source evidence only; current applicability and later amendments still require the planned source audit.

## Boundary failures observed

The same adapter call with `dns_resolver="system"` stopped before a provider request with `web_provider_endpoint_forbidden`: local DNS returned reserved Fake-IP addresses (`r.jina.ai -> 198.18.2.160`, `198.18.2.161`; CAAC -> `198.18.0.249`). A direct `curl` and a manually pinned transport using Cloudflare-resolved public addresses both returned Jina 200, so the failure was DNS/policy path selection rather than empty or malformed Jina content.

The provider adapter now applies the existing explicit `dns_resolver` choice to both the selected source URL and the external provider endpoint. The default remains `system`; the isolated smoke explicitly selected `cloudflare`. Private, reserved, non-HTTPS and redirect-invalid endpoints remain rejected.

## Limits

This smoke does not measure provider long-term availability, Firecrawl with a key, search discovery, model usage or latency, source freshness beyond the provider response and `X-No-Cache` request, TTS, channel delivery, playback completion, user acknowledgement, or the factual/technical Q02 gates. Q02 remains blocked pending the planned Gemini source-tool batch and independent quality/device checks.

The planned new Gemini batch was not started in this turn because the execution shell had no `OPENAI_API_KEY` for the authenticated 8318 endpoint. No credential was read, guessed, or written; existing older Gemini artifacts were not relabeled as a new batch.

The isolated evaluator now accepts `--source-search-provider` and
`--source-reader-provider` (both default to the existing providers). Its dry-run for
`detailed_answer`, three repeats, `jina`, `cloudflare`, and the recommended 32768/8192
budget estimated 12 logical requests and a 72-round upper bound; no network request was
made by that dry-run.
