"""Index links are bounded source data, never permission to follow them."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import cast

import httpx2
import pytest
from chatwaifu_protocol.base import JsonObject
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.public_web import PublicWebReader


@pytest.fixture
async def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(
        *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)


def _reader(handler: Callable[[httpx2.Request], httpx2.Response]) -> PublicWebReader:
    return PublicWebReader(transport_factory=lambda _: httpx2.MockTransport(handler))


@pytest.mark.asyncio
async def test_links_are_opt_in_and_never_followed(public_dns: None) -> None:
    requests: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        return httpx2.Response(
            200, headers={"content-type": "text/html"}, text='<a href="/next">Next source</a>'
        )

    result = await _reader(handle).read({"url": "https://example.org/index"})
    assert result["text"] == "Next source"
    assert result["links_requested"] is False
    assert result["links"] == [] and result["links_truncated"] is False
    assert requests == ["https://example.org/index"]


@pytest.mark.asyncio
async def test_actual_links_use_final_url_and_selected_body_only(public_dns: None) -> None:
    requests: list[str] = []
    body = """<head></head>
    <a href="/chrome">Site chrome</a><main>
    <a href="next.html#rules"> Next <em>source</em> </a>
    <a href="next.html#rules">Duplicate</a>
    <a href="http://example.org/legacy">Legacy HTTP</a>
    <a href="//other.example/article">Other source</a>
    <a href="#section">Same page</a><a href="javascript:alert(1)">Script</a>
    <a href="https://user:password@example.org/private">Credential URL</a>
    <a href="https://127.0.0.1/">Loopback</a><a href="https://device.local/">Local host</a>
    <a href="https://example.org:8443/">Unsupported port</a>
    <a href="/empty"><img src="image.png"></a>
    <div hidden><a href="/hidden">Hidden source</a></div>
    <nav><a href="/nav">Navigation</a></nav>
    <script><a href="/script">Fake link</a></script>
    </main><footer><a href="/footer">Footer</a></footer>"""

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        if request.url.path == "/start":
            return httpx2.Response(302, headers={"location": "/docs/index.html"})
        return httpx2.Response(200, headers={"content-type": "text/html"}, text=body)

    result = await _reader(handle).read({"url": "https://example.org/start", "max_links": 20})
    assert result["url"] == "https://example.org/docs/index.html"
    assert result["extraction_method"] == "main_content"
    assert result["links_scope"] == "selected_source"
    assert result["links"] == [
        {
            "url": "https://example.org/docs/next.html#rules",
            "label": "Next source",
            "label_truncated": False,
        },
        {"url": "http://example.org/legacy", "label": "Legacy HTTP", "label_truncated": False},
        {"url": "https://other.example/article", "label": "Other source", "label_truncated": False},
    ]
    assert result["links_requested"] is True and result["links_truncated"] is False
    assert requests == ["https://example.org/start", "https://example.org/docs/index.html"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base,expected",
    [
        ("/directory/", "https://example.org/directory/next"),
        ("https://other.example/docs/", "https://other.example/docs/next"),
        ("http://other.example/docs/", "http://other.example/docs/next"),
        ("https://127.0.0.1/docs/", None),
        ("javascript:ignored", None),
    ],
)
async def test_first_head_base_preserves_actual_destinations_without_network(
    public_dns: None, base: str, expected: str | None
) -> None:
    requested: list[str] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(
            200,
            headers={"content-type": "text/html"},
            text=f'<head><base href="{base}"><base href="https://ignored.example/"></head>'
            '<main><a href="next">Relative source</a>'
            '<a href="https://absolute.example/next">Absolute source</a></main>',
        )

    result = await _reader(handle).read({"url": "https://example.org/page", "max_links": 20})
    links = cast(list[JsonObject], result["links"])
    assert [link["url"] for link in links] == ([expected] if expected else []) + [
        "https://absolute.example/next"
    ]
    assert requested == ["https://example.org/page"]


@pytest.mark.asyncio
async def test_link_count_limit_is_honest_and_keeps_document_order(public_dns: None) -> None:
    body = "".join(f'<a href="/article/{i}">Source {i}</a>' for i in range(25))
    result = await _reader(
        lambda _: httpx2.Response(200, headers={"content-type": "text/html"}, text=body)
    ).read({"url": "https://example.org/", "max_links": 2})
    links = cast(list[JsonObject], result["links"])
    assert [link["url"] for link in links] == [
        "https://example.org/article/0",
        "https://example.org/article/1",
    ]
    assert result["links_truncated"] is True


@pytest.mark.asyncio
async def test_link_payload_budget_handles_multibyte_labels(public_dns: None) -> None:
    title = "😀" * 240
    body = (
        f"<head><title>{title}</title></head>"
        + "😀" * 6000
        + "".join(
            f'<a href="https://other.example/{i}/' + "x" * 1200 + '">' + "😀" * 300 + "</a>"
            for i in range(4)
        )
    )
    result = await _reader(
        lambda _: httpx2.Response(200, headers={"content-type": "text/html"}, text=body)
    ).read({"url": "https://example.org/" + "x" * 1950, "max_links": 20})
    links = cast(list[JsonObject], result["links"])
    assert links and result["links_truncated"] is True and result["truncated"] is True
    assert all(len(cast(str, link["label"])) == 240 and link["label_truncated"] for link in links)
    assert sum(len(json.dumps(link, ensure_ascii=False).encode()) for link in links) <= 3000
    assert (
        len(
            json.dumps({"untrusted": True, "ok": True, "data": result}, ensure_ascii=False).encode()
        )
        < 32768
    )


@pytest.mark.asyncio
async def test_links_outside_excerpt_are_explicitly_source_scoped(public_dns: None) -> None:
    body = "x" * 1500 + '<a href="/later">Later actual source</a>'
    result = await _reader(
        lambda _: httpx2.Response(200, headers={"content-type": "text/html"}, text=body)
    ).read({"url": "https://example.org/", "max_characters": 1000, "max_links": 2})
    assert "Later actual source" not in cast(str, result["text"])
    assert result["truncated"] is True and result["links_scope"] == "selected_source"
    assert result["links"] == [
        {
            "url": "https://example.org/later",
            "label": "Later actual source",
            "label_truncated": False,
        }
    ]


@pytest.mark.asyncio
async def test_plain_text_does_not_invent_anchor_links(public_dns: None) -> None:
    result = await _reader(
        lambda _: httpx2.Response(
            200, headers={"content-type": "text/plain"}, text="See https://example.org/next"
        )
    ).read({"url": "https://example.org/", "max_links": 20})
    assert result["links_requested"] is True
    assert result["links"] == [] and result["links_truncated"] is False


@pytest.mark.asyncio
async def test_destination_is_not_resolved_until_an_explicit_next_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved: list[str] = []
    requested: list[str] = []

    async def resolve(
        host: str, *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        resolved.append(host)
        return [(2, 1, 6, "", ("127.0.0.1" if host == "private.example" else "93.184.216.34", 443))]

    def handle(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(
            200,
            headers={"content-type": "text/html"},
            text='<a href="https://private.example/next">Unverified destination</a>',
        )

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    reader = _reader(handle)
    result = await reader.read({"url": "https://example.org/", "max_links": 2})
    assert result["links"] == [
        {
            "url": "https://private.example/next",
            "label": "Unverified destination",
            "label_truncated": False,
        }
    ]
    assert resolved == ["example.org"] and requested == ["https://example.org/"]
    with pytest.raises(SkillExecutionError) as error:
        await reader.read({"url": "https://private.example/next"})
    assert error.value.structured.code == "web_url_forbidden"
    assert resolved == ["example.org", "private.example"]
    assert requested == ["https://example.org/"]


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", [-1, 21, True, 1.5, "2", None])
async def test_invalid_link_limit_is_rejected_before_network(
    maximum: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_dns(*args: object, **kwargs: object) -> None:
        raise AssertionError("Invalid arguments must not reach DNS")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", no_dns)

    def forbidden(_: httpx2.Request) -> httpx2.Response:
        raise AssertionError("Invalid arguments must not reach the network")

    with pytest.raises(SkillExecutionError) as error:
        await _reader(forbidden).read(
            cast(JsonObject, {"url": "https://example.org/", "max_links": maximum})
        )
    assert error.value.structured.code == "web_invalid_arguments"
