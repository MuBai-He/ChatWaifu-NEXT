from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Callable
from typing import Any

import chatwaifu_runtime.runtime_skills.public_web_providers as providers
import httpx2
import pytest
from chatwaifu_runtime.config.settings import PublicWebConfig
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.public_web import PublicWebReader
from chatwaifu_runtime.runtime_skills.public_web_providers import (
    Crawl4aiReaderClient,
    FirecrawlReaderClient,
    FirecrawlSearchClient,
    JinaReaderClient,
    JinaSogouSearchClient,
    ProviderPage,
    ProviderSearchResult,
    SearxngSearchClient,
    So360SearchClient,
)
from chatwaifu_runtime.runtime_skills.transports import ValidatedMcpEndpoint
from pydantic import SecretStr

_ENDPOINT = ValidatedMcpEndpoint(
    hostname="api.example.com", port=443, addresses=(ipaddress.ip_address("93.184.216.34"),)
)


def _config(**kwargs: Any) -> PublicWebConfig:
    return PublicWebConfig(
        firecrawl_api_key=SecretStr("fire-key"),
        jina_api_key=None,
        **kwargs,
    )


def _patch_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def endpoint(_: str, **kwargs: object) -> ValidatedMcpEndpoint:
        del kwargs
        return _ENDPOINT

    async def source(_: str, _resolver: str = "system", **kwargs: object) -> ValidatedMcpEndpoint:
        del kwargs
        return _ENDPOINT

    monkeypatch.setattr(providers, "_validate_provider_endpoint", endpoint)
    monkeypatch.setattr(providers, "validate_public_url_with_resolver", source)


def _client_transport(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> Callable[[ValidatedMcpEndpoint], httpx2.AsyncBaseTransport]:
    return lambda _: httpx2.MockTransport(handler)


def _local_client_transport(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> Callable[[], httpx2.AsyncBaseTransport]:
    return lambda: httpx2.MockTransport(handler)


@pytest.mark.asyncio
async def test_searxng_search_parses_json_without_authentication() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "results": [
                    {
                        "url": "https://docs.example/guide",
                        "title": "Guide",
                        "content": "Official result",
                    }
                ]
            },
        )

    result = await SearxngSearchClient(
        PublicWebConfig(search_provider="searxng"),
        transport_factory=_local_client_transport(handle),
    ).search("official source", 3)

    assert result.provider == "searxng"
    assert result.results[0] == ProviderSearchResult(
        "https://docs.example/guide", "Guide", "Official result"
    )
    assert requests[0].url.host == "127.0.0.1"
    assert requests[0].url.port == 8080
    assert requests[0].url.path == "/search"
    assert requests[0].url.params["q"] == "official source"
    assert requests[0].url.params["format"] == "json"
    assert requests[0].headers.get("authorization") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["timeout", "Suspended: timeout", "CAPTCHA"])
async def test_searxng_engine_failure_is_not_an_empty_success(status: str) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json={"results": [], "unresponsive_engines": [["yandex", status]]}
        )

    client = SearxngSearchClient(
        PublicWebConfig(search_provider="searxng"),
        transport_factory=_local_client_transport(handle),
    )
    with pytest.raises(SkillExecutionError) as caught:
        await client.search("official source", 3)
    assert caught.value.structured.code == "web_search_engine_error"
    assert caught.value.structured.details["unresponsive_engine_count"] == 1


@pytest.mark.asyncio
async def test_searxng_missing_results_is_not_an_empty_success() -> None:
    client = SearxngSearchClient(
        PublicWebConfig(search_provider="searxng"),
        transport_factory=_local_client_transport(lambda request: httpx2.Response(200, json={})),
    )
    with pytest.raises(SkillExecutionError) as caught:
        await client.search("official source", 3)
    assert caught.value.structured.code == "web_provider_format_changed"


@pytest.mark.asyncio
async def test_so360_search_uses_canonical_data_mdurl_without_redirect_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                '<li class="res-list"><h3 class="res-title"><a '
                'href="https://www.so.com/link?m=redirect" '
                'data-mdurl="https://docs.example/official">官方公告</a></h3>'
                '<p class="res-desc">含 3C 和召回条件</p></li>'
                '<li class="res-list"><h3 class="res-title"><a '
                'href="https://www.so.com/link?m=missing">无真实来源</a></h3></li>'
            ),
        )

    result = await So360SearchClient(
        PublicWebConfig(search_provider="so360"),
        transport_factory=_client_transport(handle),
    ).search("充电宝 3C", 5, dns_resolver="cloudflare")

    assert result.provider == "so360"
    assert result.results == (
        ProviderSearchResult("https://docs.example/official", "官方公告", "含 3C 和召回条件"),
    )
    assert requests[0].url.path == "/s"
    assert requests[0].url.params["q"] == "充电宝 3C"
    assert requests[0].headers.get("authorization") is None


@pytest.mark.asyncio
async def test_so360_search_parses_mobile_div_results_and_data_pcurl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)

    def handle(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                '<div class="g-card res-list og" '
                'data-pcurl="https://news.example/official">'
                '<a class="alink" href="https://m.so.com/jump?u=ignored">'
                '<h3 class="res-title">什么是<em>3C</em>标识的充电宝?</h3>'
                "</a>"
                '<a class="alink" href="https://m.so.com/jump?u=ignored">'
                '<div class="res-con"><div class="summary sumext-line-3">'
                "民航局提示: 没有 3C 标识或被召回的充电宝不得携带登机。"
                "</div></div></a>"
                "</div>"
                '<div class="g-card res-list mso-recommend-card">'
                '<h3 class="res-title">没有 canonical URL，应丢弃</h3>'
                "</div>"
            ),
        )

    result = await So360SearchClient(
        PublicWebConfig(search_provider="so360"),
        transport_factory=_client_transport(handle),
    ).search("充电宝 3C", 5, dns_resolver="cloudflare")

    assert result.results == (
        ProviderSearchResult(
            "https://news.example/official",
            "什么是3C标识的充电宝?",
            "民航局提示: 没有 3C 标识或被召回的充电宝不得携带登机。",
        ),
    )


@pytest.mark.asyncio
async def test_so360_search_follows_only_same_origin_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    calls = 0

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx2.Response(
                302,
                headers={"location": "/s?q=redirected", "set-cookie": "WZWS4=proof; Path=/"},
            )
        assert request.url.path == "/s"
        return httpx2.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                '<div class="res-list" data-pcurl="https://docs.example/official">'
                '<h3 class="res-title">公告</h3><p class="res-desc">正文</p></div>'
            ),
        )

    result = await So360SearchClient(
        PublicWebConfig(search_provider="so360"),
        transport_factory=_client_transport(handle),
    ).search("公告", 5, dns_resolver="cloudflare")

    assert calls == 2
    assert result.results == (
        ProviderSearchResult("https://docs.example/official", "公告", "正文"),
    )


@pytest.mark.asyncio
async def test_so360_search_rejects_external_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)

    client = So360SearchClient(
        PublicWebConfig(search_provider="so360"),
        transport_factory=_client_transport(
            lambda _: httpx2.Response(302, headers={"location": "http://qcaptcha.so.com/"})
        ),
    )

    with pytest.raises(SkillExecutionError) as error:
        await client.search("公告", 5, dns_resolver="cloudflare")
    assert error.value.structured.code == "web_provider_redirect"


@pytest.mark.asyncio
async def test_crawl4ai_reader_posts_url_with_token_and_extracts_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={"markdown": "# Source\n\nBody", "metadata": {"title": "Provider title"}},
        )

    result = await Crawl4aiReaderClient(
        PublicWebConfig(reader_provider="crawl4ai", crawl4ai_api_token=SecretStr("crawl-token")),
        transport_factory=_local_client_transport(handle),
    ).read("https://example.org/article", fresh=True)

    assert result.provider == "crawl4ai"
    assert result.title == "Provider title"
    assert result.text == "# Source\n\nBody"
    assert requests[0].url.path == "/md"
    assert requests[0].headers["authorization"] == "Bearer crawl-token"
    assert json.loads(requests[0].content) == {"url": "https://example.org/article"}


@pytest.mark.asyncio
async def test_crawl4ai_reader_requires_token_before_network() -> None:
    client = Crawl4aiReaderClient(
        PublicWebConfig(reader_provider="crawl4ai"),
        transport_factory=lambda: pytest.fail("request must not be sent"),
    )
    with pytest.raises(SkillExecutionError) as error:
        await client.read("https://example.org/article", fresh=False)
    assert error.value.structured.code == "web_provider_auth"


@pytest.mark.asyncio
async def test_crawl4ai_pdf_uses_pdf_strategy_and_preserves_source_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    url = "https://example.org/paper.PDF?download=1"
    requests: list[httpx2.Request] = []
    validated: list[tuple[str, str]] = []

    async def validate(source: str, resolver: str) -> ValidatedMcpEndpoint:
        validated.append((source, resolver))
        return _ENDPOINT

    monkeypatch.setattr(providers, "validate_public_url_with_resolver", validate)

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": url,
                        "status_code": 200,
                        "metadata": {"title": "Original paper"},
                        "markdown": {"raw_markdown": "# Paper\n\nActual extracted text"},
                    }
                ],
            },
        )

    result = await Crawl4aiReaderClient(
        PublicWebConfig(reader_provider="crawl4ai", crawl4ai_api_token=SecretStr("crawl-token")),
        transport_factory=_local_client_transport(handle),
    ).read(url + "#page=2", fresh=True, dns_resolver="cloudflare")

    assert validated == [(url, "cloudflare")]
    assert result.provider == "crawl4ai" and result.source_url == url
    assert result.title == "Original paper"
    assert result.text == "# Paper\n\nActual extracted text"
    assert result.body and result.retrieved_at
    assert len(requests) == 1 and requests[0].url.path == "/crawl"
    assert requests[0].headers["authorization"] == "Bearer crawl-token"
    assert json.loads(requests[0].content) == {
        "urls": [url],
        "crawler_config": {
            "type": "CrawlerRunConfig",
            "params": {
                "scraping_strategy": {"type": "PDFContentScrapingStrategy", "params": {}},
                "cache_mode": {"type": "CacheMode", "params": "bypass"},
            },
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"success": False},
        {"status_code": 500},
        {"url": "https://different.example/paper.pdf"},
        {"markdown": None},
        {"markdown": {"raw_markdown": ""}},
    ],
)
async def test_crawl4ai_pdf_rejects_failed_or_misattributed_text(
    monkeypatch: pytest.MonkeyPatch, override: dict[str, Any]
) -> None:
    _patch_validation(monkeypatch)
    result = {
        "success": True,
        "url": "https://example.org/paper.pdf",
        "status_code": 200,
        "markdown": {"raw_markdown": "Do not accept failed content"},
        **override,
    }
    client = Crawl4aiReaderClient(
        PublicWebConfig(reader_provider="crawl4ai", crawl4ai_api_token=SecretStr("crawl-token")),
        transport_factory=_local_client_transport(
            lambda _: httpx2.Response(200, json={"success": True, "results": [result]})
        ),
    )
    with pytest.raises(SkillExecutionError) as caught:
        await client.read("https://example.org/paper.pdf", fresh=False)
    assert caught.value.structured.code in {
        "web_provider_crawl_failed",
        "web_provider_format_changed",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"success": False}, {"success": True, "results": []}])
async def test_crawl4ai_pdf_requires_one_successful_result(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    _patch_validation(monkeypatch)
    client = Crawl4aiReaderClient(
        PublicWebConfig(reader_provider="crawl4ai", crawl4ai_api_token=SecretStr("crawl-token")),
        transport_factory=_local_client_transport(lambda _: httpx2.Response(200, json=payload)),
    )
    with pytest.raises(SkillExecutionError):
        await client.read("https://example.org/paper.pdf", fresh=False)


@pytest.mark.asyncio
async def test_crawl4ai_pdf_still_validates_source_before_local_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject(_: str, _resolver: str) -> ValidatedMcpEndpoint:
        raise SkillExecutionError("web_url_forbidden", "Source must be public")

    monkeypatch.setattr(providers, "validate_public_url_with_resolver", reject)
    client = Crawl4aiReaderClient(
        PublicWebConfig(reader_provider="crawl4ai", crawl4ai_api_token=SecretStr("crawl-token")),
        transport_factory=lambda: pytest.fail("rejected target reached the companion"),
    )
    with pytest.raises(SkillExecutionError, match="Source must be public"):
        await client.read("https://internal.example/paper.pdf", fresh=False)


@pytest.mark.asyncio
async def test_crawl4ai_pdf_request_preserves_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def handle(_: httpx2.Request) -> httpx2.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("A cancelled read cannot produce a source")

    client = Crawl4aiReaderClient(
        PublicWebConfig(reader_provider="crawl4ai", crawl4ai_api_token=SecretStr("crawl-token")),
        transport_factory=lambda: httpx2.MockTransport(handle),
    )
    task = asyncio.create_task(client.read("https://example.org/paper.pdf", fresh=False))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("focus", ["4.3.2. Retry Semantics", "Retry Semantics", "absent phrase"])
async def test_markdown_focus_locates_linked_heading_instead_of_contents_or_code(
    monkeypatch: pytest.MonkeyPatch, focus: str
) -> None:
    body = (
        "# Document\n\n* [4.3.2.](https://example.org/#toc) [Retry Semantics](https://example.org/)\n"
        "```markdown\n### 4.3.2. Retry Semantics\nNOT THE REAL SECTION\n```\n"
        + ("Introduction. " * 700).rstrip()
        + "\n### [4.3.2.](https://example.org/#section) [Retry Semantics](https://example.org/)\n"
        + "ACTUAL SOURCE REQUIREMENT.\n"
        + "Following material. " * 500
        + "END"
    )

    async def read(self: object, source_url: str, **kwargs: object) -> ProviderPage:
        return ProviderPage(
            "crawl4ai", source_url, "Document", body, body.encode(), "text/plain", "2026-10-03"
        )

    monkeypatch.setattr(Crawl4aiReaderClient, "read", read)
    result = await PublicWebReader(
        provider_config=PublicWebConfig(reader_provider="crawl4ai")
    ).read({"url": "https://example.org/", "focus": focus, "max_characters": 1000})

    found = focus != "absent phrase"
    assert result["focus_matched"] is found
    assert ("ACTUAL SOURCE REQUIREMENT" in str(result["text"])) is found
    offset = result["text_offset"]
    assert isinstance(offset, int) and (offset > 8000) is found
    assert result["text"] == body[offset : offset + 1000]
    assert result["truncated"] is True
    assert result["total_characters"] == len(body)


@pytest.mark.asyncio
async def test_firecrawl_search_normalizes_results_and_authenticates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "success": True,
                "data": [
                    {
                        "url": "https://example.org/article",
                        "title": "A title",
                        "description": "A snippet",
                    }
                ],
            },
        )

    result = await FirecrawlSearchClient(
        _config(), transport_factory=_client_transport(handle)
    ).search("current rule", 5)

    assert result.provider == "firecrawl"
    assert result.results[0].url == "https://example.org/article"
    assert requests[0].url.path == "/v2/search"
    assert requests[0].headers["authorization"] == "Bearer fire-key"
    assert json.loads(requests[0].content) == {"query": "current rule", "limit": 5}


@pytest.mark.asyncio
async def test_provider_endpoint_uses_selected_dns_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def endpoint(_: str, **kwargs: object) -> ValidatedMcpEndpoint:
        calls.append(kwargs)
        return _ENDPOINT

    monkeypatch.setattr(providers, "_validate_provider_endpoint", endpoint)
    client = FirecrawlSearchClient(
        _config(),
        transport_factory=_client_transport(
            lambda _: httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                json={"success": True, "data": []},
            )
        ),
    )
    await client.search("query", 1, dns_resolver="cloudflare")
    assert len(calls) == 1
    assert calls[0]["dns_resolver"] == "cloudflare"
    assert callable(calls[0]["transport_factory"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "web_provider_auth"),
        (429, "web_provider_rate_limit"),
        (503, "web_provider_unavailable"),
    ],
)
async def test_provider_statuses_are_truthfully_normalized(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    _patch_validation(monkeypatch)
    client = FirecrawlSearchClient(
        _config(),
        transport_factory=_client_transport(lambda _: httpx2.Response(status, text="failure")),
    )
    with pytest.raises(SkillExecutionError) as error:
        await client.search("query", 1)
    assert error.value.structured.code == code
    if status in {429, 503}:
        assert error.value.structured.retryable is True


@pytest.mark.asyncio
async def test_provider_invalid_json_and_missing_key_fail_before_or_after_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    invalid = FirecrawlSearchClient(
        _config(),
        transport_factory=_client_transport(
            lambda _: httpx2.Response(200, headers={"content-type": "application/json"}, text="[]")
        ),
    )
    with pytest.raises(SkillExecutionError) as error:
        await invalid.search("query", 1)
    assert error.value.structured.code == "web_provider_format_changed"

    no_key = FirecrawlSearchClient(
        PublicWebConfig(),
        transport_factory=_client_transport(lambda _: pytest.fail("request must not be sent")),
    )
    with pytest.raises(SkillExecutionError) as error:
        await no_key.search("query", 1)
    assert error.value.structured.code == "web_provider_auth"


@pytest.mark.asyncio
async def test_firecrawl_http_200_rate_limit_envelope_preserves_retry_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    client = FirecrawlSearchClient(
        PublicWebConfig(search_provider="firecrawl", firecrawl_allow_anonymous=True),
        transport_factory=_client_transport(
            lambda _: httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "success": False,
                    "reason": "credits",
                    "error": "You've hit Firecrawl's keyless free tier rate limit.",
                    "retry_after_seconds": 72616,
                },
            )
        ),
    )

    with pytest.raises(SkillExecutionError) as error:
        await client.search("current rule", 3)

    assert error.value.structured.code == "web_provider_rate_limit"
    assert error.value.structured.retryable is True
    assert error.value.structured.details == {
        "provider_reason": "credits",
        "retry_after_seconds": 72616,
    }


@pytest.mark.asyncio
async def test_firecrawl_anonymous_mode_sends_no_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={"success": True, "data": []},
        )

    config = PublicWebConfig(search_provider="firecrawl", firecrawl_allow_anonymous=True)
    await FirecrawlSearchClient(config, transport_factory=_client_transport(handle)).search(
        "official source", 3
    )
    assert requests[0].headers.get("authorization") is None


@pytest.mark.asyncio
async def test_provider_endpoint_dns_policy_rejects_private_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden(_: str, **kwargs: object) -> ValidatedMcpEndpoint:
        del kwargs
        raise SkillExecutionError("mcp_dns_failed", "private endpoint", retryable=False)

    monkeypatch.setattr(providers, "validate_mcp_url", forbidden)
    client = FirecrawlSearchClient(
        _config(),
        transport_factory=_client_transport(lambda _: pytest.fail("request must not be sent")),
    )
    with pytest.raises(SkillExecutionError) as error:
        await client.search("query", 1)
    assert error.value.structured.code == "web_provider_endpoint_forbidden"


@pytest.mark.asyncio
async def test_jina_reader_validates_target_and_requests_fresh_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200, headers={"content-type": "text/markdown"}, text="# Source\n\nBody"
        )

    result = await JinaReaderClient(_config(), transport_factory=_client_transport(handle)).read(
        "https://example.org/article?x=1", fresh=True
    )

    assert result.provider == "jina"
    assert result.title == "Source"
    assert result.text == "# Source\n\nBody"
    assert requests[0].headers["x-no-cache"] == "true"
    assert str(requests[0].url).endswith("https%3A%2F%2Fexample.org%2Farticle%3Fx%3D1")


def test_jina_sogou_parser_keeps_actual_https_sources_and_drops_navigation() -> None:
    parsed = providers._parse_jina_sogou_markdown(  # pyright: ignore[reportPrivateUsage]
        """
### [民航局公告](http://www.caac.gov.cn/XWZX/MHYW/202506/t20250626_227805.html)
官方摘要: 3C 标识和召回批次。

[Sign in](https://accounts.google.com/ServiceLogin)
[微信文章](https://mp.weixin.qq.com/s?id=1)
[QQ视频](https://newsa.html5.qq.com/v1/share-video?vid=1)
[技术文档](https://docs.example/raft)
心跳与选举超时说明。
"""
    )
    assert parsed == (
        ProviderSearchResult(
            "https://www.caac.gov.cn/XWZX/MHYW/202506/t20250626_227805.html",
            "民航局公告",
            "官方摘要: 3C 标识和召回批次。",
        ),
        ProviderSearchResult(
            "https://docs.example/raft",
            "技术文档",
            "心跳与选举超时说明。",
        ),
    )


def test_jina_sogou_parser_drops_nested_search_images() -> None:
    parsed = providers._parse_jina_sogou_markdown(  # pyright: ignore[reportPrivateUsage]
        """
[![Image 1: 搜索](https://img01.sogoucdn.com/app/a/200797/search.png)](https://www.sogou.com/)
### [官方文档](https://docs.example/raft)
"""
    )
    assert parsed == (ProviderSearchResult("https://docs.example/raft", "官方文档", ""),)


@pytest.mark.asyncio
async def test_jina_sogou_search_uses_cloudflare_and_markdown_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "text/markdown"},
            text=("Title: query\n\n### [公告](http://docs.example/official)\n含 3C 和召回条件。\n"),
        )

    result = await JinaSogouSearchClient(
        PublicWebConfig(search_provider="jina_sogou"),
        transport_factory=_client_transport(handle),
    ).search("充电宝 3C", 3, dns_resolver="cloudflare")

    assert result.provider == "jina_sogou"
    assert result.results[0].url == "https://docs.example/official"
    assert requests[0].url.host == "r.jina.ai"
    assert str(requests[0].url).startswith("https://r.jina.ai/https%3A%2F%2Fwww.sogou.com")
    assert requests[0].headers["x-no-cache"] == "true"


@pytest.mark.asyncio
async def test_jina_reader_extracts_title_metadata() -> None:
    async def endpoint(_: str, **kwargs: object) -> ValidatedMcpEndpoint:
        del kwargs
        return _ENDPOINT

    async def source(_: str, _resolver: str = "system", **kwargs: object) -> ValidatedMcpEndpoint:
        del kwargs, _resolver
        return _ENDPOINT

    original_endpoint = providers._validate_provider_endpoint  # pyright: ignore[reportPrivateUsage]
    original_source = providers.validate_public_url_with_resolver
    providers._validate_provider_endpoint = endpoint  # pyright: ignore[reportPrivateUsage]
    providers.validate_public_url_with_resolver = source
    try:
        result = await JinaReaderClient(
            _config(),
            transport_factory=_client_transport(
                lambda _: httpx2.Response(
                    200,
                    headers={"content-type": "text/plain"},
                    text="Title: Provider title\n\nMarkdown body",
                )
            ),
        ).read("https://example.org/article", fresh=False)
    finally:
        providers._validate_provider_endpoint = original_endpoint  # pyright: ignore[reportPrivateUsage]
        providers.validate_public_url_with_resolver = original_source
    assert result.title == "Provider title"


@pytest.mark.asyncio
async def test_external_reader_preserves_cancellation_and_bounds_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    entered = asyncio.Event()

    async def hanging(_: httpx2.Request) -> httpx2.Response:
        entered.set()
        await asyncio.Future()
        raise AssertionError("unreachable")

    reader = PublicWebReader(
        provider_config=_config(reader_provider="jina"),
        transport_factory=lambda _: httpx2.MockTransport(hanging),
    )
    task = asyncio.create_task(reader.read({"url": "https://example.org/"}))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_firecrawl_reader_rejects_missing_markdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    reader = FirecrawlReaderClient(
        _config(),
        transport_factory=_client_transport(
            lambda _: httpx2.Response(
                200,
                headers={"content-type": "application/json"},
                json={"success": True, "data": {}},
            )
        ),
    )
    with pytest.raises(SkillExecutionError) as error:
        await reader.read("https://example.org/", fresh=False)
    assert error.value.structured.code == "web_provider_format_changed"


@pytest.mark.asyncio
async def test_external_reader_timeout_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)

    async def hanging(_: httpx2.Request) -> httpx2.Response:
        await asyncio.Future()
        raise AssertionError("unreachable")

    reader = PublicWebReader(
        provider_config=_config(reader_provider="jina", timeout_seconds=0.01),
        transport_factory=lambda _: httpx2.MockTransport(hanging),
    )
    with pytest.raises(SkillExecutionError) as error:
        await reader.read({"url": "https://example.org/"})
    assert error.value.structured.code == "web_timeout"


@pytest.mark.asyncio
async def test_jina_transient_failure_falls_back_to_firecrawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    calls: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        if request.url.host == "r.jina.ai":
            return httpx2.Response(503, text="temporarily unavailable")
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "success": True,
                "data": {
                    "markdown": "# Fallback\n\nVerified body",
                    "metadata": {"title": "Fallback"},
                },
            },
        )

    result = await PublicWebReader(
        provider_config=_config(reader_provider="jina"),
        transport_factory=_client_transport(handle),
    ).read({"url": "https://example.org/"})

    assert result["provider"] == "firecrawl"
    assert result["title"] == "Fallback"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_jina_failure_can_use_explicit_anonymous_firecrawl_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_validation(monkeypatch)
    calls: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        if request.url.host == "r.jina.ai":
            return httpx2.Response(503, text="temporarily unavailable")
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "success": True,
                "data": {"markdown": "# Anonymous fallback\n\nBody", "metadata": {}},
            },
        )

    result = await PublicWebReader(
        provider_config=PublicWebConfig(reader_provider="jina", firecrawl_allow_anonymous=True),
        transport_factory=_client_transport(handle),
    ).read({"url": "https://example.org/"})

    assert result["provider"] == "firecrawl"
    assert len(calls) == 2
