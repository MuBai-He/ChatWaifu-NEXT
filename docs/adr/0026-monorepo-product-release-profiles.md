# ADR 0026: One main branch with independent Web and Desktop product profiles

- Status: Accepted
- Date: 2026-08-30

## Context

ChatWaifu NEXT has one character Runtime and one React feature set, but two user-facing products:
the browser visual-novel application and the native desktop companion. Previously both were static
imports behind one Vite entry and one `dist` directory. A route selected the visible page, but the
artifacts, versions, CI scope, and release lifecycle were not actually separate.

Long-lived product branches or duplicated applications would make fixes to conversation,
realtime media, memory, settings, and avatar behavior drift. Keeping one mixed browser bundle would
make independent release and dependency review impossible.

## Decision

`main` is the only product truth source. Web and Desktop remain in one monorepo and share domain,
protocol, Runtime client, voice, memory, settings, and avatar modules, but have independent
compile-time product profiles:

- `web` owns the browser conversation and Avatar Lab surfaces and emits `apps/web/dist/web`.
- `desktop` owns the desktop-pet and control-center surfaces and emits
  `apps/web/dist/desktop`; Tauri consumes only this directory.
- Each build emits `chatwaifu-product.json` with its product, version, surfaces, and sanitized module
  list. The release gate rejects a product graph containing the other product's entry modules.
- `release/products.json` is the canonical release-train manifest. Web and Desktop versions and tag
  prefixes are independent. Component protocol and Runtime versions do not have to equal a product
  version.
- Releases are cut from commits on `main` using `web-vX.Y.Z` or `desktop-vX.Y.Z`; product branches
  are not release channels.
- Ordinary CI uses path ownership to avoid unrelated product work. Tag workflows do not use path
  filters because tag pushes do not provide a reliable changed-file boundary.

The Tauri host stays thin per ADR 0005. This decision does not move character or Runtime behavior
into Rust and does not duplicate React feature implementations.

## Failure behavior and release gates

A mismatched tag, mirrored version, product mode, output directory, or mixed product module graph
fails before publication. Web artifacts can be published independently once their build and tests
pass.

The current Desktop host is still an unsigned `--no-bundle` developer artifact. A
`desktop-v*` production release additionally requires a frozen Runtime sidecar, resource assembly,
Tauri installer bundling, target-machine smoke tests, and the Live2D/model license gate. CI must not
label a naked host executable or Runtime wheel as an installable Desktop release.

## Consequences

Browser releases no longer carry native desktop UI or Tauri surface routing. Desktop releases no
longer carry the visual-novel Web page or Avatar Lab. Shared feature changes can still affect both
products, so their path filters intentionally overlap on shared frontend and protocol paths.

Developers must choose an explicit profile for release work. The compatibility commands `pnpm dev`
and `pnpm build` continue to mean Web; Tauri always invokes the Desktop profile itself.

## Migration

The former `main.tsx`, mixed `App.tsx`, `App.css`, and cross-product route resolver are removed.
Existing browser development commands continue to open the Web product. Desktop development is
routed through `dev:desktop`, and packaged Tauri builds load `dist/desktop/index.html`.

## Alternatives

Maintain separate repositories; keep a permanent desktop-pet branch; retain a single mixed bundle
and distinguish only by URL; duplicate the entire React application for each product.
