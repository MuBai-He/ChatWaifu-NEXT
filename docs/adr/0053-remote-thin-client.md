# ADR 0053: Explicit remote thin clients and server-owned voice

- Status: Accepted
- Date: 2026-09-21

## Context

An always-on server must own character state, channels, inference and voice processing. A laptop must be able to enter an endpoint and use only the frontend. The previous native host eagerly booted local services and accepted only loopback bootstrap URLs; starting a source server alone did not satisfy this deployment model.

## Decision

- Select local or remote before mounting Runtime-dependent product surfaces. No native startup hook implicitly starts the supervisor. Native start, restart (including tray) and Worker installation require an explicit local choice under the same lock as connection changes.
- Persist desktop connection selection separately from Runtime bootstrap metadata. Treat its access token as secret: no Debug, no event payload, Unix mode 0600. Browser credentials are session-only; only the endpoint persists in localStorage. This first slice is a single-owner management client, not multi-tenant authorization.
- Require HTTPS remote origins, allowing HTTP only for loopback tunnels. Do not weaken loopback validation of native sidecar bootstrap. Connection changes tear down product surfaces, stop the local supervisor and reload all desktop windows; session IDs are namespaced by endpoint.
- Keep Runtime loopback-bound behind a TLS proxy; use its existing Host/Origin allowlists, Bearer HTTP authentication and scoped WebSocket tickets. Protect WAV asset downloads with the same HTTP authentication and use abortable, revocable Blob playback.
- Keep inference, STT/TTS and cloud-provider credentials on the server. The client only captures and plays media and renders semantic avatar cues. A versioned authenticated, non-cacheable client-configuration endpoint supplies ICE servers/policy. Provider-specific ICE objects are constructed only inside the Pipecat adapter. General settings output excludes TURN credentials.
- STUN/TURN deployment is explicit. Source server service management and thin-client development do not install model workers or a TURN server. Reuse existing voice generation, interruption, receipt and reconnection behavior.

## Consequences

Static Web hosting is the lightest client. Native source builds require Node/Rust but not Python; running an already-built remote native app needs no development environment. Server installation packages remain deferred. Public TLS, real NAT/TURN connectivity, actual STT/TTS and physical audio still require acceptance on the target server and laptop; local process or mock checks do not substitute for that acceptance.
