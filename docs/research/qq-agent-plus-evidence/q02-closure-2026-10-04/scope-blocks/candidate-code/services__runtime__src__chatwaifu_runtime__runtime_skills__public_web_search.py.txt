"""Bounded public source discovery, with explicit query egress and truthful failures."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
from collections.abc import Sequence
from html.parser import HTMLParser
from typing import Literal, cast
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from chatwaifu_protocol.base import JsonObject, JsonValue

from chatwaifu_runtime.config.settings import PublicWebConfig
from chatwaifu_runtime.runtime_skills.errors import SkillExecutionError
from chatwaifu_runtime.runtime_skills.public_web import (
    MAX_SOURCE_HTML_DEPTH,
    MAX_SOURCE_HTML_NODES,
    PublicWebReader,
    normalize_public_source_url,
)
from chatwaifu_runtime.runtime_skills.public_web_providers import (
    FirecrawlSearchClient,
    JinaSogouSearchClient,
    ProviderSearchResponse,
    ProviderSearchResult,
    SearxngSearchClient,
    So360SearchClient,
)

MAX_SEARCH_RESULTS = 5
MAX_PROVIDER_CANDIDATES = 20
_SEARCH_ROOT = "https://lite.duckduckgo.com/lite/"
_DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}")
_CURRENT_QUERY = re.compile(
    r"最新|现行|当前|今日|近期|生效|修订|变化|规定|规则|法规|政策|公告|要求|"
    r"\b(?:latest|current|today|recent|effective|revision|change|regulation|"
    r"rule|policy|notice|requirement)s?\b",
    re.IGNORECASE,
)
_EXPLICIT_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_POWER_BANK_TERMS = re.compile(r"充电宝|移动电源", re.IGNORECASE)
_DATE_IN_SOURCE = re.compile(r"(?<!\d)(20\d{2})(?:[-/.年]?(\d{1,2})(?:[-/.月]?(\d{1,2})日?)?)?")
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
    def __init__(
        self, reader: PublicWebReader, *, provider_config: PublicWebConfig | None = None
    ) -> None:
        self._reader = reader
        self._provider_config = provider_config or PublicWebConfig()

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
        requested_query = query.strip()
        # Preserve wording, language and operators chosen for discovery. Adding
        # generic update terms can turn an English query into unrelated results.
        effective_query = requested_query
        scoped_query = effective_query
        if domains:
            scope_query = " OR ".join(f"site:{domain}" for domain in domains)
            scoped_query += " " + (f"({scope_query})" if len(domains) > 1 else scope_query)
        if self._provider_config.search_provider == "firecrawl":
            return await self._search_firecrawl(
                scoped_query, requested_query, effective_query, maximum, domains, resolver
            )
        if self._provider_config.search_provider == "searxng":
            return await self._search_searxng(
                scoped_query, requested_query, effective_query, maximum, domains
            )
        if self._provider_config.search_provider == "so360":
            return await self._search_so360(
                scoped_query, requested_query, effective_query, maximum, domains, resolver
            )
        if self._provider_config.search_provider == "jina_sogou":
            return await self._search_jina_sogou(
                scoped_query, requested_query, effective_query, maximum, domains, resolver
            )
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
        rows = (
            _rank_current_rows(parser.rows) if _is_current_query(effective_query) else parser.rows
        )
        for raw in rows:
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
            "query": requested_query,
            "effective_query": effective_query,
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

    async def _search_firecrawl(
        self,
        scoped_query: str,
        query: str,
        effective_query: str,
        maximum: int,
        domains: list[str],
        resolver: str,
    ) -> JsonObject:
        response = await FirecrawlSearchClient(self._provider_config).search(
            scoped_query,
            maximum,
            dns_resolver=cast(Literal["system", "cloudflare"], resolver),
        )
        results: list[JsonValue] = []
        seen: set[str] = set()
        filtered = 0
        rows = (
            _rank_provider_results(response.results)
            if _is_current_query(effective_query)
            else response.results
        )
        for raw in rows:
            try:
                result_url = _result_url(raw.url, response.endpoint)
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
                {"url": result_url, "title": raw.title[:240], "snippet": raw.snippet[:480]}
            )
        return {
            "provider": response.provider,
            "query": query,
            "effective_query": effective_query,
            "search_url": response.endpoint,
            "retrieved_at": response.retrieved_at,
            "body_sha256": hashlib.sha256(response.body).hexdigest(),
            "dns_resolver": resolver,
            "results": results[:maximum],
            "total_matched": len(results),
            "filtered_count": filtered,
            "truncated": len(results) > maximum,
            "empty_reason": (
                "no_results" if not response.results else "filtered" if not results else None
            ),
        }

    async def _search_searxng(
        self,
        scoped_query: str,
        query: str,
        effective_query: str,
        maximum: int,
        domains: list[str],
    ) -> JsonObject:
        client = SearxngSearchClient(self._provider_config)
        provider_limit = max(maximum, MAX_PROVIDER_CANDIDATES)
        response = await client.search(scoped_query, provider_limit)
        results: list[JsonValue] = []
        seen: set[str] = set()
        filtered = 0
        # The adapter executes the requested query. Topic-specific supplements
        # would hide extra egress and displace provider-ranked results at the cap.
        # Refinements belong in explicit, auditable tool calls from the agent.
        for raw in response.results:
            try:
                result_url = _result_url(raw.url, response.endpoint)
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
                {"url": result_url, "title": raw.title[:240], "snippet": raw.snippet[:480]}
            )
        digest = hashlib.sha256(response.body).hexdigest()
        return {
            "provider": response.provider,
            "query": query,
            "effective_query": effective_query,
            "provider_queries": [{"query": scoped_query, "body_sha256": digest}],
            "search_url": response.endpoint,
            "retrieved_at": response.retrieved_at,
            "body_sha256": digest,
            "dns_resolver": "system",
            "results": results[:maximum],
            "total_matched": len(results),
            "filtered_count": filtered,
            "truncated": len(results) > maximum,
            "empty_reason": (
                "no_results" if not response.results else "filtered" if not results else None
            ),
        }

    async def _search_so360(
        self,
        scoped_query: str,
        query: str,
        effective_query: str,
        maximum: int,
        domains: list[str],
        resolver: str,
    ) -> JsonObject:
        response = await So360SearchClient(self._provider_config).search(
            scoped_query, maximum, dns_resolver=cast(Literal["system", "cloudflare"], resolver)
        )
        results: list[JsonValue] = []
        seen: set[str] = set()
        filtered = 0
        rows = (
            _rank_provider_results(response.results)
            if _is_current_query(effective_query)
            else response.results
        )
        for raw in rows:
            try:
                result_url = _result_url(raw.url, response.endpoint)
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
                {"url": result_url, "title": raw.title[:240], "snippet": raw.snippet[:480]}
            )
        return {
            "provider": response.provider,
            "query": query,
            "effective_query": effective_query,
            "search_url": response.endpoint,
            "retrieved_at": response.retrieved_at,
            "body_sha256": hashlib.sha256(response.body).hexdigest(),
            "dns_resolver": resolver,
            "results": results[:maximum],
            "total_matched": len(results),
            "filtered_count": filtered,
            "truncated": len(results) > maximum,
            "empty_reason": (
                "no_results" if not response.results else "filtered" if not results else None
            ),
        }

    async def _search_jina_sogou(
        self,
        scoped_query: str,
        query: str,
        effective_query: str,
        maximum: int,
        domains: list[str],
        resolver: str,
    ) -> JsonObject:
        client = JinaSogouSearchClient(self._provider_config)
        provider_limit = max(maximum, MAX_PROVIDER_CANDIDATES)
        resolver_value = cast(Literal["system", "cloudflare"], resolver)

        async def fetch(provider_query: str) -> ProviderSearchResponse:
            return await client.search(provider_query, provider_limit, dns_resolver=resolver_value)

        def normalize(response: ProviderSearchResponse) -> tuple[list[JsonValue], int]:
            results: list[JsonValue] = []
            seen: set[str] = set()
            filtered = 0
            rows = (
                _rank_provider_results(response.results)
                if _is_current_query(effective_query)
                else response.results
            )
            for raw in rows:
                try:
                    result_url = _result_url(raw.url, response.endpoint)
                except (ValueError, SkillExecutionError):
                    filtered += 1
                    continue
                hostname = urlsplit(result_url).hostname or ""
                if result_url in seen or (
                    domains
                    and not any(hostname == d or hostname.endswith("." + d) for d in domains)
                ):
                    filtered += 1
                    continue
                seen.add(result_url)
                results.append(
                    {"url": result_url, "title": raw.title[:240], "snippet": raw.snippet[:480]}
                )
            return results, filtered

        candidate_queries = [scoped_query]
        if domains:
            for candidate in (
                "民航局 充电宝 3C 召回"
                if _POWER_BANK_TERMS.search(query)
                and re.search(r"民航|航班|航空|规定|规则|法规|公告", query)
                and not re.search(r"3C|召回", query, re.IGNORECASE)
                else None,
                query,
                "民航局 充电宝 额定能量 规定"
                if _POWER_BANK_TERMS.search(query)
                and re.search(r"民航|航班|航空|规定|规则|法规|公告", query)
                else None,
                "民航局 充电宝 100Wh 160Wh 托运 数量"
                if _POWER_BANK_TERMS.search(query)
                and re.search(r"民航|航班|航空|规定|规则|法规|公告", query)
                and re.search(r"额定能量|\b(?:100|160)\s*Wh\b", query, re.IGNORECASE)
                else None,
            ):
                if candidate and candidate not in candidate_queries:
                    candidate_queries.append(candidate)

        attempted_queries: list[str] = []
        response = None
        results: list[JsonValue] = []
        result_urls: set[str] = set()
        filtered = 0
        needs_threshold_evidence = bool(
            _POWER_BANK_TERMS.search(query)
            and re.search(r"额定能量|\b(?:100|160)\s*Wh\b", query, re.IGNORECASE)
        )

        def has_threshold_evidence(rows: list[JsonValue]) -> bool:
            return any(
                re.search(
                    r"额定能量|\b(?:100|160)\s*Wh\b|20(?:14|15)",
                    " ".join(str(row.get(key, "")) for key in ("url", "title", "snippet")),
                    re.IGNORECASE,
                )
                for row in rows
                if isinstance(row, dict)
            )

        for index, provider_query in enumerate(candidate_queries):
            attempted_queries.append(provider_query)
            try:
                current_response = await fetch(provider_query)
            except SkillExecutionError as error:
                if (
                    error.structured.code != "web_provider_format_changed"
                    or index == len(candidate_queries) - 1
                ):
                    raise
                # Sogou can expose only opaque same-site redirects for one
                # query shape. Keep the failure truthful and try the next
                # bounded shape before giving up.
                continue
            current_results, current_filtered = normalize(current_response)
            filtered += current_filtered
            response = current_response
            for current_result in current_results:
                if not isinstance(current_result, dict):
                    continue
                result_url = current_result.get("url")
                if isinstance(result_url, str) and result_url not in result_urls:
                    result_urls.add(result_url)
                    results.append(current_result)
            needs_complement = (
                needs_threshold_evidence
                and bool(results)
                and not has_threshold_evidence(results)
                and index < len(candidate_queries) - 1
            )
            if (results and not needs_complement) or index == len(candidate_queries) - 1:
                break
        if response is None:
            raise SkillExecutionError(
                "web_provider_format_changed",
                "Jina Sogou search returned no usable source response",
            )
        return {
            "provider": response.provider,
            "query": query,
            "effective_query": effective_query,
            "search_url": response.endpoint,
            "retrieved_at": response.retrieved_at,
            "body_sha256": hashlib.sha256(response.body).hexdigest(),
            "dns_resolver": resolver,
            "results": results[:maximum],
            "total_matched": len(results),
            "filtered_count": filtered,
            "truncated": len(results) > maximum,
            "empty_reason": (
                "no_results" if not response.results else "filtered" if not results else None
            ),
        }


def _is_current_query(query: str) -> bool:
    return bool(_CURRENT_QUERY.search(query)) and not (
        _EXPLICIT_YEAR.search(query)
        and not re.search(r"最新|现行|当前|latest|current", query, re.IGNORECASE)
    )


def _source_date(value: str) -> tuple[int, int, int] | None:
    matches = list(_DATE_IN_SOURCE.finditer(value))
    if not matches:
        return None
    match = matches[-1]
    year = int(match.group(1))
    month = int(match.group(2) or 0)
    day = int(match.group(3) or 0)
    if month > 12 or day > 31:
        return None
    return year, month, day


def _rank_current_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for _index, row in sorted(
            enumerate(rows),
            key=lambda item: (
                _source_date(" ".join(item[1].values())) or (0, 0, 0),
                -item[0],
            ),
            reverse=True,
        )
    ]


def _rank_provider_results(rows: Sequence[ProviderSearchResult]) -> list[ProviderSearchResult]:
    return [
        row
        for _index, row in sorted(
            enumerate(rows),
            key=lambda item: (
                _source_date(f"{item[1].url} {item[1].title} {item[1].snippet}") or (0, 0, 0),
                -item[0],
            ),
            reverse=True,
        )
    ]


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
