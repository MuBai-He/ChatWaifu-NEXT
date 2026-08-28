# ADR 0018: Complete MCP host, loopback server, and enforceable plugin sandbox

- Status: Accepted
- Date: 2026-08-29

## Context

ADR 0013 proved permissioned MCP tool execution with one Python stdio example, but it deliberately
excluded remote transports, resource and prompt primitives, arbitrary executable entrypoints,
strong process isolation, and a ChatWaifu-owned MCP server. Extending the handwritten JSON-RPC
subset independently would create protocol drift and duplicate lifecycle, cancellation, and
transport-security logic maintained by the MCP project.

MCP remains an integration protocol. Runtime Skills, the Permission Broker, and the audit log stay
the product security boundary; a remote server's tool annotations are descriptive input and are
not trusted authorization decisions.

## Decision

Runtime becomes an MCP Host through the official Python MCP SDK v2. It supports stdio, Streamable
HTTP, and legacy SSE connection profiles. Profiles and non-secret metadata are persisted in SQLite;
bearer credentials use the existing write-only local-secret pattern. Remote URLs are denied by
default unless they resolve to loopback. Non-loopback connections require an explicit egress opt-in,
reject credential-bearing redirects, and are revalidated at connection time to limit SSRF and DNS
rebinding exposure.

The Host discovers and caches tools, resources, resource templates, and prompts after protocol
capability negotiation. Tool calls are projected into namespaced Runtime Skill capabilities and
continue through schema validation, permissions, per-invocation confirmation, timeout,
cancellation, normalized errors, and audit. Resource reads and prompt rendering are explicit
read-only operations; their content is never silently inserted into character context or memory.
Legacy SSE is compatibility-only; new profiles should use Streamable HTTP.

Stdio profiles accept an explicit executable and argument vector rather than only Python. Local
untrusted processes default to a required OS sandbox. Supported backends are macOS Seatbelt
(`sandbox-exec`), Linux bubblewrap, or an OCI runtime with a read-only root, dropped capabilities,
bounded resources, and network disabled unless policy grants it. A required sandbox fails closed
when no enforcing backend is available. A trusted profile may explicitly choose preferred or
disabled isolation, but the UI and API always report the actual backend and enforcement state.

Runtime also mounts a standard Streamable HTTP MCP server at `/mcp`. It binds with the Runtime to
loopback, validates Host and Origin through the SDK transport-security layer, and uses Runtime
authentication when configured. It publishes only policy-filtered tools and explicitly public
resources/prompts. Calls re-enter Runtime Skill orchestration; the server never invokes an adapter
directly. Realtime PCM, internal events, private memory, provider credentials, local paths, and
arbitrary plugin UI are not exposed.

The MCP SDK owns wire lifecycle and transports. ChatWaifu-owned protocol models describe connection
profiles, discovered capabilities, policy, health, and API snapshots. Cancellation closes the SDK
request/transport and also propagates through the active Skill Run. All discovery and execution
requests have bounded timeouts and response-size limits.

## Consequences

Local and remote MCP servers share one policy and observability surface, while external MCP clients
can consume a deliberately small ChatWaifu capability surface. The official SDK adds a Runtime
dependency, but prevents a growing partial protocol implementation. Existing `plugin.json` and
`chatwaifu.yaml` packages migrate without changing their Skill identity; their transport and
security fields receive safe defaults.

Strong isolation availability is platform-dependent. Windows without an OCI runtime has no approved
backend in this decision and therefore cannot execute an untrusted `sandbox_mode=required` stdio
profile; remote MCP and trusted explicit profiles remain usable. Adding an AppContainer launcher is
a compatible follow-up, but soft process cleanup must never be labeled an OS sandbox.

MCP Apps, sampling, elicitation, filesystem roots, automatic prompt injection, and transport of
realtime media remain out of scope. Adding any of them requires a separate threat-model review.

## Alternatives

Continue expanding the handwritten JSON-RPC subset; call remote MCP directly from React; trust
server annotations as permissions; allow arbitrary commands with cleaned environment only; expose
all Runtime state; require a container runtime for every trusted local plugin.
