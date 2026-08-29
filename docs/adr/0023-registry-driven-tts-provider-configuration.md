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
name, strict configuration model and default, adapter factory, secret fallback policy, and typed UI
field descriptors. Runtime composition, configuration seeding, discovery, validation, provider
construction, and the settings catalog all consume that registry.

The HTTP boundary is provider-neutral:

- `GET /v1/tts/configurations` discovers registered configuration and UI schemas.
- `GET` and `PUT /v1/tts/configurations/{provider_id}` read and partially update one provider.
- `POST /v1/tts/configurations/{provider_id}/test` runs the provider's normalized probe.

Provider configuration models remain strict. Only the generic request envelope accepts dynamic
top-level fields, then validates them against the path-selected registration. API keys are handled
as write-only secrets and never enter the public configuration document.

Configuration mutation, secret-journal recovery, compensation, and in-memory reload run behind one
service-level async writer lock. Concurrent updates therefore cannot compensate each other's secret
or publish a configuration assembled from different committed mutations.

## Consequences

Adding another configurable TTS adapter no longer adds routes or frontend provider branches. The
registry is a deliberate in-process extension point rather than arbitrary dynamic code loading;
shipping a new adapter still requires reviewed Runtime code and tests.
