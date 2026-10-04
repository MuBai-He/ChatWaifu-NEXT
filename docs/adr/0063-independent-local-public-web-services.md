# ADR 0063: Independent local public-web services

- Status: Accepted
- Date: 2026-10-03

## Context

Q02 source discovery used DuckDuckGo Lite directly and encountered provider
challenge pages. The Runtime already has bounded built-in, Jina and Firecrawl
adapters, but a browser crawler or a metasearch server should not become a
Runtime dependency. The user also needs a reproducible local deployment without
putting browser processes, search-engine plugins or their dependency trees into
the CW2 server process.

## Decision

Run SearXNG and Crawl4AI as independent local services managed by Docker
Compose. SearXNG owns search discovery and exposes only its JSON search response
to Runtime. Crawl4AI owns browser-backed page extraction and exposes its
authenticated `/md` endpoint for HTML and `/crawl` with a fixed PDF strategy
for `.pdf` sources to Runtime. The Runtime contains small typed HTTP
adapters, not either service's SDK or browser code.

The adapters accept only loopback service origins. The host ports are configurable
so an existing local service can keep its port without exposing either provider
on the LAN. SearXNG has no Runtime-side
credential; Crawl4AI uses a bearer token stored as a secret configuration value.
Before a Crawl4AI request, Runtime still validates the target URL as an HTTPS
public source. Search result URLs remain untrusted until the normal `web.read`
permission and source validation path reads them.

The providers are opt-in configuration. The existing DuckDuckGo Lite plus built-
in Reader defaults remain unchanged until the local services have passed health,
provider-parser, source-receipt and real Q02 batch checks. A service outage is a
truthful provider error, not an empty search result or a silent fallback.

## Consequences and validation

The Runtime process stays small and independent from Chromium and SearXNG's
engine plugins. Service upgrades and restarts are isolated, while the loopback
ports keep the provider surface off the network. The Compose file uses the
official SearXNG image and Crawl4AI 0.9.3 image, binds both ports to host
loopback, exposes SearXNG JSON, and requires a nonempty Crawl4AI token.

Unit tests cover SearXNG JSON normalization, result filtering, Crawl4AI bearer
authentication, Markdown extraction, missing-token failure and loopback endpoint
validation. Compose health and a real source batch remain deployment checks and
are not implied by the unit suite.

The PDF addition follows a real 0.9.2 failure: `/md` used a browser/HTML strategy
on the Raft paper and returned HTTP 500, and the deployed image lacked `pypdf`.
The upstream [0.9.3 changelog](https://github.com/unclecode/crawl4ai/blob/main/CHANGELOG.md)
documents Docker PDF support and the required PDF redirect/security fixes. Runtime
sends one fixed `PDFContentScrapingStrategy` request for HTTPS `.pdf` URL paths,
with cache bypass and no image/file/hook options. The companion owns PDF parsing
and per-hop URL checks. HTTP 200, an empty result, or a failed extraction cannot
be a successful source receipt. Extensionless PDFs and OCR are not covered.
Existing 0.9.2 deployments need separate upgrade validation; changing the checked-in
template does not change a running server or establish Q02 acceptance.

## Rollback

Leave the providers disabled and remove the Compose stack. Existing built-in,
Jina and Firecrawl configuration and source receipts remain compatible; no
database migration is required.
