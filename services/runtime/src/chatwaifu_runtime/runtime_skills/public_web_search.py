"""Bounded public source discovery, with explicit query egress and truthful failures."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
from html.parser import HTMLParser
from typing import Literal, cast
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from chatwaifu_protocol.base import JsonObject, JsonValue

from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.public_web import (
    MAX_SOURCE_HTML_DEPTH,
    MAX_SOURCE_HTML_NODES,
    PublicWebReader,
    normalize_public_source_url,
)

MAX_SEARCH_RESULTS = 5
_SEARCH_ROOT = "https://lite.duckduckgo.com/lite/"
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}")
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


def _domain(value: str) -> str:
    try:
        result = value.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as error:
        raise SkillExecutionError("web_search_arguments", "Invalid source domain scope") from error
    if (
        len(result) > 253
        or not _DOMAIN.fullmatch(result)
        or result.endswith((".local", ".localhost"))
    ):
        raise SkillExecutionError("web_search_arguments", "Source domains must be public DNS names")
    return result


class PublicWebSearch:
    def __init__(self, reader: PublicWebReader) -> None:
        self._reader = reader

    async def search(self, arguments: JsonObject) -> JsonObject:
        query = arguments.get("query")
        maximum = arguments.get("max_results", MAX_SEARCH_RESULTS)
        scopes = arguments.get("domains", [])
        resolver = arguments.get("dns_resolver", "system")
        if (
            not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 256
            or any(ord(char) < 32 for char in query)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not 1 <= maximum <= MAX_SEARCH_RESULTS
            or not isinstance(scopes, list)
            or len(scopes) > 3
            or not all(isinstance(value, str) for value in scopes)
            or not isinstance(resolver, str)
            or resolver not in {"system", "cloudflare"}
        ):
            raise SkillExecutionError(
                "web_search_arguments", "Invalid search query, scope or result limit"
            )
        domains = [_domain(value) for value in cast(list[str], scopes)]
        scoped_query = query.strip()
        if domains:
            scope_query = " OR ".join(f"site:{domain}" for domain in domains)
            scoped_query += " " + (f"({scope_query})" if len(domains) > 1 else scope_query)
        url = _SEARCH_ROOT + "?" + urlencode({"q": scoped_query})
        try:
            async with asyncio.timeout(25):
                page = await self._reader.fetch(
                    url, dns_resolver=cast(Literal["system", "cloudflare"], resolver)
                )
        except SkillExecutionError as error:
            if error.structured.details.get("status_code") == 202:
                raise SkillExecutionError(
                    "web_search_challenge", "Search provider requested interactive verification"
                ) from error
            raise
        except TimeoutError as error:
            raise SkillExecutionError(
                "web_timeout", "Public search timed out", retryable=True
            ) from error
        if urlsplit(page.url).hostname not in {"lite.duckduckgo.com", "duckduckgo.com"}:
            raise SkillExecutionError(
                "web_search_format_changed", "Search provider redirected outside its result site"
            )
        parser = _SearchParser()
        parser.feed(page.decoded)
        parser.close()
        if parser.challenge:
            raise SkillExecutionError(
                "web_search_challenge", "Search provider requested interactive verification"
            )
        if parser.rows and parser.no_results:
            raise SkillExecutionError(
                "web_search_format_changed",
                "Search response contained contradictory result markers",
            )
        if not parser.rows and not parser.no_results:
            raise SkillExecutionError(
                "web_search_format_changed", "Search response contained no recognized results"
            )
        results: list[JsonValue] = []
        seen: set[str] = set()
        filtered = 0
        for raw in parser.rows:
            try:
                result_url = _result_url(raw["url"], page.url)
            except (ValueError, SkillExecutionError):
                filtered += 1
                continue
            hostname = urlsplit(result_url).hostname or ""
            if result_url in seen or (
                domains and not any(hostname == d or hostname.endswith("." + d) for d in domains)
            ):
                filtered += 1
                continue
            seen.add(result_url)
            results.append(
                {"url": result_url, "title": raw["title"][:240], "snippet": raw["snippet"][:480]}
            )
        return {
            "provider": "duckduckgo_lite",
            "query": query.strip(),
            "search_url": page.url,
            "retrieved_at": page.retrieved_at,
            "body_sha256": hashlib.sha256(page.body).hexdigest(),
            "dns_resolver": resolver,
            "results": results[:maximum],
            "total_matched": len(results),
            "filtered_count": filtered,
            "truncated": len(results) > maximum,
            "empty_reason": "no_results"
            if parser.no_results
            else "filtered"
            if not results
            else None,
        }


def _result_url(href: str, base: str) -> str:
    if not href.strip():
        raise ValueError("missing source URL")
    url = urljoin(base, href)
    parsed = urlsplit(url)
    if (
        parsed.hostname
        and (parsed.hostname == "duckduckgo.com" or parsed.hostname.endswith(".duckduckgo.com"))
        and parsed.path == "/l/"
    ):
        targets = parse_qs(parsed.query).get("uddg", [])
        if len(targets) != 1:
            raise ValueError("invalid search redirect")
        url = targets[0]
    url = normalize_public_source_url(url)
    hostname = urlsplit(url).hostname or ""
    if hostname == "duckduckgo.com" or hostname.endswith(".duckduckgo.com"):
        raise ValueError("search navigation is not a source")
    if hostname == "localhost" or hostname.endswith((".local", ".localhost")):
        raise ValueError("private search result")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return url
    if not address.is_global:
        raise ValueError("private search result")
    return url


class _SearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self.stack: list[tuple[str, str | None, bool]] = []
        self.nodes = 0
        self.challenge = False
        self.no_results = False

    def _count(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_SOURCE_HTML_NODES or len(self.stack) >= MAX_SOURCE_HTML_DEPTH:
            raise SkillExecutionError(
                "web_search_parse_limit", "Search response exceeded its parsing limit"
            )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._count()
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if values.get("id") == "challenge-form" or "/anomaly.js" in (values.get("action") or ""):
            self.challenge = True
        if "no-results" in classes or values.get("id") == "no-results":
            self.no_results = True
        ignored = (
            (bool(self.stack) and self.stack[-1][2])
            or tag in {"script", "style", "template", "noscript"}
            or "hidden" in values
        )
        kind = self.stack[-1][1] if self.stack else None
        if tag == "a" and "result-link" in classes and not ignored:
            self.rows.append({"url": values.get("href") or "", "title": "", "snippet": ""})
            kind = "title"
        elif "result-snippet" in classes and self.rows and not ignored:
            kind = "snippet"
        if tag not in _VOID_TAGS:
            self.stack.append((tag, kind, ignored))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        self._count()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data: str) -> None:
        self._count()
        if self.rows and self.stack and not self.stack[-1][2] and self.stack[-1][1]:
            kind = self.stack[-1][1]
            assert kind is not None
            cleaned = " ".join(data.split())
            if cleaned:
                previous = self.rows[-1][kind]
                self.rows[-1][kind] = previous + (" " if previous else "") + cleaned
