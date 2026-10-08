# ADR 0080: Model-selected QQ conversation and social events

Status: Accepted for the owner's 2026-10-07 request, limited to the two existing
groups and paired owner direct chat. Real channel acceptance is recorded separately.

## Decision

Reuse the primary Runtime's `BehaviorDecisionService`, character and chat model.
Persist default-off `qq_free_chat_enabled` in the existing versioned CAS channel
settings. It does not add senders, groups, private owner skills or memory grants.
The operator also enables the existing per-route `member` policies. Those retain
their decision/speech budgets, quiet hours and selected-original memory policy.

In member mode, mentions as well as ordinary group messages enter voluntary
behavior selection. A reply is admitted through the existing current route,
audience, participant, generation and reliable delivery fences. Account operations
retain ADR 0079. A gesture event is an observation, not a fabricated request to
poke back. Self/other-target, malformed and old notices are ignored. Stable hashes
of bounded provider notice content supply numeric message identities for existing
deduplication. Membership notices keep their separate immediate audience fences.

Owner direct chat selects respond/clarify/wait after durable ingress and, for voice,
after cancellable transcription. It includes six bounded recent history entries.
Wait terminalizes that admission as cancelled without error, generation, reply,
TTS or delivery. The pending decision is visible to synchronization and bounded to
32 simultaneous admissions; policy changes and shutdown cancel the same task.
Revalidate connection/configuration, binding, turn state and audio permission after
selection before admitting a generation. Failed selection remains silent with a
recorded normalized error, rather than assuming consent to respond.

Group voice is opt-in with free conversation and a current member policy. Parse
one record, reject mixed media, then authenticate and check the durable route and
speaker before any provider read or STT. Because NapCat filename lookup is account
global, first read that exact provider message and verify its group, speaker,
message ID and record reference. Use the existing bounded WAV/transcriber with
ephemeral observation identities; no audio is retained. Route/link/transport and
policy changes revoke preparation, including late transcripts. Only the transcript
enters the volatile discussion cache and behavior decision; a selected reply gets
its own real Conversation generation. Default text and explicit voice reply rules
remain independent of inbound voice recognition.

The Python, JSON Schema, TypeScript and Zod contracts and operator UI expose the
same flag. No database migration, provider routing or additional agent service is
introduced. New phone acceptance covers actual unmentioned text, voice and poke
separately; a model's voluntary wait is not a delivery failure.
