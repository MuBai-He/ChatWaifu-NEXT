# ADR 0023: Configurable TTS providers use one Runtime registration

- Status: Accepted
- Date: 2026-08-29

## Context

Adding a cloud TTS provider required parallel edits to provider composition, database seeding,
provider-specific HTTP routes, frontend request types, and a hand-built settings panel. The copies
had already diverged between Qwen3-TTS and CosyVoice and made a third provider disproportionately
risky.

## Decision

Each configurable TTS adapter has one `TtsProviderRegistration` containing its stable ID, display
name, strict `BaseModel` configuration type and default, adapter factory, credential policy,
presentation metadata, schema version, and typed UI field descriptors. Runtime composition,
configuration seeding, discovery, validation, provider construction, and the settings catalog all
consume the registrations owned by the same `TtsConfigurationService`; provider construction does
not reread a process-global registry.

Provider configuration is persisted in `tts_provider_configs` as a versioned JSON object. The
provider's strict model remains the validation boundary, so providers may define fields unrelated
to the original Aliyun row shape. `updated_at` and derived credential-presence fields stay outside
the JSON document. On first startup after this decision, matching rows from the legacy
`tts_cloud_configs` table are imported into the versioned store. While legacy readers remain, models
that contain the complete legacy field shape are also mirrored back to that table; provider IDs are
not used to decide compatibility.

The current credential contract intentionally supports exactly one write-only `api_key`. A
registration may declare an `api_key` secret UI field and optional fallback to another registered
API-key provider. Registration validation rejects every other secret field, a missing
`api_key_configured` model field, or an invalid fallback. This is a deliberate honest constraint,
not an assertion that arbitrary secret descriptors are already implemented. Supporting another
credential shape requires extending the request, storage, redaction, and registration contract
together.

The HTTP boundary is provider-neutral:

- `GET /v1/tts/configurations` discovers registered configuration and UI schemas.
- `GET` and `PUT /v1/tts/configurations/{provider_id}` read and partially update one provider.
- `POST /v1/tts/configurations/{provider_id}/test` runs the provider's normalized probe.

Discovery also returns the provider configuration schema version, explicit credential descriptor,
and optional grouping/variant presentation metadata. Clients group and label choices from this
metadata rather than branching on provider IDs.

Provider configuration models remain strict. Only the generic request envelope accepts dynamic
top-level fields, then validates them against the path-selected registration. API keys are handled
as write-only secrets and never enter the public configuration document.

Configuration mutation, secret-journal recovery, compensation, and in-memory reload run behind one
service-level async writer lock. Concurrent updates therefore cannot compensate each other's secret
or publish a configuration assembled from different committed mutations.

## Consequences

Adding another configurable TTS adapter no longer adds persistence columns, routes, or frontend
provider branches. Contract tests use a third provider whose configuration has neither Aliyun
fields nor an API key to keep this extension path honest across persistence, factory composition,
and HTTP discovery.

The registry is a deliberate in-process extension point rather than arbitrary dynamic code loading;
shipping a new adapter still requires reviewed Runtime code and tests. A future configuration schema
change must either keep the same version and remain backward-compatible or add an explicit migration
before changing `configuration_schema_version`; unknown stored versions fail closed.
