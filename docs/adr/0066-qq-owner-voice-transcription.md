# ADR 0066: Bounded owner-private QQ voice transcription

- Status: Accepted
- Date: 2026-10-03
- Extends: ADRs 0064 and 0065.
- Validation state: Implementation in progress; real QQ voice transcription pending.

## Context

Phase D includes incoming voice, proactive delivery and group participation.
Incoming owner voice can reuse the existing authenticated local STT worker and
external Conversation path. Group privacy and proactive delivery require their
own permission and participant design and are not enabled by this decision.

Submitting `[语音]` before transcription would commit a false user utterance to
history and memory. Waiting for STT inside the QQ receive loop would prevent a
new message from cancelling the old transcription. Native CPU inference cannot
be forcibly interrupted, so cancelling an HTTP request alone does not release
the underlying worker's capacity.

## Decision

Accept one structured `record` from the paired owner in private chat, optionally
with a reply reference. Reject mixed text/images or multiple records. Pairing
remains exact text-only. The existing text envelope and an immutable audio
sidecar remain internal; only the descriptor digest and input kind are durable.

Authenticate, admit and deduplicate before any download or STT. Register bounded
background preprocessing and return the admission receipt immediately. Allocate
session, turn and generation identity once and carry it through download, STT
and final Conversation submission. Commit only a nonempty final transcript.
No placeholder, partial transcript or raw audio is added to history or memory.

Use pinned NapCat `download_file_record_stream` with `out_format: wav`, 64 KiB
chunks, account preflight/postflight, a 20-second download deadline and one
active stream shared with images. File references are admitted safe basenames,
never model-selected paths or URLs. Validate ordered chunks, canonical Base64,
bounded actual bytes and exact terminal totals. NapCat's record header can
describe the original compressed size; it need not equal the converted WAV's
size. No provider guarantee of atomic peer-qualified fetching or durable cache
is implied.

Decode in memory only: at most 5 MiB, complete uncompressed PCM16 WAV, one or
two channels, 8–48 kHz and at most 60 seconds. The whole preprocessing deadline
is 60 seconds. Transcription uses the existing local `SttBackend`; session,
turn, generation, request and job IDs must all match the worker response.
Final text must be nonempty and within the existing 20,000-character ingress
limit. Reject oversize or malformed input without truncating it.

Cancel preprocessing on supersession, disable, delete and shutdown. A durable
cancellation fence and active-task check prevent late transcripts from creating
a generation or failure notice. On failure or restart with orphaned audio
preparation, create one durable text notice asking the owner to resend; never
automatically re-download or transcribe the old record.

Replies remain text by default. A clear voice-output request in this turn's
final transcript uses the same `channel.voice` gate as typed text. Arrival of a
record, quoted content and previous requests cannot authorize voice output.
The external path does not use the local realtime playback command.

QQ deployment uses its own loopback authenticated CPU/int8 STT worker, a new
credential and private state, and a fixed offline model snapshot. The existing
worker remains separate. Bound accepted worker jobs using the union of live
request tasks and unfinished native inference: a cancelled native job keeps
its capacity slot until it finishes. The dedicated stage sets capacity to one;
further requests fail promptly with a fixed HTTP 429 response. Resource idle
unload must treat preprocessing as busy.

## Acceptance

Automated gates cover owner admission before IO, deduplication, identity,
limits, failure notices, restart without replay, cancellation during download
and STT, swallowed cancellation and late results, ordinary text and requested
voice continuity, and persistence without raw audio. Real phone acceptance must
also establish that this pinned NapCat installation correctly decodes QQ's
actual voice codec and that the local model understands the spoken content.
A valid RIFF header or healthy worker alone does not establish that result.

Proactive messaging, group membership, per-participant memory and relationship
policy, voice enrollment, streaming partial transcripts and retained voice
assets remain outside this slice.
