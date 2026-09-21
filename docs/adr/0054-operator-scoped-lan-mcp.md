# ADR 0054: Operator-scoped LAN MCP origins

- Status: Accepted
- Date: 2026-09-22

## Context

ADR 0018's network protections reject private MCP targets. Home Assistant commonly runs on a private LAN, so an explicit remote connection cannot currently reach it even when the network route exists.

## Decision

Add an empty-by-default `security.mcp_private_origins` tuple to operator configuration. Each entry is an HTTP(S) origin containing a literal RFC1918 IPv4 address and optional port. No DNS names, wildcard ranges, userinfo, paths, query strings, metadata or link-local addresses are admitted. The connection still requires `allow_remote=true`.

Only the composition root supplies this policy to the shared MCP client transport used for discovery and execution. Connection CRUD cannot extend it. Match scheme, canonical IP and effective port; preserve pinned network transport, TLS certificate checks, redirect restrictions, payload limits and per-invocation authorization. Local stdio sandbox rules are unchanged.

## Consequences

The operator can grant access to one selected HA origin without permitting all private networks. Policy changes take effect on Runtime restart. This neither establishes a VPN route nor grants HA permissions. Real HA discovery and physical device control remain separate acceptance steps. Calendar, reminders and general task scheduling are independent features described in the personal assistant plan.
