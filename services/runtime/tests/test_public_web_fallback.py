"""Optional HTML recovery never bypasses permission, source safety or deadlines."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
from pathlib import Path
from typing import cast

import httpx2
import pytest
import yaml
from chatwaifu_runtime.config.settings import PublicWebConfig
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.public_web import PublicWebPage, PublicWebReader
from chatwaifu_runtime.runtime_skills.public_web_providers import Crawl4aiReaderClient
from chatwaifu_runtime.runtime_skills.transports import ValidatedMcpEndpoint
from jsonschema import validate
from pydantic import SecretStr

URL = "https://source.example/article"
ROOT = Path(__file__).resolve().parents[3]


def config(enabled: bool = True) -> PublicWebConfig:
    return PublicWebConfig(reader_provider="crawl4ai", crawl4ai_builtin_fallback=enabled)


def fail_crawler(monkeypatch: pytest.MonkeyPatch, code: str = "web_provider_unavailable") -> None:
    async def read(self: object, source_url: str, **kwargs: object) -> None:
        raise SkillExecutionError(
            code,
            "private provider failure must not escape",
            details={"status_code": 503, "private": "secret"},
        )

    monkeypatch.setattr(Crawl4aiReaderClient, "read", read)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["web_provider_unavailable", "web_provider_network_error", "web_timeout"]
)
async def test_opt_in_fallback_preserves_actual_source_focus_resolver_and_schema(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    fail_crawler(monkeypatch, code)
    fetched: list[tuple[str, str]] = []
    html = (
        "<title>Original</title><article>"
        + "prefix " * 1100
        + "TARGET original condition"
        + " ending" * 400
        + "</article>"
    )

    async def fetch(self: object, url: str, *, dns_resolver: str) -> PublicWebPage:
        fetched.append((url, dns_resolver))
        return PublicWebPage(
            url, html.encode(), html, "text/html", "2026-10-04T00:00:00Z", "cloudflare"
        )

    monkeypatch.setattr(PublicWebReader, "fetch", fetch)
    result = await PublicWebReader(provider_config=config()).read(
        {"url": URL, "dns_resolver": "cloudflare", "focus": "TARGET", "max_characters": 1000}
    )
    assert fetched == [(URL, "cloudflare")]
    assert result["provider"] == "builtin" and result["url"] == URL
    assert result["body_sha256"] == hashlib.sha256(html.encode()).hexdigest()
    assert result["focus_matched"] is True and "TARGET original condition" in str(result["text"])
    assert cast(int, result["text_offset"]) > 0 and result["truncated"] is True
    assert result["provider_fallback"] == {
        "from_provider": "crawl4ai",
        "reason": code,
        "http_status": 503,
    }
    assert "secret" not in json.dumps(result) and "private provider" not in json.dumps(result)
    manifest = yaml.safe_load(
        (ROOT / "skills/builtin/web-read/chatwaifu.yaml").read_text(encoding="utf-8")
    )
    schema = manifest["definition"]["capabilities"][0]["output_schema"]
    validate(result, schema)


@pytest.mark.asyncio
async def test_crawl4ai_fallback_default_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    fail_crawler(monkeypatch)
    reader = PublicWebReader(provider_config=PublicWebConfig(reader_provider="crawl4ai"))

    async def forbidden(self: object, *args: object, **kwargs: object) -> None:
        pytest.fail("default must not dispatch builtin")

    monkeypatch.setattr(PublicWebReader, "fetch", forbidden)
    with pytest.raises(SkillExecutionError) as error:
        await reader.read({"url": URL})
    assert error.value.structured.code == "web_provider_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "web_provider_auth",
        "web_provider_rate_limit",
        "web_url_forbidden",
        "web_dns_failed",
        "web_provider_endpoint_forbidden",
        "web_provider_format_changed",
    ],
)
async def test_fallback_does_not_retry_auth_rate_security_or_format_errors(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    fail_crawler(monkeypatch, code)

    async def forbidden(self: object, *args: object, **kwargs: object) -> None:
        pytest.fail("this error must not dispatch builtin")

    monkeypatch.setattr(PublicWebReader, "fetch", forbidden)
    with pytest.raises(SkillExecutionError) as error:
        await PublicWebReader(provider_config=config()).read({"url": URL})
    assert error.value.structured.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url", ["https://source.example/paper.pdf", "https://source.example/paper.PDF?download=1"]
)
async def test_pdf_never_falls_back_to_html_reader(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    fail_crawler(monkeypatch)

    async def forbidden(self: object, *args: object, **kwargs: object) -> None:
        pytest.fail("builtin cannot extract PDF")

    monkeypatch.setattr(PublicWebReader, "fetch", forbidden)
    with pytest.raises(SkillExecutionError) as error:
        await PublicWebReader(provider_config=config()).read({"url": url})
    assert error.value.structured.code == "web_provider_unavailable"


@pytest.mark.asyncio
async def test_failed_fallback_preserves_final_and_first_scalar_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fail_crawler(monkeypatch)
    calls = 0

    async def fetch(self: object, *args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        raise SkillExecutionError(
            "web_http_error",
            "Public source returned an unsuccessful HTTP status",
            details={"status_code": 404},
        )

    monkeypatch.setattr(PublicWebReader, "fetch", fetch)
    with pytest.raises(SkillExecutionError) as error:
        await PublicWebReader(provider_config=config()).read({"url": URL})
    assert calls == 1 and error.value.structured.code == "web_http_error"
    assert error.value.structured.details == {
        "status_code": 404,
        "provider_fallback": {
            "from_provider": "crawl4ai",
            "reason": "web_provider_unavailable",
            "http_status": 503,
        },
    }
    assert "secret" not in error.value.structured.model_dump_json()


@pytest.mark.asyncio
async def test_fallback_shares_parent_timeout_and_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fail_crawler(monkeypatch)
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def fetch(self: object, *args: object, **kwargs: object) -> None:
        entered.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    monkeypatch.setattr(PublicWebReader, "fetch", fetch)
    reader = PublicWebReader(provider_config=config(), timeout_seconds=0.01)
    with pytest.raises(SkillExecutionError) as error:
        await reader.read({"url": URL})
    assert error.value.structured.code == "web_timeout" and stopped.is_set()
    assert error.value.structured.details["provider_fallback"] == {
        "from_provider": "crawl4ai",
        "reason": "web_provider_unavailable",
        "http_status": 503,
    }
    entered.clear()
    stopped.clear()
    task = asyncio.create_task(PublicWebReader(provider_config=config()).read({"url": URL}))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_fallback_never_transmits_companion_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fail_crawler(monkeypatch)
    requests: list[httpx2.Request] = []

    async def endpoint(self: object, url: str) -> ValidatedMcpEndpoint:
        assert url == URL
        return ValidatedMcpEndpoint(
            hostname="source.example", port=443, addresses=(ipaddress.ip_address("93.184.216.34"),)
        )

    monkeypatch.setattr(PublicWebReader, "_cloudflare_endpoint", endpoint)

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200, headers={"content-type": "text/plain"}, text="Actual public body"
        )

    reader = PublicWebReader(
        provider_config=PublicWebConfig(
            reader_provider="crawl4ai",
            crawl4ai_api_token=SecretStr("private-crawler-credential"),
            crawl4ai_builtin_fallback=True,
        ),
        transport_factory=lambda _: httpx2.MockTransport(handle),
    )
    result = await reader.read({"url": URL, "dns_resolver": "cloudflare"})
    assert result["text"] == "Actual public body" and len(requests) == 1
    assert "authorization" not in requests[0].headers
    assert "private-crawler-credential" not in str(requests[0].headers)
    assert "private-crawler-credential" not in json.dumps(result)
