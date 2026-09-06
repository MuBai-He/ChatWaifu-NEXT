# ADR 0043: Bounded shared joke association and recall

Status: Accepted.

## Context

Conversations between a companion persona and the user often produce shared jokes, recurring comedic callbacks, or agreed-upon code words ("暗号" / inside jokes). Previously, ChatWaifu had no mechanism to recognize and bind these shared humorous exchanges into episodic memory.

Unconstrained or naive LLM extraction of "jokes" introduces severe risks:

1. Hallucination / unpresented model output: Extracting a shared joke from assistant responses that were cancelled, superseded, or failed network delivery creates false memory of interactions that never occurred to the user.
2. One-sided humor: An assistant or user monologue without mutual uptake is not a shared joke.
3. Fictional premise confusion: Stating that a fictional joke premise literally happened corrupts factual truthfulness.
4. Privacy and sensitivity: Canonizing credentials or direct personal identifiers as "jokes" violates user trust.
5. Inappropriate channels: Group chats or multi-principal contexts could leak or misattribute inside jokes.

## Decision

Phase 17.4A implements bounded shared joke extraction and recall under strict fail-closed constraints:

- **Zero Database Migration**: Reuses `MemoryRecord.kind = "episodic.shared_event"` with structured payload `value = {"cue": str, "kind": "shared_joke", ...}` and predicate `shared_joke.<normalized_cue>`.
- **Preceding Authoritative Delivery Evidence**: Shared joke extraction requires concrete proof that the preceding assistant turn was presented to the user:
  - Voice: `assistant.spoken_text_committed` in `runtime.playback`; local voice evidence cannot be paired with a user turn attributed to an external route.
  - External messaging: `channel.delivery_plan_completed` in `runtime.external_channels`, with the assistant and user events attributed to the same owner direct route. The rule is provider-neutral and does not key policy to a provider name.
  - Unpresented generation text, draft completions, or undelivered plans fail closed (0 shared jokes produced).
- **Strict Adjacency and Age Ceiling**: The current user turn (`user.turn_committed`) must immediately follow the presented assistant turn in the same session without intervening user turns. The exchange age between assistant presentation and user reaction must be <= 30 minutes.
- **Mutual Uptake Classification**: Memory candidates must exhibit positive mutual uptake:
  - Explicit declaration (e.g., "这是我们的梗", "约定暗号", "inside joke"): confidence threshold >= 0.80.
  - Implicit laughter / callback (e.g., "哈哈哈哈太搞笑了", "笑死我了"): confidence threshold >= 0.90.
  - Lacking mutual uptake or falling below threshold fails closed.
  - Negated or corrective statements such as "别把这个当梗" and "this is not our inside joke" fail closed.
- **Bounded Model Context**: The preceding assistant text is loaded and supplied to memory extraction only when the user text first passes the positive uptake classifier. Other user turns keep the existing single-turn extraction context.
- **Bounded Cue Grounding**: The joke cue must be 2..80 characters long, normalized for identity, and strictly grounded as an intact substring in the user or assistant text. ASCII cues require word boundaries; whitespace and punctuation are never removed from the evidence before matching.
- **Sensitive Content Guard**: Content containing credential terms or directly identifying contact/address patterns fails closed immediately. Broader content moderation remains governed by the existing product safety policy.
- **Factual Text Guard**: Runtime discards the model-authored memory sentence and context claim. It deterministically states that the user recognized the grounded phrase as a shared joke or code word without assigning the phrase to a particular speaker or character name, and retains a bounded excerpt of the actually presented assistant text as context.
- **Deterministic Predicate & Coexistence**: Predicates follow `shared_joke.<normalized_cue>`. Multiple distinct jokes coexist. Arriving candidates with an identical cue deduplicate idempotently (`ignore` proposal) without creating duplicates or superseding existing records.
- **Multi-Event Provenance**: Memory sources record both the triggering `user_turn` and the authoritative preceding `assistant_spoken` or `assistant_delivered`. The current user event remains the causation ID.
- **Protocol Port Extension**: `MemorySource.source_kind` adds `assistant_delivered` to accurately attribute external channel delivery alongside voice `assistant_spoken`, with zero schema drift.
- **Prompt Compiler Guidance**: When shared joke memories are recalled into context, PromptCompiler injects concise instructions reminding the model to use the joke naturally only when relevant, without mechanical explanations, and preserving safety and factual truth.

## Validation and limits

The vertical-slice test suite (`services/runtime/tests/test_shared_jokes.py`) validates:

- Positive voice presentation (`assistant.spoken_text_committed`) and positive external messaging delivery (`channel.delivery_plan_completed`) with multi-event provenance and channel attribution.
- Fail-closed rejection of generated-but-undelivered assistant text, one-sided assistant proposals, negated/interrogative uptake, ungrounded or cross-word cues, malformed/oversized cues, sensitive topics, stale exchanges (>30 min), intervening user turns, mixed local-voice/external-channel evidence, and mismatched external routes.
- Uptake classification thresholds (explicit >= 0.80, implicit >= 0.90).
- Coexistence of distinct jokes and idempotent deduplication of same-cue jokes.
- Forget and tombstone handling allowing subsequent clean re-formation.
- SQLite persistence across engine restarts and character namespace isolation.
- PromptCompiler guidance injection and token budget compliance.
- Memory projection queue cancellation upon scope reset.

Excluded from Phase 17.4A:

- Group and multi-principal chats.
- Desktop text chat without presentation ACK.
- Sticker binding and adaptive sticker ranking.
- Provider-specific acceptance, including QQ.
- Animated media and video planes.
