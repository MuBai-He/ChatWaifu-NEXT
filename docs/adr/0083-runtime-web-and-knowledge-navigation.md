# ADR 0083: Runtime web shell and settings entry for the knowledge map

- Status: Accepted
- Date: 2026-10-14
- Extends: ADRs 0053 and 0082

## Decision

Keep the knowledge map as a separate Web product surface at `/knowledge`, with
its existing bounded renderer, owner-session checks and source/correction UI.
Settings adds a searchable "知识图谱" section under "记忆与资料". Web navigation
stays in the same tab and origin, retaining the current session credential. The
map returns to `/settings/knowledge`. Desktop settings exposes a credential-free
browser URL; the browser authenticates in its own session.

The frontend reuses the deployed knowledge API contracts and does not create a
second memory, graph store, extraction pipeline or model provider. A Runtime
without those APIs reports the service as unavailable rather than inventing an
empty successful graph. Native embedded viewer cookies retain their existing
scoped API behavior; the page does not grant them operator privileges.

An optional operator-only `runtime.web_client_dir` lets a source Runtime serve a
build with Vite base `/web-client/` alongside its private APIs. Explicit product
documents (root, settings, knowledge and Avatar Lab) and static resources support
public GET/HEAD after Host/Origin checks. The public shell performs password or
token authentication before mounting data consumers. API routes, POST requests,
MCP and private assets retain existing authentication. Default Runtime behavior
is unchanged when the directory is absent. The host path is omitted from public
settings.

Documents use no-store and a same-origin CSP. Static files stay within the
operator-selected build directory. This supplies a usable root/login/settings
entry on the existing TLS listener, without placing a long-lived credential into
HTML, URLs, public environment variables or an authentication cookie.

## Delivery and compatibility

Deploy the reviewed password/web changes as an overlay on the current production
source, preserving its knowledge and information services and existing viewer
scope checks. The standalone historical `/knowledge-assets` mount can remain for
already-open clients. Publish both the LAN Web build and the prefixed TLS build;
only the explicit web-directory service environment is added. Existing operator
tokens, model routes, channel grants and stored data are retained.

This UI integration does not migrate or re-index knowledge. The repository's
baseline may not contain the previously deployed knowledge backend; running the
new UI locally against that baseline should show the unavailable-service message.
Deployment verifies the existing production endpoints separately.
