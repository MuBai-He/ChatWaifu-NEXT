"""Permissioned public HTTPS source reads with pinned, bounded network access."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Literal, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx2
from chatwaifu_protocol.base import JsonObject, JsonValue

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.transports import (
    PinnedAsyncHTTPTransport,
    ValidatedMcpEndpoint,
    validate_mcp_url,
)

MAX_SOURCE_RESPONSE_BYTES = 1024 * 1024
MAX_SOURCE_CHARACTERS = 6000
MAX_SOURCE_URL_CHARACTERS = 2048
MAX_SOURCE_REDIRECTS = 3
MAX_SOURCE_HTML_NODES = 20_000
MAX_SOURCE_HTML_DEPTH = 128
MAX_DNS_RESPONSE_BYTES = 16 * 1024
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_IGNORED_TAGS = {"script", "style", "nav", "footer", "aside", "template", "noscript", "svg"}
_CONTENT_CLASSES = {
    "article-body",
    "article-content",
    "entry-content",
    "post-content",
    "story-body",
    "trs_editor",
}
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "li",
    "ul",
    "ol",
    "pre",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "dt",
    "dd",
    "tr",
    "br",
    "hr",
}


def normalize_public_source_url(url: str) -> str:
    try:
        if len(url) > MAX_SOURCE_URL_CHARACTERS or any(ord(char) <= 32 for char in url):
            raise ValueError("invalid URL length or whitespace")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
        ):
            raise ValueError("not a credential-free HTTPS 443 source")
        # Fragments locate within a document and are never sent to the server.
        normalized = str(httpx2.URL(urlunsplit(parsed._replace(fragment=""))))
        if len(normalized) > MAX_SOURCE_URL_CHARACTERS:
            raise ValueError("encoded URL too long")
        return normalized
    except (ValueError, httpx2.InvalidURL) as error:
        raise SkillExecutionError(
            "web_invalid_url",
            "Source URL must be a public HTTPS URL without credentials on port 443",
        ) from error


async def validate_public_url(url: str) -> ValidatedMcpEndpoint:
    """Reuse address pins, while excluding the MCP loopback/LAN exceptions."""
    normalized = normalize_public_source_url(url)
    try:
        endpoint = await validate_mcp_url(normalized, allow_remote=True)
    except SkillExecutionError as error:
        code = (
            "web_dns_failed" if error.structured.code == "mcp_dns_failed" else "web_url_forbidden"
        )
        raise SkillExecutionError(
            code,
            "Source URL must resolve only to public Internet addresses",
            retryable=error.structured.retryable,
        ) from error
    if any(not address.is_global for address in endpoint.addresses):
        raise SkillExecutionError(
            "web_url_forbidden", "Source URL must resolve only to public Internet addresses"
        )
    return endpoint


@dataclass(frozen=True, slots=True)
class PublicWebPage:
    url: str
    body: bytes
    decoded: str
    content_type: str
    retrieved_at: str
    dns_resolver: Literal["system", "cloudflare"]


class PublicWebReader:
    """One read creates and closes its HTTP clients; no cookies or durable content."""

    def __init__(
        self,
        *,
        transport_factory: Callable[[ValidatedMcpEndpoint], httpx2.AsyncBaseTransport]
        | None = None,
        timeout_seconds: float = 25,
    ) -> None:
        self._transport_factory = transport_factory or PinnedAsyncHTTPTransport
        self._timeout_seconds = timeout_seconds

    async def read(self, arguments: JsonObject) -> JsonObject:
        url = arguments.get("url")
        focus = arguments.get("focus")
        maximum = arguments.get("max_characters", MAX_SOURCE_CHARACTERS)
        resolver = arguments.get("dns_resolver", "system")
        if (
            not isinstance(url, str)
            or (focus is not None and (not isinstance(focus, str) or not 1 <= len(focus) <= 128))
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not 1000 <= maximum <= MAX_SOURCE_CHARACTERS
            or not isinstance(resolver, str)
            or resolver not in {"system", "cloudflare"}
        ):
            raise SkillExecutionError(
                "web_invalid_arguments", "Invalid source URL, focus, or excerpt length"
            )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._read(
                    normalize_public_source_url(url),
                    focus,
                    maximum,
                    cast(Literal["system", "cloudflare"], resolver),
                )
        except (TimeoutError, httpx2.TimeoutException) as error:
            raise SkillExecutionError(
                "web_timeout", "Public source read timed out", retryable=True
            ) from error
        except httpx2.HTTPError as error:
            raise SkillExecutionError(
                "web_network_error", "Public source could not be read", retryable=True
            ) from error
        except SkillExecutionError as error:
            if error.structured.code.startswith("mcp_"):
                code = (
                    "web_response_limit"
                    if error.structured.code == "mcp_response_limit"
                    else "web_invalid_response"
                )
                raise SkillExecutionError(
                    code, "Public source failed its network response validation"
                ) from error
            raise

    async def _read(
        self, url: str, focus: str | None, maximum: int, resolver: Literal["system", "cloudflare"]
    ) -> JsonObject:
        page = await self.fetch(url, dns_resolver=resolver)
        source = _source_text(page.decoded, page.content_type)
        title, text = source.title, source.text
        if not text.strip():
            raise SkillExecutionError(
                "web_empty_content", "Public source contained no readable text"
            )
        match = re.search(re.escape(focus), text, re.IGNORECASE) if focus else None
        # Do not discard earlier conditions when the whole source fits. Near the
        # end, move the window back to use the available excerpt length fully.
        offset = (
            min(max(0, match.start() - 160), max(0, len(text) - maximum))
            if match is not None
            else 0
        )
        excerpt = text[offset : offset + maximum]
        return {
            "url": page.url,
            "title": title[:240],
            "text": excerpt,
            "retrieved_at": page.retrieved_at,
            "content_type": page.content_type,
            "body_sha256": hashlib.sha256(page.body).hexdigest(),
            "total_characters": len(text),
            "text_offset": offset,
            "truncated": offset > 0 or offset + len(excerpt) < len(text),
            "focus_matched": bool(match) if focus else None,
            "dns_resolver": resolver,
            "extraction_method": source.extraction_method,
            "document_characters": source.document_characters,
        }

    async def fetch(
        self, url: str, *, dns_resolver: Literal["system", "cloudflare"] = "system"
    ) -> PublicWebPage:
        """Internal source adapter boundary shared with discovery; never exposed as a tool."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._fetch(normalize_public_source_url(url), dns_resolver)
        except (TimeoutError, httpx2.TimeoutException) as error:
            raise SkillExecutionError(
                "web_timeout", "Public source read timed out", retryable=True
            ) from error
        except httpx2.HTTPError as error:
            raise SkillExecutionError(
                "web_network_error", "Public source could not be read", retryable=True
            ) from error
        except SkillExecutionError as error:
            if error.structured.code.startswith("mcp_"):
                code = (
                    "web_response_limit"
                    if error.structured.code == "mcp_response_limit"
                    else "web_invalid_response"
                )
                raise SkillExecutionError(
                    code, "Public source failed its network response validation"
                ) from error
            raise

    async def _fetch(self, url: str, resolver: Literal["system", "cloudflare"]) -> PublicWebPage:
        for redirect_count in range(MAX_SOURCE_REDIRECTS + 1):
            endpoint = (
                await validate_public_url(url)
                if resolver == "system"
                else await self._cloudflare_endpoint(url)
            )
            async with httpx2.AsyncClient(
                transport=self._transport_factory(endpoint),
                timeout=httpx2.Timeout(self._timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                headers={
                    "User-Agent": "ChatWaifu-NEXT/0.1 public-source-reader",
                    "Accept": "text/html,application/xhtml+xml,text/plain",
                    "Accept-Encoding": "identity",
                },
            ) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location or redirect_count == MAX_SOURCE_REDIRECTS:
                            raise SkillExecutionError(
                                "web_redirect_limit", "Public source redirect could not be followed"
                            )
                        url = normalize_public_source_url(urljoin(url, location))
                        continue
                    if response.status_code != 200:
                        raise SkillExecutionError(
                            "web_http_error",
                            "Public source returned an unsuccessful HTTP status",
                            retryable=response.status_code >= 500,
                            details={"status_code": response.status_code},
                        )
                    content_type = (
                        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    )
                    if content_type not in _CONTENT_TYPES:
                        raise SkillExecutionError(
                            "web_content_type", "Public source must contain HTML or plain text"
                        )
                    body = await _bounded_body(response)
                    try:
                        decoded = body.decode(response.encoding or "utf-8", errors="replace")
                    except LookupError as error:
                        raise SkillExecutionError(
                            "web_encoding",
                            "Public source declared an unsupported character encoding",
                        ) from error
            return PublicWebPage(
                url=url,
                body=body,
                decoded=decoded,
                content_type=content_type,
                retrieved_at=datetime.now(UTC).isoformat(),
                dns_resolver=resolver,
            )
        raise AssertionError("redirect loop must return or fail")

    async def _cloudflare_endpoint(self, url: str) -> ValidatedMcpEndpoint:
        hostname = urlsplit(url).hostname
        assert hostname is not None
        hostname = hostname.rstrip(".").lower()
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            # Literal addresses must be policy checked, never looked up elsewhere.
            return await validate_public_url(url)
        if hostname == "localhost" or hostname.endswith((".local", ".localhost")):
            raise SkillExecutionError(
                "web_url_forbidden", "Local source hostnames are not public sources"
            )
        bootstrap = ValidatedMcpEndpoint(
            hostname="cloudflare-dns.com", port=443, addresses=(ipaddress.ip_address("1.1.1.1"),)
        )
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        try:
            async with httpx2.AsyncClient(
                transport=self._transport_factory(bootstrap),
                trust_env=False,
                follow_redirects=False,
                timeout=5,
                headers={"Accept": "application/dns-json", "Accept-Encoding": "identity"},
            ) as client:
                for name, record_type in (("A", 1), ("AAAA", 28)):
                    client.cookies.clear()
                    async with client.stream(
                        "GET",
                        "https://cloudflare-dns.com/dns-query",
                        params={"name": hostname, "type": name},
                    ) as response:
                        if response.status_code != 200:
                            raise _dns_error()
                        body = await _bounded_body(response, limit=MAX_DNS_RESPONSE_BYTES)
                    payload = cast(JsonValue, json.loads(body))
                    for address in _dns_addresses(payload, hostname, record_type):
                        if address not in addresses:
                            addresses.append(address)
        except (httpx2.HTTPError, ValueError, SkillExecutionError) as error:
            raise _dns_error() from error
        if not addresses or any(not address.is_global for address in addresses):
            raise SkillExecutionError(
                "web_url_forbidden", "Source DNS must return only public Internet addresses"
            )
        return ValidatedMcpEndpoint(hostname=hostname, port=443, addresses=tuple(addresses))


async def _bounded_body(
    response: httpx2.Response, *, limit: int = MAX_SOURCE_RESPONSE_BYTES
) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
            if length < 0:
                raise ValueError("negative length")
        except ValueError as error:
            raise SkillExecutionError(
                "web_invalid_response", "Public source declared an invalid Content-Length"
            ) from error
        if length > limit:
            raise _response_limit()
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in {"identity", "", "gzip", "deflate"}:
        raise SkillExecutionError(
            "web_encoding", "Public source used an unsupported content encoding"
        )
    # In-memory test transports may already have consumed/decoded their response.
    if response.is_stream_consumed:
        if len(response.content) > limit:
            raise _response_limit()
        return response.content
    decoder = (
        zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS)
        if encoding in {"gzip", "deflate"}
        else None
    )
    output = bytearray()
    received = 0
    try:
        async for chunk in response.aiter_raw(chunk_size=16384):
            received += len(chunk)
            if received > limit:
                raise _response_limit()
            decoded = decoder.decompress(chunk, limit + 1 - len(output)) if decoder else chunk
            output.extend(decoded)
            if len(output) > limit or (decoder is not None and decoder.unconsumed_tail):
                raise _response_limit()
        if decoder is not None and (not decoder.eof or decoder.unused_data):
            raise SkillExecutionError(
                "web_invalid_response",
                "Public source used an incomplete or multiple compressed stream",
            )
    except zlib.error as error:
        raise SkillExecutionError(
            "web_invalid_response", "Public source contained invalid compressed data"
        ) from error
    return bytes(output)


def _response_limit() -> SkillExecutionError:
    return SkillExecutionError("web_response_limit", "Public source exceeded its response limit")


def _dns_error() -> SkillExecutionError:
    return SkillExecutionError(
        "web_dns_failed", "Public encrypted DNS resolution failed", retryable=True
    )


def _dns_addresses(
    payload: JsonValue, hostname: str, record_type: int
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    if (
        not isinstance(payload, dict)
        or type(payload.get("Status")) is not int
        or payload.get("Status") != 0
        or payload.get("TC") is not False
    ):
        raise _dns_error()
    questions = payload.get("Question")
    if not isinstance(questions, list) or len(questions) != 1 or not isinstance(questions[0], dict):
        raise _dns_error()
    question = questions[0]
    query_name = question.get("name")
    if (
        not isinstance(query_name, str)
        or query_name.rstrip(".").lower() != hostname
        or type(question.get("type")) is not int
        or question.get("type") != record_type
    ):
        raise _dns_error()
    answers = payload.get("Answer", [])
    if not isinstance(answers, list) or len(answers) > 64:
        raise _dns_error()
    records: list[tuple[str, int, str]] = []
    for answer in answers:
        if not isinstance(answer, dict):
            raise _dns_error()
        owner, kind, data = answer.get("name"), answer.get("type"), answer.get("data")
        if not isinstance(owner, str) or type(kind) is not int or not isinstance(data, str):
            raise _dns_error()
        records.append((owner.rstrip(".").lower(), kind, data))
    allowed_names = {hostname}
    for _ in range(8):
        expanded = allowed_names | {
            data.rstrip(".").lower()
            for owner, kind, data in records
            if owner in allowed_names and kind == 5
        }
        if expanded == allowed_names:
            break
        allowed_names = expanded
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for owner, kind, data in records:
        if owner in allowed_names and kind == record_type:
            address = ipaddress.ip_address(data)
            if address.version != (4 if record_type == 1 else 6):
                raise _dns_error()
            addresses.append(address)
    return addresses


@dataclass(slots=True)
class _ContentRegion:
    start: int
    end: int | None = None


@dataclass(frozen=True, slots=True)
class _SourceText:
    title: str
    text: str
    extraction_method: Literal["plain_text", "visible_text", "main_content"]
    document_characters: int


def _is_content_region(tag: str, attributes: dict[str, str | None]) -> bool:
    # Explicit document/CMS body markers only. Generic "content", "bottom" or
    # words in source prose cannot safely distinguish an article from site chrome.
    return (
        tag in {"main", "article"}
        or "main" in (attributes.get("role") or "").lower().split()
        or "articlebody" in (attributes.get("itemprop") or "").lower().split()
        or bool(_CONTENT_CLASSES.intersection((attributes.get("class") or "").lower().split()))
        or attributes.get("data-role") == "n_content"
    )


class _SourceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title: list[str] = []
        self.stack: list[tuple[str, bool, _ContentRegion | None]] = []
        self.regions: list[_ContentRegion] = []
        self.nodes = 0

    def _count_node(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_SOURCE_HTML_NODES or len(self.stack) >= MAX_SOURCE_HTML_DEPTH:
            raise SkillExecutionError(
                "web_parse_limit", "Public source exceeded its HTML parsing limit"
            )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._count_node()
        attributes = dict(attrs)
        style = (attributes.get("style") or "").replace(" ", "").lower()
        ignored = (
            (bool(self.stack) and self.stack[-1][1])
            or tag in _IGNORED_TAGS
            or "hidden" in attributes
            or (attributes.get("aria-hidden") or "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if tag in _BLOCK_TAGS and not ignored:
            self.parts.append("\n")
        if tag not in _VOID_TAGS:
            region = None
            if (
                not ignored
                and _is_content_region(tag, attributes)
                and not any(frame[2] is not None for frame in self.stack)
            ):
                region = _ContentRegion(start=len(self.parts))
                self.regions.append(region)
            self.stack.append((tag, ignored, region))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        self._count_node()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                if tag in _BLOCK_TAGS and not self.stack[index][1]:
                    self.parts.append("\n")
                for _, _, region in self.stack[index:]:
                    if region is not None:
                        region.end = len(self.parts)
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self._count_node()
        if self.stack and self.stack[-1][1]:
            return
        tags = {tag for tag, _, _ in self.stack}
        if "title" in tags:
            self.title.append(data)
        elif "head" not in tags:
            self.parts.append(data)


def _source_text(decoded: str, content_type: str) -> _SourceText:
    if content_type == "text/plain":
        text = _clean_text(decoded)
        return _SourceText("", text, "plain_text", len(text))
    parser = _SourceParser()
    parser.feed(decoded)
    parser.close()
    title = _clean_text("".join(parser.title)).strip()
    visible = _clean_text("".join(parser.parts))
    # Several independent bodies may be a document index or multiple articles;
    # preserve all visible text rather than silently selecting one of them.
    if len(parser.regions) == 1 and parser.regions[0].end is not None:
        region = parser.regions[0]
        content = _clean_text("".join(parser.parts[region.start : region.end]))
        if content.strip():
            return _SourceText(title, content, "main_content", len(visible))
    return _SourceText(title, visible, "visible_text", len(visible))


def _clean_text(text: str) -> str:
    text = _CONTROL.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    # Retain indentation in documentation examples and plain text source code.
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")
