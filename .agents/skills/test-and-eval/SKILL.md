---
name: test-and-eval
description: Design, implement, run, or repair ChatWaifu unit, integration, protocol, realtime cancellation, memory, Runtime Skill, conversation behavior, latency regression, evaluation, or release-gate checks.
---

# Test And Eval

Keep deterministic software correctness and probabilistic agent quality distinct;
neither replaces the other. Unit-test domain rules, integrate real boundaries with
controlled adapters, scenario-test realtime cancellation, and evaluate model
behavior with explicit fixtures and metrics.

Fake streaming providers must support chunks, configurable delay, errors, hanging,
cancellation, and late output. Realtime scenarios cover normal turn, barge-in during
LLM and TTS, disconnect, stale generation chunks, bounded backpressure, and provider
failure. Protocol tests cover round-trip serialization, required fields,
discriminators, and version behavior.

Memory evals measure precision, recall, corrections, duplicates, negative retrieval,
privacy, and context budget. Runtime Skill evals verify selection, arguments,
permissions, confirmations, and truthful failure handling. Track component p50/p95
latency rather than only one average.

Every reproducible fix should add a regression test when practical. Do not weaken
assertions or hide failures to satisfy a release gate.
