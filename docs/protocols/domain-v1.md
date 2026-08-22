# Domain protocol v1

The Python Pydantic package is the source of truth. JSON Schema and TypeScript declarations are
generated artifacts; TypeScript runtime parsers are hand-reviewed trust boundaries.

## Compatibility

- Wire names use lowercase dot namespaces.
- `1.x` readers ignore unknown optional fields.
- Unknown major versions, message types and invalid payloads are rejected.
- Facts use `EventEnvelope`; requested actions use `CommandEnvelope`.
- High-frequency media bodies stay outside the event store.

The v1 audio control header is bounded, validated JSON encoded as UTF-8 bytes. A future transport may
place those bytes before a binary media body, but framing and transport are intentionally deferred.

Run `make generate-protocol`, `make test-contract` and `make check-generated` after every contract
change.
