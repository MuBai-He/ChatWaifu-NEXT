# Local public-web services

CW2 can use two independent local services for public-source work:

- **SearXNG** at the configured loopback port (default `http://127.0.0.1:8080`)
  discovers source URLs through its JSON API.
- **Crawl4AI** at the configured loopback port (default `http://127.0.0.1:11235`)
  renders and extracts page content through
  its authenticated `/md` endpoint. With Crawl4AI 0.9.3+, `.pdf` URL paths use
  `/crawl` with the fixed PDF extraction strategy; PDF parsing stays outside Runtime.

They are separate from the Runtime process. SearXNG does not replace page
reading, and Crawl4AI does not provide the SearXNG result set used for discovery.

For local Chinese public-source discovery, the checked-in SearXNG template keeps
only the Yandex engine as a trial configuration. The initial server probe
recorded five successful repetitions for each fixed query, but the later model
batch was followed by ten consecutive Yandex timeouts. A later three-query
check recovered; this proves intermittent availability, not stability.
See [server evidence](../research/qq-agent-plus-evidence/q02-server-yandex-crawl4ai-probe-2026-10-03.md).
Sogou's SearXNG adapter failed with a compatibility error; Baidu was challenged
and Bing returned irrelevant technical results. Keep provider errors distinct
from empty result sets and recheck discovery under the intended workload.

The container healthcheck uses `/healthz`, which does not issue upstream search
queries. A healthy container therefore proves service readiness only. The trial
Yandex engine timeout is eight seconds, within the Runtime skill's 30-second
deadline. SearXNG executes one explicitly requested query per skill call; later
refinements require separate visible calls. This does not fix a blocked engine.
SearXNG HTTP 200 with an empty list and `unresponsive_engines` is a provider
failure, not evidence that no sources exist.

The keyless `so360` provider remains an optional Runtime adapter. Its result
cards expose canonical `data-pcurl` source URLs, but the endpoint may return a
same-origin WZWS cookie challenge; the adapter follows at most two same-origin
HTTPS redirects and rejects external captcha redirects. Keep
`dns_resolver: cloudflare` on Fake-IP hosts.

## Start

From the repository root:

```bash
cd deploy/public-web
cp .env.example .env
openssl rand -hex 32  # use one value for CRAWL4AI_API_TOKEN
openssl rand -hex 32  # use another value for CRAWL4AI_SECRET_KEY
```

Put the generated values in `.env`, replace the local SearXNG
`server.secret_key` placeholder in `searxng/settings.yml`, then start:

```bash
docker compose up -d
docker compose ps
curl 'http://127.0.0.1:8080/search?q=民航局+充电宝&format=json'
curl -fsS http://127.0.0.1:11235/health
```

When Docker Hub is unavailable, set `SEARXNG_IMAGE` and `CRAWL4AI_IMAGE` in
`.env` to reachable mirrors. The Runtime interface is unchanged by the image
source.

If the host VPN uses Fake-IP DNS, set both `SEARXNG_DNS_SERVER` and
`CRAWL4AI_DNS_SERVER` to a LAN gateway resolver that returns real public
addresses. Setting only the crawler resolver leaves SearXNG's upstream engine
requests on the host Fake-IP path. Recreate the affected container after changing
the DNS setting and check resolution from inside it. Crawl4AI keeps its SSRF protection;
do not enable `CRAWL4AI_ALLOW_INTERNAL_URLS` for this deployment. When the CW2
host's own resolver still returns Fake-IP addresses, pass
`dns_resolver: cloudflare` to `web.search`/`web.read`; the normal public-address
and TLS checks remain enabled.

For the SearXNG adapter, that argument validates the local service call; it does
not configure SearXNG's upstream network. If the engines require the host's
existing proxy, configure `outgoing.proxies` in SearXNG with a proxy address
reachable from its container (container loopback is not host loopback). Preserve
certificate verification. In the October 3 isolated server check, explicitly
using the host proxy restored DuckDuckGo results, but repeated requests then
received CAPTCHA; Google was denied and Brave rate-limited. Proxy connectivity
does not establish upstream availability or source relevance. See the
[network and engine evidence](../research/qq-agent-plus-evidence/q02-source-freshness-review-2026-10-03.md).

The Crawl4AI image requires the token for all endpoints except `/health`.

The template now pins 0.9.3 for Docker PDF support. An existing `.env` image
override keeps its old value until explicitly changed. Validate an upgrade on a
separate loopback port before replacing the active instance: check both HTML and
PDF through CW2's permissioned reader, with `dns_resolver: cloudflare` on Fake-IP
hosts. A health response cannot validate PDF dependencies or extraction. PDF reads
bypass crawler cache and retain the normal 6000-character excerpt, focus, byte,
permission and timeout limits. URLs without a `.pdf` path suffix and OCR are not
covered; a successful excerpt is not proof that omitted pages were examined.

## Enable in CW2

Keep the service token in the Runtime environment or private server environment;
do not put it in frontend variables or committed TOML:

```bash
export CHATWAIFU_PUBLIC_WEB__SEARCH_PROVIDER=searxng
export CHATWAIFU_PUBLIC_WEB__READER_PROVIDER=crawl4ai
export CHATWAIFU_PUBLIC_WEB__CRAWL4AI_API_TOKEN="$CRAWL4AI_API_TOKEN"
```

Restart Runtime after changing provider configuration. The loopback endpoint
validation rejects remote provider origins. `web.search` still requires normal
permission confirmation, and every selected result still needs a separate
`web.read` confirmation and public HTTPS validation.

## Stop

```bash
docker compose down
```

The default CW2 route remains DuckDuckGo Lite plus the built-in Reader. Enabling
the local SearXNG/Crawl4AI pair is still an explicit deployment choice; a local
HTML read can optionally recover a companion outage with
`CHATWAIFU_PUBLIC_WEB__CRAWL4AI_BUILTIN_FALLBACK=true` (default false). Recovery
allows one builtin read attempt only after a Crawl4AI network, timeout or 5xx error,
within the original read deadline. It never handles PDFs, credentials/auth errors,
rate limits or source-address validation failures. Results identify the actual
reader as `builtin` and retain scalar `provider_fallback` metadata; a second failure
keeps its own error plus that metadata. The fallback still validates/pins every
public destination and redirect and does not send the companion token to the page.
An HTML document may still require a `focus` excerpt; this option proves neither
source completeness nor current applicability. Isolated evaluations expose
`--source-reader-builtin-fallback` and reject resuming with a different value.

The local engine probe does not by itself pass Q02's model-quality, channel-delivery, or
playback gates.

### 2026-10-03 隔离验证状态

服务器另设 SearXNG 18081（360search + Yandex，经已有 Mihomo 代理）及 Crawl4AI 11236
（0.9.3）进行 Q02 验证。生产 18080/11235 没有随模板或这些验证自动切换。新版读取器已实际
读取 HTML 和 Raft PDF 指定段落；组合搜索短期 12/12 成功仍不证明长期稳定，真实模型仍有
来源时效性与解释范围问题。详见 [来源流程调查](../research/qq-agent-plus-evidence/q02-source-continuation-order-2026-10-03.md)。

web.search 1.3.6已移除自动中文追加词及SearXNG固定旧条款补充查询，保留明示查询、域过滤
与供应商排序。隔离八轮复测仍有未读到原文/耗尽调用后回退，不作为质量通过；生产未部署。
见[查询与答案对照](../research/qq-agent-plus-evidence/q02-query-preservation-and-self-review-2026-10-03.md)。

### 2026-10-04 生产PDF读取器配套修复

生产11235已固定到经过验证的Crawl4AI0.9.3镜像digest，镜像本身含pypdf6.16.2；
生产Runtime仅回移PDF专用分支，不再把.pdf送到HTML /md。实际国航HTML正文与原
冻结资料相同，Raft PDF实际返回相关6000字符选段并标明截断。模型、角色、预算、
数据库与渠道配置未改，SearXNG18080未重启或换引擎。成功只覆盖这两次指定读取，
不代替自主发现、来源现行性、模型回答质量或长期稳定性。

原模块/.env保留在服务器私有备份目录q02-reader-20261004；恢复二者后重建Crawl4AI
并重启Runtime可回退，密钥文件不导出。源码与镜像必须配套，不能仅凭/health升级
结论。详见[部署与正文证据](../research/qq-agent-plus-evidence/q02-closure-2026-10-04/reader-integration/README.md)。
