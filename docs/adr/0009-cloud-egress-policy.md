# ADR 0009: Explicit cloud egress policy

- Status: Accepted
- Date: 2026-08-23

## Context

Audio, transcripts, memories and character context have different privacy levels. Provider routing
must not silently send local or sensitive material to a cloud service.

## Decision

Cloud egress is denied by default unless configuration and privacy policy authorize the data class,
provider and purpose. Routes record their reason. Secrets stay in local/server configuration and
never enter clients, events or logs.

## Consequences

Cloud features require visible consent and auditable routing. Some fallbacks will be unavailable
when policy forbids egress.

## Alternatives

Cloud allowed by default; provider-specific privacy logic; redaction only after transmission.
