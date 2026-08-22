---
name: memory-system
description: Design, implement, debug, or evaluate ChatWaifu working and long-term memory, including extraction, policy, persistence, deduplication, contradiction handling, retrieval, ranking, context injection, forgetting, provenance, and privacy.
---

# Memory System

Memory is a cognitive subsystem, not a transcript archive. Keep working, semantic,
episodic, relationship, prospective, procedural, and character-canon concepts
distinct where their policies differ.

Durable writes follow candidate extraction -> classification -> policy -> related
memory search -> deduplication/conflict resolution -> persistence. Extraction
confidence is not permission to persist. Preserve source event IDs, confidence,
validity, sensitivity, and status so corrections and forgetting are explainable.

Retrieval follows query construction -> hybrid candidate search -> ranking -> policy
filtering -> context budgeting -> injection. Do not dump vector top-K results into a
prompt. Agent code depends on retriever, writer, and policy interfaces, never SQL.

Test relevance precision/recall, duplicates, corrections, unrelated queries,
context-budget pressure, deletion, and privacy rejection. Log IDs and scores needed
for debugging without logging sensitive payloads at normal levels.
