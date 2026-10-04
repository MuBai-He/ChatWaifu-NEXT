# QQ NapCat validation — 2026-10-03

Implementation branch: `mubai/qq-napcat-integration`, based on `e609345`.
This is CW2 implementation evidence. It is separate from QQ Agent Plus comparison
results and from the concurrent search/answer evaluation.

## Verified software behavior

- QQ uses the existing external gateway, character, conversation and memory path.
- Only configured-owner private structured text is admitted. Pairing requires the
  exact one-time command; ordinary Chinese messages do not fail or establish pairing.
- Normal QQ replies have no tools or TTS. An explicit fresh voice request exposes
  only `channel.voice`, bound to the current session/turn/generation and fixed owner.
- Delivered speech is the canonical assistant text. Voice failure produces one
  truthful text fallback under plan version 2, retaining the failed audio evidence.
- Real local WebSockets and SQLite cover authentication, RPC correlation, timeout,
  cancellation during audio read/account preflight, disabled connections, account
  replacement, committed send fences, lost receipt persistence and restart replay.
- Settings recover from missing ephemeral pairing resources after restart, confirmed
  pairing versus cancel races, and failed connection refresh without showing stale
  healthy state.

## Checks

| Scope                       | Command                                                                        | Result                                                   |
| --------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------- |
| Full Python repository      | `uv run pytest -q`                                                             | 2063 passed, 46 platform skips                           |
| QQ adapter boundary/review  | `uv run pytest services/runtime/tests/external_channels/adapters/qq_napcat -q` | 109 passed                                               |
| Complete QQ Runtime         | `uv run pytest services/runtime/tests/test_qq_channels.py -q`                  | 13 passed                                                |
| Python lint/types           | Ruff check/format; `uv run pyright`                                            | passed; 0 type errors                                    |
| Protocol contracts          | `pnpm --filter @chatwaifu/protocol test`                                       | 36 passed                                                |
| Web application             | `pnpm test`                                                                    | 333 Web tests passed; protocol/avatar suites also passed |
| TypeScript                  | `pnpm typecheck`; `pnpm lint`                                                  | passed                                                   |
| Frontend artifacts          | `pnpm build:web`; `pnpm build:desktop-ui`                                      | passed                                                   |
| Linux QQ fixture acceptance | stage Python pytest, same two QQ test paths                                    | 122 passed                                               |

Continuation checks added five recovery regressions: full supervisor reconnect,
successful Runtime reconstruction for text/audio with binding/session/history
retention, and a fresh scheduler reclaiming a lease after lost provider or internal
ACK. The combined adapter, Runtime and recovery suite passed **127 checks locally
and 127 on Linux**; Ruff check/format and strict Pyright passed. These tests use
real local WebSockets/SQLite with controlled providers. Runtime object rebuilding
uses test in-memory credentials and does not replace independent-process or real
QQ acceptance. The full repository/frontend results above are the initial
implementation checks; they were not inferred from the focused continuation run.

The native packaging command `pnpm build` additionally attempted the unchanged
Rust shell and failed to load `thiserror_impl` from the local Cargo artifacts.
Native app packaging is not counted as a passed check. The Web and desktop UI
artifacts above built successfully; no Rust source was changed.

## Linux staging

The separate source is `/home/mubai/cw2-qq-napcat-stage/source`, with state in
`/home/mubai/.local/share/chatwaifu-qq-stage`. The new user service
`chatwaifu-qq-stage.service` binds `127.0.0.1:8771`. Authenticated health and
provider discovery returned HTTP 200, and discovery contains `qq_napcat`.
The separate static Web service binds `127.0.0.1:18781` and returned HTTP 200.
An authenticated invalid pairing request returned HTTP 422 without echoing the
submitted token. The original Runtime service remained active throughout the
staging restart.
These are separate from the existing Runtime/search test environment.

Staging uses its own Python executable and source paths, with a read-only
site-packages reference to the existing test server dependency environment.
A fresh full frozen dependency installation timed out while fetching PyPI
artifacts, so clean Linux installation is not claimed. Pytest and pytest-asyncio
are installed only in the staging environment.

Staging now uses the existing server's current OpenAI-compatible chat parameters
and the corresponding chat credential, copied into its own configuration. The
original database has no `budget_json` column; the current protocol default budget
was validated/generated while retaining its 65,536-token context window. The
preparation checks made no live chat calls; the three owner-initiated turns below
subsequently exercised live chat inference. Memory extraction/summary remain deterministic demo,
and embedding remains local-hash for this transport/voice staging acceptance.

The separate `chatwaifu-qq-stage-tts.service` binds `127.0.0.1:8773`, uses a new
worker credential, and returned authenticated health/capability HTTP 200; requests
without credentials return HTTP 401. Its vendor code/configuration, working
directory, output and Python/Numba/HuggingFace/Torch caches are independent.
Model weights and reference audio are read from existing resources. User-service
read-only mount enforcement was not confirmed; the evidence relies on isolated
write paths and before/after resource hashes, not a verified OS sandbox.
Proactive tasks, wake phrases and resource sleep are disabled for staging.

One actual local GPT-SoVITS synthesis passed through this branch's Runtime TTS
adapter: 1,440 ms, 32,000 Hz mono PCM16 WAV, 92,204 bytes, non-silent samples and
valid RIFF frames; cold synthesis took 21.872 seconds. Evidence and the audio are
in `/home/mubai/cw2-qq-napcat-stage/validation/REAL-VOICE-ACCEPTANCE.json` and
`real-voice-smoke.wav`. This test made no chat-model or QQ calls and does not
establish phone playback, voice similarity, or real-model answer quality.

After synthesis, all three original services retained their process IDs/start
times; all 22 captured model/reference/vendor-configuration hashes were unchanged,
as were the original worker's loaded state and empty queue. No original Python
cache files had timestamps modified since staging configuration began. This cache
check is a timestamp inventory, not a full dependency-cache before/after hash.
Non-secret result evidence is under staging `worker/evidence/`. All 1,442 deployed
tracked source files also matched the implementation source manifest.

## Real-account staging acceptance

NapCat v4.18.28 is running and healthy in `cw2-qq-stage-napcat-napcat-1`.
All seven compressed layers and the image configuration matched the official
amd64 manifest before offline loading. The container uses immutable image ID
`sha256:dc4600051247e5716c9ea0db2a0b057a2357ff79d5ea383ba33ae1335ebbeeb7`.
WebUI `/webui/` returned HTTP 200. Docker binds only `127.0.0.1:16099` and
`127.0.0.1:13001`.
Non-secret provenance and deployment checks are recorded in
`/home/mubai/cw2-qq-napcat-stage/napcat/IMAGE-PROVENANCE.json` and
`DEPLOYMENT-ACCEPTANCE.json`. Existing container identities, start times and
restart counts were unchanged.
The initial fixture checks did not log into QQ. Subsequent manual QR login is
confirmed by WebUI `isLogin=true`, `loginPhase=ready` and `coreReady=true`.
The account-specific OneBot WebSocket server was added through the official
configuration API, preserving all other configuration. Authenticated
`get_login_info` and `get_status` returned a valid account and online status.
Exact owner-private command pairing is now confirmed. The durable connection
is enabled, has exactly one allowed sender, reports `ready`, and has no last error.

Three owner-initiated turns then passed on the real QQ client:

| Turn                       | Persisted delivery                                              | Runtime Skill              | Owner observation                 |
| -------------------------- | --------------------------------------------------------------- | -------------------------- | --------------------------------- |
| Ordinary message           | One text part, delivered with receipt, attempt 1                | None                       | Text received                     |
| Explicit voice request     | One audio part, delivered with receipt, attempt 1; no text part | `channel.voice`, succeeded | Voice received and plays normally |
| Following ordinary message | One text part, delivered with receipt, attempt 1                | None                       | Only text, no extra voice         |

All three turns and generations completed without recorded errors, using the
configured `openai_compatible` / `gemini-3.1-flash-lite` chat route. Sanitized
metadata is saved as staging `validation/QQ-THREE-SCENARIOS.json`. Provider token
usage was not persisted by the current Runtime and is reported as unavailable;
prompt budget estimates are not actual usage. Voice arguments/results are privacy
summarized, so persisted metadata does not independently compare spoken text with
canonical conversation text. Automated integration checks cover that contract.
The owner observations establish real text display and audio playback, while
voice similarity and answer quality remain separate acceptance scopes.

Only `chatwaifu-qq-stage.service` was restarted. The same enabled connection
returned to `ready` with one owner and no last error. Both snapshots contain the
same three turn/generation identities and identical delivery/skill metadata,
receipts and attempt 1. An independent read-only count across the whole connection
found three turns, three distinct generations, three deliveries and three parts:
two text and one audio, with exactly one `channel.voice` run. It found no duplicate
external message IDs, multiple deliveries per turn or retry attempts. Sanitized
post-restart evidence is in staging `validation/QQ-AFTER-RESTART.json`. These
snapshots omit delivery/part UUIDs and cannot independently establish their exact
identity across restart or exclude platform-side duplication.

Remaining real-account acceptance: denied senders/groups/media, interruptions,
and an actual send/receipt interruption window. Real character-voice quality,
answer quality, long-term memory behavior and integration with the concurrent
search workstream still need their own acceptance.

## Container recreation and reconnect continuation

Before mutation, a full read-only connection snapshot found no nonterminal turns,
deliveries, parts, generations or skills. Only the dedicated Compose service was
stopped. The two mounted data directories were backed up while stopped, with
`0600` permissions, before recreating the pinned container. The first recreation
preserved all Runtime ledger metadata and mounts, but QQ entered `waiting_qrcode`;
neither a healthy container nor retained login files established automatic login.

Inspection of the pinned image found `/app/entrypoint.sh` passes `-q $ACCOUNT`
only when `ACCOUNT` is set. The deployment lacked that setting. The generic
template now maps private `NAPCAT_ACCOUNT` to `ACCOUNT`, and the private stage
configuration takes the role account from the existing durable connection.
The effective container setting was checked for presence and equality without
printing it. This enables a saved-account quick-login attempt; it does not promise
that QQ credentials remain valid or that scanning can always be avoided.
Read-only inspection found consistent process HOME/UID and writable persistent
paths, no entrypoint removal of QQ login files, and no eligible quick-login entries
from the two supported list APIs. It did not establish why those entries were
ineligible or that all cached state was lost.

After this change QQ still required manual authentication. The owner scanned a
new QR; the active WebUI then reported logged-in/core-ready, and authenticated
OneBot `get_login_info`/`get_status` confirmed the same role account online. Runtime
automatically recovered the existing enabled connection to `ready`, with one
owner. It was not re-paired or restarted for this recovery.

The pre-recreation and post-login full-connection ledger hashes matched, including
internal turn/generation/delivery/part/skill identities, attempts, receipt presence
and the send lifecycle journal. Counts remained three turns, three deliveries,
three parts and one voice run. This closes the earlier snapshot's delivery/part
identity limitation for this particular quiet recreation window. It still does
not independently rule out platform-side duplication. Image, mount and port
settings stayed the same; the Compose config hash changed when `ACCOUNT` was
added. The original three service identities and all 22 resource hashes remained
unchanged. Stage Runtime and TTS process identities were unchanged during this
container test.

Private sanitized evidence is in staging `validation/`:
`RECOVERY-before-napcat-recreate-20261003T074410922614Z.json`,
`RECOVERY-after-napcat-recreate-20261003T074841427047Z.json`,
`RECOVERY-relogged-after-napcat-recreate-20261003T080053971347Z.json`, and
`QQ-RELOGIN-AFTER-RECREATE-RPC.json`. Automatic login without scanning remains
unverified; the demonstrated recovery includes manual QQ reauthentication.

## Real unavailable-TTS fallback

Only the dedicated stage TTS unit was temporarily stopped in a five-minute,
bounded fault window. The owner initiated a fresh voice request on QQ. Its
`channel.voice` run failed with `skill_internal_error` before creating an audio
part; the generation completed and sent exactly one text part with a provider
receipt, attempt 1 and plan version 1. The owner confirmed receiving the failure
explanation and text reply. This is synthesis-unavailable fallback, distinct from
the plan-version-2 fallback after an actual audio-delivery failure.

The fault-window guard restored the dedicated TTS service after the terminal
result, and a separate service check confirmed active state.
The restored worker's authenticated `/v1/health` also returned HTTP 200, `ready`,
empty queue and a not-yet-loaded model; this was a health check, not another synthesis.
Sanitized evidence is `validation/QQ-TTS-FAILURE.json` (including the owner's observation) and
`QQ-TTS-FAILURE-TURN.json`. The final capture
`RECOVERY-after-tts-failure-restored-20261003T080728868100Z.json` reports ready,
four completed turns/deliveries, one audio and three text parts, no active work,
and unchanged original services/resources. This test does not establish behavior
during an actual QQ send/receipt interruption or a stale audio result after
cancellation; those have controlled regression evidence but still need
real-account fault acceptance.

See [setup and operations](qq-napcat-setup.md) and
[ADR 0064](adr/0064-qq-napcat-current-turn-voice.md).

## Original A/B/C/D plan and phase B extension

The original stages are A private text, B images and reply references, C character
voice, and D incoming voice transcription, proactive messages and group chat.
Search/answer optimization is a separate workstream, not stage D. A's core text
and restart path and C's current-turn voice and unavailable-TTS fallback have the
real-account evidence above. Additional A/C access and fault gates remain scoped.
D1 incoming voice is tracked separately below; D2/D3 remain unimplemented.

Phase B now uses the same gateway and Conversation image loader. The authenticated
pinned NapCat stream API transports bounded chunks without public URLs or shared
paths; owner admission, account checks, batch limits, actual decoding, EXIF removal,
cancellation and duplicate admission are tested. Incoming images remain ephemeral.
Optional preset/learned sticker delivery uses existing scoped immutable assets and
the original send fence. A dedicated settings toggle is opt-in.

Reply references use durable same-binding admitted/confirmed messages and bounded
untrusted historical context. Unknown references, scope reset, photo redaction and
raw-ID ambiguity fail closed. Referenced text remains separate from fresh input and
cannot authorize voice. Assistant references also register the existing history
dependency so a later photo deletion redacts derived replies even outside recent
history. A real local OneBot peer verifies the reply segment's fixed current-message
target. These fixtures do not establish phone quote display.

Root independently integrated and reviewed all files, including new tests. Full
Python completed **2219 passed, 46 platform skips**; full Web **334 passed**;
strict Pyright, Ruff, TypeScript checks, lint and both Web/desktop UI builds passed.
The earlier native Rust packaging limitation is unchanged; no Rust code changed.
Real phone visual understanding, received sticker display and reply display remain
pending. Static PNG/JPEG and existing stickers do not claim arbitrary photo
generation, animated market stickers, quote-image replay or phase D capability.

Phase B commit `359d0808b0910a062110d5b65c97c1abe7075d3c` is now deployed to
the dedicated Linux stage. All 1449 tracked file hashes matched the source
manifest. The complete Linux QQ adapter, Runtime integration, recovery, outgoing
image and quote suites passed **278 tests**. The refreshed Web index and both
referenced assets returned HTTP 200. An authenticated active OneBot probe
confirmed the same paired QQ account online, with no QQ sends from the probe.

Before/after deployment captures retained the same four accepted turn,
generation, delivery and part identities, attempt 1, receipts and send journal;
no nonterminal work remained. Only the dedicated Runtime and Web units were
restarted. The dedicated NapCat container and TTS process, the original three
services, and all 22 protected model/reference/configuration hashes were
unchanged. Source, database and Web backups are private to the stage. Evidence is
`validation/QQ-PHASE-B-DEPLOY-RPC.json` and the `RECOVERY-before/after-phase-b-deploy`
captures. These checks establish deployment and preserved state, not phone image
understanding or quote/sticker display.

## Phase D1: incoming owner voice

Implemented the ADR 0066 slice: one owner-private structured record, bounded
authenticated WAV download, strict in-memory PCM16 validation and the existing
local STT backend. Durable admission allocates identity before IO, returns
immediately and deduplicates without repeating download. Only the complete final
transcript enters Conversation; raw audio, partial text and the internal audio
placeholder are not retained. This initial source used TEXT unless the current
transcript explicitly requested the voice tool; ADR 0067 later supersedes that
reply-medium gate as described below.

Independent review reproduced and then verified fixes for admission/registration
lifecycle cancellation and owner revocation during Conversation preparation.
Six separate root-source race fixtures passed: stop/disable/delete/interrupt
start no loader, old owner text starts no model, and supersession starts only the
new text with no voice. The original evidence scripts asserted the old defect;
the repaired checks reverse those assertions. The crash-recovery fixture uses
a committed-WAL crash snapshot, since clean shutdown now cancels durable audio.
An existing pre-start cleanup regression also verifies that an unopened
database is not queried during constructor teardown.

The pinned stream producer can emit legal short reads. A real Node file-stream
fixture `[1024, 65536, 29484]` is accepted, while the 256-chunk, 5-MiB, 20-second
and shared bounded-queue limits remain enforced. The STT worker retains capacity
for cancelled native inference and shared model loading; repeated cancellations
do not launch unbounded model initialization. All five worker response identity
fields are checked.

Root's final full Python command completed **2393 passed, 46 platform skips**.
The isolated STT worker suite passed **22 tests** and strict worker typing;
full Runtime Pyright reported zero errors, Ruff and changed-source format checks
passed. The unchanged frontend has **334 tests** passing, TypeScript/lint and
Web/desktop UI builds passing. Existing native Rust packaging limits remain
separate; these checks do not establish actual QQ speech decoding or accuracy.

Source `02e58018e403da209b102afcb0f34397d6e06a4f` deployed with all **1457**
tracked file hashes matched. Linux QQ tests passed **452**, and the worker suite
passed **22** with the existing production worker dependencies and read-only
Runtime test tooling. No new dependencies or model downloads were needed.
The dedicated CPU/int8 ASR unit is authenticated, loopback-only and enabled for
restart; its configured capacity is one. A real adapter round trip with synthetic
silence verified identity and authentication, without claiming speech recognition.
The three pinned offline model hashes remain unchanged.

Before/after captures preserved the four existing turns, generations, deliveries,
parts, attempt counts, receipts and full send lifecycle journal. No unfinished
work remained. NapCat and the dedicated TTS process retained their identities;
the original three services and 22 protected resources remained unchanged.
The new Runtime health confirmed the STT provider, and an active authenticated
OneBot RPC confirmed the same account online. Private source/env/database backup
is `backups/voice-20261003T104835Z`. Startup-failure recovery checks quiescence
and business facts before restoring both code and its compatible database;
newly accepted facts must never be overwritten by rollback.

At `2026-10-03T10:53:41Z`, a real owner-initiated QQ record produced one completed
audio input turn and one delivered TEXT part, attempt one, with a provider receipt
and zero `channel.voice` runs. The nonempty final transcript was committed instead
of the placeholder. The owner explicitly confirmed correct understanding and
text-only output on the phone. This establishes the pinned codec/model happy path
for this sample; it does not establish broad transcription accuracy or all voice
fault behavior. Source evidence is `QQ-D1-PHONE-SNAPSHOT.json` and
`QQ-D1-PHONE-ACCEPTANCE.json` in the private validation directory.

The subsequent recording at `2026-10-03T10:57:30Z` requested spoken output but
was transcribed as `用語間說一句話`. Its fresh voice gate was false, zero voice
Skills ran and one TEXT part was delivered. The owner corrected an earlier
playback confirmation: only text arrived. That correction is authoritative;
this sample did **not** establish the recording-to-voice-output happy path.
Evidence is `QQ-D1-REQUESTED-VOICE-SNAPSHOT.json`. It does not indicate a TTS
or QQ audio-send failure, because neither was invoked.

The owner subsequently requested model choice instead of keyword recognition.
ADR 0067 removes the text gate, query-dependent voice availability and forced
voice-tool choice. The host supplies the trusted reply capability for each
admitted owner QQ turn; the model chooses direct text or `send_voice` under an
optional channel-specific instruction. The original final transcript remains
unchanged, including STT errors. No script conversion or guessed homophone
controls authorization. Current user preference is interpreted semantically;
historical content is data. Manual, non-QQ, owner and active-generation checks
continue to constrain actual execution. This does not authorize proactive voice.

The final source passed **2382 Python tests, 46 platform skips**, **514 focused
integration tests** and zero strict Runtime/worker Pyright errors. Ruff and
changed-source formatting passed. The isolated worker suite passed **31**.
Scripted model tests exercise both media for the same keyword-free input,
direct text without forced retries, contextual schema/budget/disabled/plugin
boundaries, an unavailable function-calling provider, native function rejection
followed by plain-text fallback, current manual denial and other audio-provider
denial. Existing TTS/delivery failure, cancellation, duplicate and quote tests
still pass. These fixtures establish control flow; real-model choice and
fresh recording playback still require separate phone acceptance.

The worker exposes bounded local decode settings, preserving beam 1/no prompt
as defaults. The dedicated QQ stage may opt into beam 5 and a generic Chinese
initial prompt without output-command vocabulary. The prompt is `zh`-only;
VAD, temperature fallback and previous-window context remain unchanged.
Configuration and synthetic fixtures do not prove recognition accuracy;
a fresh owner-initiated recording must be checked after deployment.

The model-choice source `b21fcbc5a1fc5876077bc6915c7e7a6976f7f095` is deployed
with all **1457** tracked hashes matched. Linux QQ regressions passed **439**;
the isolated worker suite passed **31** using the existing dependencies. Private
backup `backups/chinese-voice-20261003T115437Z` was read back and checked for
source inventory/hashes, environment integrity and SQLite `quick_check` before
any source was overwritten. The rollout restarts only its own Runtime and ASR,
preserves their existing enabled/disabled settings and never restores the
database. The first preflight attempt changed nothing because it unnecessarily
required Runtime autostart; the corrected rollout preserves that existing setting.
An independent fake rollout suite passed **21** failure/preservation scenarios.

Before/after captures `RECOVERY-before-voice-deploy-20261003T114729450520Z.json`
and `RECOVERY-after-voice-deploy-20261003T115629093746Z.json` preserved all six
prior turn/generation/delivery/part identities, receipts, attempt counts and full
send journal. Both were quiescent; the same connection/configuration, NapCat and
TTS identities, original three services and 22 protected resources remained
unchanged. Active authenticated OneBot confirms the same account online. The
live dedicated ASR process environment confirms beam 5, the exact generic Chinese
prompt, offline CPU/int8 and capacity one. No model download occurred; only the
old keyword module was removed. The probe loads settings without instantiating
or calling a model and makes no QQ sends. Evidence is `QQ-CHINESE-VOICE-DEPLOY.json`,
`QQ-MODEL-VOICE-CONFIG.json`, `QQ-MODEL-VOICE-ONLINE.json` and the Linux test logs.

At `2026-10-03T12:12:04Z`, a fresh owner AUDIO input on the model-choice deployment
committed a nonempty final transcript without the placeholder. Its admission
deduplication count was one. It completed with exactly one successful agent
`channel.voice` run and one delivered AUDIO part, attempt one, with a provider
receipt. No TEXT part accompanied it. The owner confirmed successful phone
playback. This establishes recording transcription, actual model tool selection,
QQ delivery and handset playback for this sample. Tool arguments and results are
privacy-summarized, so the audit does not claim a spoken-text comparison. Evidence
is `QQ-MODEL-VOICE-PHONE-SNAPSHOT.json` and
`QQ-MODEL-VOICE-PHONE-ACCEPTANCE.json` in the private validation directory; the
audit uses a read-only committed-WAL transaction. The earlier failed recording
is not retrospectively counted as success or resent. Additional semantic
preferences and voice fault acceptance remain separate.

D2 proactive text is now implemented in the isolated source branch under
ADR 0068, disabled by default. It adds migration 38, fixed-owner policy/episode/
intent persistence, normal text-only character generation, permission rechecks,
receipt reconciliation and authenticated management/UI. Root's initial full
source regression completed **2464 passed, 46 platform skips**, with **358 Web**
and **74 protocol** tests, strict typing, lint and Web/desktop UI builds passing.
Later cancellation/current-owner fixes independently passed **118 focused tests**
with normal exit. A real composed-container HTTP/SQLite regression then passed
with the complete **21-case management suite**, proving default-off and read-only
GET/preview/history, stale-save 409 without side effects, and unknown-route 404.
The known lifecycle-only Python process-exit hang is retained separately and is
not counted as a clean command. These checks make no real model/QQ sends and do
not establish phone receipt or authorize opt-in. D3 group/member isolation remains
unimplemented. See [QQ next slices](qq-next-slices.md).

The final frozen D2 source `467bb7dc617b47881f23287f37d5e9f9c5282921`
completed **2503 Python tests, 46 platform skips**, with normal process exit;
Ruff, all **677** Python source formatting checks and strict Pyright passed.
The matching Web rebuild and documentation build passed; prior **358 Web**,
**74 protocol**, lint/type checks and desktop UI build remain unchanged.
Linux candidate fixtures completed **579 passed, zero skips/failures**, normal
exit in 108.766 seconds including the wrapper. All **1484** source hashes and
**224** loaded project module origins/hashes matched the frozen manifest; all
nine observed service identities/states were unchanged. No real model/QQ calls,
business database access, dependency installation or service mutation occurred.
Evidence is private directory `validation/.qq-d2-467bb7d-fixture-cyc2s3t9/`.

An independent real `Database.open()` migration and reopen on online backup copies
preserved **73 old tables, 598 old columns and 345 typed rows**, including duplicates
and all 37 old ledger checksums/timestamps. Seven new delivery binding fields
matched their original inbound turns. Foreign keys remained enabled, integrity
passed, and all three proactive tables were empty with repository policy off at
revision zero. The original nine services stayed unchanged and the verification
made zero network connection attempts. This copied-database evidence is
`validation/migration38-710e9a3-9l4hmc3h/` and is distinct from live deployment.

Root independently ran **23** prepared-rollout tests with normal exit. They cover
preserving the dependency environment, exact source/Web inventories, assets-before-
index replacement, existing operator-token fallback, terminal Skills, failed stop,
pre-38 code-only recovery and post-38 failure preserving new business facts without
ever restoring an old database. The frozen **1484-source / seven-Web-file** bundle
was uploaded with every archive/helper hash checked. Backup is private directory
`backups/d2-20261003T143245Z-34ff0fc4/`.

The first live rollout completed source/Web installation, real migration 38,
health and authenticated default-off management reads, but its all-table startup
comparison rejected expected journal garbage collection. The script stopped its
own Runtime and kept schema 38 and the compatible new source; it never restored
SQLite or modified another service. Root's exact read-only comparison found only
checkpoint cursor/updated-at changes. Each of the **seven** removed journal keys
matched exactly one existing **delivered** part with the same provider receipt,
both before and after. No unknown, conflicting or unacknowledged key was removed.
All other old business facts were identical. Root retained those facts, restarted
the compatible default-off Runtime and independently verified its health. The
original `deployment-evidence.json` remains a failed attempt; separate
`RESUME-verified-journal-gc.json` records the resolution.

Live authenticated policy/history/preview confirm one connection, policy revision
**zero/off**, no proactive intents and `disabled` eligibility. Active WebUI and
OneBot login/status RPC confirm the same QQ account online. Before/after captures
preserve all **seven** turns, generations, deliveries, parts, attempt-one receipts,
three Skill records and **44** send lifecycle events by metadata hashes. NapCat,
own TTS, original three services and **22** resource hashes remain unchanged;
only own Runtime process identity changes. Evidence is
`RECOVERY-before-d2-default-off-20261003T142429472085Z.json`,
`RECOVERY-after-d2-default-off-20261003T143819640178Z.json` and
`QQ-D2-DEFAULT-OFF-ONLINE.json`. The default-off deployment makes no proactive send.
The final resolved-deployment verifier passed with all **1484 source files** and
**seven Web files**, including the HTTP-served bytes, matching the frozen manifest.
All **38** migration checksums, foreign-key/integrity checks, default-off empty
tables and seven original receipts passed. The strict startup comparison preserved
**73 old tables, 597 compared columns and 345 typed rows**; its only allowed change
was the independently proven collection of seven already-confirmed journal keys.
The migration inventory counts schema columns; the startup projection also
compares FTS row IDs and excludes four transient connection fields. Eight protected unit identities,
22 resource hashes and configuration were unchanged. Root independently passed
**37** verifier tests, including rejection of removed unknown/unacknowledged keys,
changed receipts, retained-key mutations and timestamp-only checkpoint changes.
The separate successful evidence is
`validation/d2-resume-467bb7d-root-7fa51511/resolved-deployment-evidence.json`;
the original failed report remains retained. No old database was restored.
Real opt-in, phone receipt, quiet hours/disable, new-owner cancellation and restart
without replay still require separate acceptance. D3 remains unimplemented.

## D3 source and local Runtime acceptance (2026-10-04)

The isolated QQ branch now implements ADR 0069 through trusted participant links,
immutable group audiences, shared scene memory and per-member session/state,
fixed text-only group plans, operator management and shared settings. New routes
remain disabled. Group tools, media, voice, quotes and proactive sends are not
available. This source has not been merged into the primary checkout or deployed
to the Linux QQ stage.

Root independently passed **258** application, operator API, store, migration,
host-contract and group delivery checks after `b124504`. These include migration
39/40, legacy-group binding quarantine, global admission capacity and bounded
group epoch metadata. Unknown groups at the 128-entry epoch limit revoke the
whole connection without reusing epochs. After durable pause, only the captured
blocks are cleared; newer notices and stale admissions, observations and delivery
authorizations retain their revocation.

The nine new `10e3889` scenarios exercise the same actual RuntimeContainer,
operator guard, SQLite 40, Conversation, Memory and Character Kernel used by the
application, with a loopback OneBot WebSocket peer and a controlled model. Root
independently passed all nine, then **37** combined group/private QQ checks with
the NapCat 1.1.0 capability registration. They cover explicit enablement,
two-member state isolation, private-history exclusion, wire rejections and
deduplication, synchronous membership revocation while private preparation
blocks event consumption, late output after a provider swallows cancellation,
failed CAS, disable/reset/reconnect, pending input cancellation, known receipt
reconciliation after revocation and unknown no-replay. They establish local
integration behavior; they do not establish real QQ handset receipt or atomic
freshness of NapCat's member list.

Frozen production source `48ef3ae` completed the full Python suite with
**3023 passed, 46 platform skips**, normal exit in 153.48 seconds. Full strict
Pyright and Ruff passed; all **704** Python files passed formatting. Web and
protocol sources remain unchanged from the separately recorded **409 Web** and
**111 TypeScript protocol** checks, Web/desktop UI builds and Web lint. The UI
build is separate from native desktop packaging, which remains unverified.

Root then strengthened the membership-notice test to await the already-owned
generation task without issuing another cancellation. The reader's fence must
reject uncancelled late output on its own. All **37** group/private QQ checks,
strict typing, lint and test formatting passed again; production source is
unchanged from the full regression.

A read-only server observation found schema **38**, the four owned QQ services
active, a ready connection and no proactive policies or intents. Root verified
the actual source root and absence of the D3 application; source provenance
records `467bb7d`. This read did not recheck every source hash. Migration on an
online-backup copy, a verified default-off D3 deployment and a user-selected
two-member real QQ acceptance remain separate pending steps. No real group send
or proactive opt-in was performed for these checks.

## D3 Linux gate and default-off deployment (2026-10-04)

Frozen source/Web `f0588d1dad2a516eea2ac2489fcee33fd5e6e639` passed
**394 Linux group/private QQ fixtures, zero failures/skips**, with normal pytest
and runner exits. All **1535** source files and **259** loaded project modules
matched the candidate hashes. Python audit admitted only the fixtures' own
**180** ephemeral peer ports; blocked attempts and protected-port intersection
were zero. The nine unit identities and independent NapCat container identity
remained unchanged throughout. Null keyring, private logs/cache/tmp and the one
exact read-only `ldconfig -p` probe plus `/dev/null` open were explicit fixture
parameters. Two unaccepted runner attempts remain retained; neither is recorded
as a passing gate. These checks use a controlled model and OneBot peer.

Root independently reviewed all frozen executor/helpers/tests and passed
**71** real SQLite and simulated service/HTTP fault checks with normal exit,
Ruff and formatting. Fresh preflight reverified all **1484** old tracked source
hashes, no new-file untracked collisions and the five shared Web hashes/modes.
The separately verified source/Web archives both identify `f0588d1`.

The owned stage then completed an actual normal-exit rollout. Only
`chatwaifu-qq-stage.service` stopped and started. SQLite online backups and a
candidate rehearsal preceded the stopped in-place **38→39→40→40 reopen**.
Migration retained **76 old tables, 646 old columns and 385 typed rows**, all
38 old migration checksums/timestamps, delivery metadata and receipt/journal
facts without exceptions. Foreign keys were on, integrity passed, state scope
was backfilled and new group/proactive tables remained empty. No database was
restored. Private backups and the successful rollout evidence are under
`backups/d3-20261003T195622Z-f9e4a264/`.

Root's separate deployed-state verification matched all **1535 tracked source
files**, **60** preserved untracked files, **seven** candidate Web files on disk
and HTTP and **12** preserved cached Web assets. It verified **111** migration
module origins/hashes and **76-table, 642-column, 385-row** typed startup facts;
only the four documented transient connection health columns were excluded.
Checkpoint journal changes were **zero**, and all **eight** delivered receipts
were retained. Configuration, own unit enablement, eight protected unit process
identities, NapCat identity and **22** resource hashes were unchanged. Own
Runtime has a new process identity, healthy database and the persisted private
connection ready. Authenticated management reads show group routes, links,
observations and new group turns empty, policy **revision zero/off**, empty
proactive history and a disabled read-only preview.

The first supplemental root readback referenced the wrong D2 table name and
failed before writing its result. The corrected read-only verifier passed with
the actual `channel_proactive_episodes` catalog name; the failed-probe metadata
is retained separately. This was a verifier error, not a rollout or database
failure. Root's successful evidence is
`validation/d3-release-f0588d1-0vymhcy1/root-postdeployment-verification.json`.

No group audience observation, route enablement, real model request or QQ send
API call was made by these scripts. Runtime restart restored its normal private
connection. Actual new-version phone receipt, Phase B image/sticker/quote
behavior, D2 explicit opt-in and D3 user-selected two-member group acceptance
remain separate. This deployment is in the owned QQ stage; the source remains
in the isolated QQ branch rather than the primary checkout.

## Selected two-member group: real at/text acceptance (2026-10-04)

The operator selected one exact test group and named its second human member.
Read-only NapCat metadata confirmed the role account, existing bound owner and
exactly one other human. The owner's registered participant came from its
existing trusted private binding. The second member received a separate
registered participant; QQ nicknames did not grant identity or permission.

Operator APIs created two explicit links and a new two-member shared scene.
The route was created disabled at revision one, then enabled at revision two
using a second fresh account-matched audience observation. Only this group
became enabled. Private sender admission and the default-off proactive policy
were unchanged. Operator scripts called neither model nor QQ send APIs.

The owner confirmed that ordinary non-at input received no reply and both
members' actual at messages received text. Independent operator/SQLite reads
found **three completed group turns, three delivered text parts and provider
receipts** covering both participants. Two separate member sessions use one
shared `scene:` memory scope and distinct `scene_member:` state scopes. No
private binding points to the group scene. Runtime health, the persisted
private connection and foreign-key checks passed. The private source inputs,
replies and root verification are retained under
`validation/d3-selected-group-nmheduli/` rather than public documentation.

The selected route was then temporarily disabled at revision three. The owner
confirmed group silence and working private replies. Root verified the same
three completed group turns/receipts and a new delivered owner-private turn
after disabling. A new account-matched audience observation restored only this
route at revision four with the same scene and member mapping; proactive
delivery remained off. Phone receipt after resume remains pending. This proves
one real group's core at/text flow and operator disable; membership changes,
reconnect behavior and additional real memory-isolation scenarios remain
separate acceptance gates.

## Phase B private sticker opt-in and group revalidation (2026-10-04)

An authenticated read-only preflight verified the ready paired owner, advertised
image input and three preset assets with matching hashes and decoded static
PNG/JPEG formats. The connection initially had no presentation override, no
sticker opt-in, no learned stickers and no private image turns. This does not
prove real image understanding or sticker display.

The owner then explicitly requested that sticker replies remain enabled.
Operator management saved a presentation override for the existing default
character and private owner. A typed controlled `celebrate`/`happy` plan first
verified a complete short text plus one optional preset image, with no model or
QQ send call. The override uses the instant-message profile, two maximum text
parts, a 500-character preferred size, a 1000-character soft size and no cadence
delay or typing indicator. Technical/structured bypass remains enabled. Account,
character, principal and admitted private sender were retained; neither learning
nor proactive delivery was enabled. This opt-in is continuous, not a temporary
test toggle to be closed automatically.

Saving the connection configuration refreshed its existing QQ transport and
paused the selected group. An initial restore used a route revision that had
changed during reconnect and returned **409**; that failed attempt is retained.
Root then read current authority, obtained another account-matched two-member
observation and enabled the same selected route at revision **seven**. Scene,
member identity and all three original group text receipts were unchanged.
Private readiness, authenticated readback and foreign-key checks passed. No
service or source deployment was performed by these operator scripts.

Private evidence is under
`validation/phase-b-sticker-opt-in-y3sw8mya/`, with the original failure and
successful recovery recorded separately. Actual phone sticker display, incoming
image understanding and quote display remain pending, as does phone receipt
after group resume. The existing model-selected voice policy is unchanged.

## Phase B first phone sticker failure and planning repair (2026-10-04)

The owner reported **text only** for the approved celebration test. Root matched
the exact committed input and retained its failed evidence in
`validation/phase-b-missing-sticker-jd3sqgem/`: the completed generation planned
`answer`/`neutral`, and its two required text parts were delivered with provider
receipts. There was no image part or image-send attempt. Continuous sticker
opt-in remained saved; this is a planning failure, not a verified transport failure.

Two deterministic Character omissions reproduced locally: the intensifier
`特别` was mistaken for the negation `别`, and an admitted positive signal did
not otherwise produce a celebration plan. The narrow repair preserves negation
before an intensifier and all higher-priority response branches, then plans the
current positive signal as `celebrate`/`happy`. Generic answers remain undecorated
even when background affect is happy. This follows ADR 0015 and 0034; it does
not claim a new model-selected sticker tool or change model-selected voice.

The strengthened QQ regression removes its supplied response-plan fixture and
runs real Character planning, durable plan storage and the local OneBot socket
for enabled, disabled and missing-image cases. The failed-first planning checks
are retained in the turn history. After repair, 121 related checks passed and
the complete Python suite passed **3039**, with **46** platform skips and normal
exit in **151.69 s**. Full strict Pyright and targeted Ruff/format checks passed.
New-version server deployment and actual phone sticker display remain separate
gates; the first failed phone result is not promoted to a pass by these checks.

The reviewed repair commit `d6ba8137e4a5dcceb66033382d970b0d60b328ee`
was subsequently promoted as a **one-file overlay** on the frozen `f0588d1`
Linux QQ source. This is not a complete deployment of every file at the newer
commit. The executor first matched the exact prior source hash and passed seven
pure Character planning checks on Linux, without starting a test Runtime or
calling a model/send API. It backed up the source and consistent SQLite 40 state,
then restarted only the owned QQ Runtime. A fresh account-matched observation
restored the same authorized group at revision **nine** with the same scene and
two participant mappings.

Complete source-inventory comparison found only the reviewed planning file
changed, to SHA-256
`e3322b90ec125b0b32658c54b2998a2fea1edb48f6927a4b14ccda4f7a8fd9a9`.
Eight other unit identities, NapCat identity, 22 protected resource hashes and
configuration hashes were unchanged. All 14 prior delivered part receipts were
preserved; database integrity and foreign keys passed. Root separately re-read
authenticated connection/group/proactive state and the imported source path/hash:
private connection ready, continuous sticker opt-in saved, selected group enabled,
and proactive policy off. Private rollback copies, executor evidence and the
separate root readback are under `validation/phase-b-positive-plan-i5mwabx_/`.
The new celebration phone retry was requested; sticker and group-resume phone
acceptance are still pending at this checkpoint.

The owner then confirmed **text plus a normally displayed kitten sticker**.
Root matched the second exact celebration input, admitted after this overlay,
to its completed generation and durable `celebrate`/`happy` response plan. Two
required text parts and one optional final `kitten_happy` image all had delivered
status and provider receipts; the image payload matched the immutable preset
hash. The private raw turn, parts and human acknowledgement are retained in
`validation/phase-b-positive-plan-i5mwabx_/private-phone-sticker-acceptance.json`.
This single owner-direct preset display is now accepted. The initial text-only
failure remains retained; inbound image understanding, quote display, learned
stickers and post-resume group phone acceptance are not implied by this result.

## Post-repair Linux fixtures and real image understanding (2026-10-04)

The complete committed source at `76290f942c8218d9701d5e0bd74df0572f983069`
was frozen into a new isolated Linux fixture directory. Relative to `f0588d1`,
its only production-code change is the reviewed Character planning repair.
The previously reviewed child fences and launcher were retained; only frozen
identities/inventory counts and five additional Character/sticker test files
changed. No production source, service or database was changed by this run.

All **478** selected cases passed, with zero failures/skips and normal pytest
and runner exit. Before/after inventory matched all **1536** frozen files, and
all **268** loaded project module origins and hashes matched that candidate.
Root separately checked raw evidence, the repaired module's live-overlay hash,
and exact before/after service/NapCat identities. Connections reached only
**183** owned ephemeral peers, with no blocked attempt or protected-port
intersection. Null keyring, one exact read-only `ldconfig -p` probe and one
`/dev/null` write-open were explicit fixture boundaries; Python audit fences
are not an OS sandbox or real-phone proof.

Server evidence is `validation/positive-plan-linux-qomzecal/`; the frozen source
archive SHA-256 is
`c95bdb2c9e860d0619d6c0e3f138fe697d5535cb251c39b8576cdba6f1e618a1`,
and the manifest hash is
`f6a69c002dcca107aad948ef6e59a78fa0b3f286f2a1315e26153ef9b40403e7`.
Root's independent evidence review is in the corresponding local private
`cw2-qq-positive-linux-x2pgyoo1` directory. This does not imply a full new-commit
deployment; the live source still uses the previously recorded one-file overlay.

The owner then confirmed the static test image was understood correctly. Root
matched the exact approved question to a completed **image** turn admitted at
`2026-10-03T21:47:22.001905Z`. Its actual answer identified a red square on the
left and a blue circle on the right, agreeing with the deterministic oracle.
One text part was delivered with a provider receipt, with no voice or extra
image. The private accepted turn, part and human acknowledgement are under
`validation/phase-b-phone-image-h3zsqqt6/` alongside the oracle manifest. Its
local PNG hash identifies the test oracle, not provider-transcoded bytes.
This one owner-private static image understanding case is accepted; quote
display and general vision quality are separate. The phone quote test was
requested next.

## D2 explicitly approved single proactive-text test, armed (2026-10-04)

Authenticated readback and policy-only preview verified policy revision zero,
disabled state and empty intent history. A typed reviewable proposal was saved
under `validation/d2-live-plan-2y5vgln_/` without applying it or calling a model
or send API. The owner subsequently explicitly approved **one temporary test**.
The operator saved revision **one** with two-minute idle, 60-minute cooldown,
one local-day reservation, five-minute TTL, `Asia/Shanghai`, and a temporary
quiet-hours override. Voice, stickers and tools remain excluded from proactive
generation/delivery regardless of private inbound reply settings.

A bounded operator controller is actually running as
`cw2-qq-d2-once-test-2y5vgln.service`, with verified nonzero PID and running state.
It only uses policy management and intent reads, and never chooses a recipient
or calls model/message-send APIs. The existing Runtime supplies any authorized
generation and send. The controller restores the exact previous disabled policy
after the first terminal intent or its ten-minute test window, including signal
cleanup, and does not overwrite a newer operator policy revision. A newly
admitted owner input after saving is required; enabling does not authorize old
idle backlog. That fresh-input phone step was requested. At this checkpoint
actual proactive phone receipt and disabled-policy cleanup are still pending.

Separate root readback while the controller PID was still running confirmed
enabled revision one and preview reason `no_owner_activity`, with null owner
anchor and zero reservations. All previously admitted private inputs preceded
this policy revision; no old idle backlog was exposed by enabling. This is
actual server policy/anchor evidence, not a proactive phone-receipt claim.

## D2 first-window timeout corrected; same single test rearmed (2026-10-04)

The owner correctly reported having sent the requested fresh private message.
Root's earlier no-input summary was inaccurate: read-only SQLite showed the
text "我先离开一会儿" admitted at **06:09:12.403915 Asia/Shanghai**, with a
completed normal reply. The controller's original window ended at
**06:09:49.093707**, leaving only **36.689792 seconds**. Two-minute eligibility
would start at 06:11:12, after that operator window had already closed. This
was an incomplete test window, not an inbound-message failure or a proactive
phone acceptance. No outbound intent or proactive send was attempted.

The terminal controller recorded `test_deadline_reached`, exit **one**, and
`restored_off=true`. Independent authenticated policy, preview and history reads
plus read-only SQLite verified exact restoration of the previous disabled
policy at revision **two**, `disabled` preview, zero intents and valid foreign
keys. The private connection remained ready, continuous preset stickers stayed
enabled, and the selected group remained enabled at revision nine. The evidence
under `validation/d2-live-plan-2y5vgln_/root-post-timeout-verification.json`
explicitly corrects the earlier no-input summary. Only the already-terminal
test controller's failed unit marker was reset; evidence was preserved.

Under the existing explicit, still-unspent **one-test** approval, a new typed
proposal used expected revision two and the same temporary policy. The reviewed
operator controller now grants at least **five minutes after observing its
first fresh owner anchor**, with a **fifteen-minute total hard limit**. It still
restores the exact previous disabled policy, preserves any newer operator
revision and calls neither model nor QQ send APIs. Syntax and early/middle/late
anchor deadline calculations were checked before launch.

The actual transient unit `cw2-qq-d2-once-test-dv8t6e4b.service` was verified
active/running with a nonzero PID. Separate authenticated readback confirmed
enabled revision **three**, empty intent history, zero reservations and
`no_owner_activity`; a fresh owner input is required for this new revision.
Private proposal, controller and readback evidence are retained under
`validation/d2-live-retry-dv8t6e4b/`. The phone step was requested again;
proactive receipt and this new revision's disabled cleanup remain pending.

## D2 one real owner-idle proactive text accepted; default off restored (2026-10-04)

The fresh private owner input was admitted at **06:21:02.299952 Asia/Shanghai**.
Root independently observed `idle_threshold_not_reached` and a next-eligible
time of 06:23:02 while the controller was still running. The owner subsequently
confirmed **"收到额外的主动文字"**. Authenticated history and read-only SQLite
matched this anchor to exactly **one** outbound intent at policy revision three,
reserved after **120.057018 seconds**, with settled reason `delivered`, one
delivered text part and a real provider message receipt. There were no image
or voice parts in this proactive delivery.

The bounded controller completed with `one_intent_terminal`, `ok=true`,
`restored_off=true`, `Result=success`, normal exit **zero** and no running PID.
Separate root authenticated readback verified exact restoration of the previous
disabled policy at revision **four**, a `disabled` preview, and only the single
settled intent in history. Private readiness, permanent preset stickers,
the selected group at revision nine and valid foreign keys were preserved.
Only this completed transient test unit was stopped after evidence capture.
Private raw intent, owner input, text part/receipt and handset confirmation are
retained in `validation/d2-live-retry-dv8t6e4b/root-phone-proactive-acceptance.json`.

This accepts the **single owner-idle proactive text plus exact default-off
cleanup** case. The original incomplete window remains recorded above.
Quiet hours, revocation, input cancellation, restart and uncertain-send phone
checks remain separate; this one-test opt-in does not authorize ongoing
proactive delivery. Group-resume and private quote display are also pending.

## D2 disabled Runtime restart and selected-group revalidation (2026-10-04)

Fresh authenticated readback found policy revision **four** disabled and exactly
the single delivered proactive intent. Private input count was fourteen and group
input count was three, with no new quoted or post-resume group phone test input.
The concurrent search checkout remained at committed `e609345`, with forty-one
tracked changes and seventy-nine untracked paths; no stable integrated release
or search permission was inferred from that state.

Before restarting, root verified zero nonterminal channel turns/intents, saved a
private online SQLite backup, and captured exact typed row/column hashes for
six channel/proactive business tables: **17** channel turns, **18** deliveries,
**21** delivery parts, **1** outbound intent, **1** policy and **2** episodes.
Only `chatwaifu-qq-stage.service` was restarted. It returned active with a new
process/invocation, healthy Runtime and ready private connection. Every captured
business table retained its exact hash and row count; policy revision four,
the single settled delivered intent, connection configuration and the reviewed
Character overlay hash were unchanged. The other eight unit identities/states
and the NapCat container identity/process/start time were unchanged.

Reconnect correctly paused the selected group at revision **ten** with reason
`reconnect`. The operator then observed only that selected group's audience
through the authenticated management API, verified the same account and exact
two-member sender set, and used current-revision CAS to restore it at revision
**eleven**. The shared scene, participant links and speaker grants were preserved;
foreign keys passed. No operator model or QQ message-send API was called. Raw
backup, before/paused/after readbacks and typed fact hashes are retained under
`validation/d2-real-disabled-restart-0nm7e0dn/`.

This proves the captured live restart/cleanup facts, without substituting for
phone absence or display observations. Resumed real group text, no repeated
private proactive greeting and correct same-binding quote/display checks were
requested together. Those phone results remain pending at this checkpoint.

The owner subsequently confirmed **"群里收到新文字，私聊无重复主动消息"**.
Root matched the exact new group input "恢复测试，你好", admitted at
**06:33:41.907700 Asia/Shanghai**, to a completed turn at route revision eleven
in the same shared scene. Its sender matched a granted linked participant and
it had exactly one delivered text part with provider receipt. Group history
now contains four turns; the previous three remain intact. The private policy
snapshot at revision four and single delivered proactive intent history were
exactly unchanged. Raw handset confirmation and matching turn/part evidence
are retained in `root-phone-restart-group-acceptance.json` in that same folder.
This accepts the resumed selected-group text and absence of repeated private
proactive greeting after the real restart; quote display remains pending.

## B real same-binding quote text and QQ display accepted (2026-10-04)

The owner confirmed **"结论正确，显示引用标记"**. Root read-only SQLite
verification matched the quoted private input admitted at **06:35:20.484716
Asia/Shanghai** to the unique previously delivered image-answer text part from
the same connection and binding. The referenced generation was completed,
the provider ID did not collide with an admitted user message, and no photo
redaction applied. Its text was the previously confirmed left-red-square and
right-blue-circle image answer.

The new completed answer was "左侧：红色的正方形\n右侧：蓝色的圆形", with exactly
one delivered text part and provider receipt and no image or voice part.
The persisted fresh input contained its reply-reference metadata. The adapter
derives the first part's reply segment target from that current admitted message;
the model cannot choose it. The QQ marker was observed on the handset, while a
raw outgoing packet capture is not claimed. Foreign keys passed. Private exact
input, referenced part, matching reply/receipt and handset confirmation are
retained under `validation/phase-b-phone-quote-8cuetj5h/`.

This accepts one owner-private same-binding quote text/display case. Together
with the separately recorded static image and preset-display cases, B's core
real phone checks are now verified. Animation, photo retention, sticker learning,
cross-binding history and general image-answer quality are not added by this
acceptance. Additional A/C fault, D2 quiet/revocation/input-cancellation, D3
privacy/membership and search-integration gates remain open.

## D2 quiet-hours negative check prepared, not enabled (2026-10-04)

After the separately confirmed one-test authorization was consumed, a new typed
quiet-hours proposal was prepared without applying it. Current policy remains
disabled at revision **four**. The proposal preserves `Asia/Shanghai` and the
23:00–08:00 quiet window, with one-minute idle/cooldown, two local-day reservations
(one is already used) and two-minute TTL. The latter bounds avoid daily budget or
the completed check-in's cooldown masking the quiet-hours decision. The intended
result is **zero new proactive intents/sends**, after a newly admitted owner input.

The reviewed policy-only controller has a five-minute initial input window,
at least three minutes after its first fresh anchor and an eight-minute total
limit. It requires the entire possible window plus margin to remain inside quiet
hours and the same local date, observes the actual `quiet_hours` preview for
thirty seconds after idle eligibility while history remains exactly unchanged,
then restores the exact previous disabled policy through current-revision CAS.
It preserves newer operator revisions and calls no model or QQ send API.

Private typed proposal/controller are retained under
`validation/d2-quiet-negative-plan-77e153o5/`. Syntax and early/middle/late anchor
deadline calculations passed. A real unapproved-run guard check rejected the
controller before policy/credential calls; independent authenticated readback
confirmed the prior disabled snapshot unchanged and no armed/result file.
Its approved source hash is
`9f4475adc1d1abdc01345fe3744659543957aa8ed92b545377647d5ff8ff0101`.
New explicit opt-in was requested under ADR 0068; no controller unit was started
and no quiet-hours phone acceptance is claimed at this checkpoint.

The owner subsequently separately approved **"临时启用静默检查"**. The saved
proposal records that direct approval, and the deployed controller hash still
matches the reviewed hash above. Actual transient unit
`cw2-qq-quiet-test-77e153o5.service` was verified active/running with a nonzero
PID. Separate authenticated readback confirmed temporary enabled revision
**five**, the proposed quiet window, `no_owner_activity`, one existing reservation,
one remaining budget slot, and the exactly unchanged single-intent history.
The initial fresh-input window ends at **06:51:56.816810 Asia/Shanghai**; a
first owner input grants enough additional observation time within the stated
eight-minute hard limit. The phone fresh-input/no-extra-text step was requested.
This is an armed checkpoint, not quiet-hours phone acceptance or cleanup proof.

## D2 actual quiet-hours block accepted; default off restored (2026-10-04)

The owner confirmed **"只有正常回复，没有额外主动文字"**. The actual private
text was "静默测试，我先离开一会", admitted at **06:47:44.135424 Asia/Shanghai**
and completed with one delivered normal text part and provider receipt. At
06:48:44 the episode became due; the controller observed `quiet_hours`, not
budget exhaustion or cooldown, continuously for **30.46842296 seconds**. One
budget slot remained and the old reservation's one-minute cooldown had elapsed.
There were zero new proactive intents and the original delivered intent history
was exactly unchanged.

The controller ended with `quiet_hours_blocked_without_new_intent`, `ok=true`,
`restored_off=true`, no running PID, `Result=success` and normal exit zero.
Independent policy/preview/history reads and read-only SQLite verified the exact
previous disabled policy restored at revision **six**, `disabled` preview,
one unchanged existing intent, valid foreign keys, ready private connection,
permanent stickers enabled and the selected group unchanged at revision eleven.
Only the completed transient test unit was stopped. Private raw phone/input,
due preview and cleanup evidence is retained in
`validation/d2-quiet-negative-plan-77e153o5/root-phone-quiet-acceptance.json`.
An initial root verifier expected the suggested prompt's trailing "儿"; the
actual input omitted it. That probe mismatch is retained separately and the
passing verification matches the exact admitted owner timestamp and actual text.
It was not a Runtime failure.

The owner then requested a wake-up-readable acceptance document and instructed
this chat to keep future commits outside the other chat's repository. Six owned
documentary changes were hash-verified into independent clone
`/Users/mubai/Desktop/CW2-QQ-integration`, whose Git common directory is its own
`.git`, with no object alternates or remotes. Only the verified transferred edits
were restored in the old worktree, leaving its HEAD unchanged and worktree clean.
Subsequent changes and commits use the independent repository. Transfer evidence
is retained under `/private/tmp/cw2-qq-independent-3wv7obn5/`.

## Independent repository checks and wake-up acceptance document (2026-10-04)

The independent clone installed locked Python/Node dependencies using offline
caches into its own virtual environment/workspace. Protocol, Runtime and worker
SDK package imports were verified to originate in that repository. The focused
private QQ adapter/Runtime, group application/API/store/migration/host and
proactive Runtime/repository/API campaign passed **838** with zero failures,
errors or skips, one audioop deprecation warning, and normal pytest exit zero.
Full strict Pyright, relevant Ruff, Web typecheck/build, documentary Prettier
and diff checks passed. The Web build retains its existing bundle-size warning.
The first test invocation named a nonexistent repository test file and exited
four without running tests; its log was preserved separately, then the actual
file inventory was used for the passing campaign. Raw JUnit/logs, module origins
and independent root review are under the transfer evidence directory above.

No production source changed in this checkpoint, and no new version was
deployed merely for documentary edits. All three completed transient proactive
test units were independently observed inactive/not-found with zero PID.
The remaining phone and controlled-fault steps are organized in
[QQ phone acceptance](qq-phone-acceptance.md), including what can be tested
without enabling another policy and what needs a prepared operator window.
These automated checks do not claim the still-pending real phone fault cases.

## Complete Runtime voice receipt fault coverage (2026-10-04)

Three new protocol cases send a successful OneBot response with a missing,
malformed or boolean message id. The actual Runtime, SQLite repository and
loopback WebSocket are exercised with scripted model/TTS providers. Each case
verifies exactly one AUDIO send attempt, one TEXT fallback saying the voice
result is unconfirmed, and the same durable delivery plan revised to version two.
The failed optional audio part retains `qq_delivery_unknown`, its journal entry
remains `unknown`, the required text part has a confirmed receipt, assistant
history contains the actual fallback, and the temporary audio asset is removed.

After a complete Runtime shutdown/startup on the same state, the prior plan and
unknown fence survive. Re-ingesting the same input is rejected as a duplicate;
a fresh owner text reply completes without replaying the old voice or fallback.
The focused private Runtime and delivery campaign passed **81**, zero failures
or skips, normal exit zero, with only the existing audioop deprecation warning.
An initial test-only missing enum import produced three failures and is retained
in `uncertain-voice-receipt.xml`; the corrected full checks and raw log are
`voice-receipt-final.xml` and `voice-receipt-final.log` in the independent evidence
directory. This checkpoint changes tests and documents, not production behavior
or deployed source. Strict types, Ruff and formatting are checked independently.

ADR 0064 now points explicitly to ADR 0067 and describes its current optional,
model-selected voice policy, removing contradictory keyword-gate prose. This is
a documentary correction to the already implemented decision. Real phone
send-failure and uncertain-receipt cases remain pending.

A read-only server check at **2026-10-04 07:20:29 Asia/Shanghai** again found the
private channel ready, proactive policy off at revision six, exactly unchanged
single-intent history, the selected group enabled at revision eleven, and all
three completed temporary controllers inactive/not-found with zero PID.

## Real nonowner private-access acceptance (2026-10-04)

The fresh live preflight at 15:12:48 Asia/Shanghai found the private connection
ready, unchanged disabled proactive policy revision six, selected two-member
group enabled at revision eleven, one original intent and the expected source
overlay hash. The owner then confirmed **"睦月无回复，主人正常收到回复"**.

NapCat's actual receive logs show the linked nonowner's
`私聊权限验收:我是睦月` at 15:16:19 and the owner's shortened
`私聊权限验收` at 15:16:36. The nonowner has zero newly admitted turns on this
connection since preflight. The owner input was admitted at
`2026-10-04T07:16:36.884022+00:00` and completed at
`2026-10-04T07:16:51.691526+00:00`, with exactly one delivered AUDIO part and
provider message receipt on the same one-owner allowlist. This accepts this
specific private refusal and continued owner reply availability; the phone
confirmation does not newly establish audio playback quality.

The passive observer had filtered for exact suggested text and did not match
the half-width colon or owner's shortened input. The passing verification uses
the actual provider receive logs and SQLite, not a fabricated wire capture.
An initial root probe also incorrectly required TEXT-only output; its mismatch
is retained, and the corrected requirement permits the implemented ADR 0067
model-selected reply medium. No Runtime behavior, permissions or policy changed.

Private evidence is under
`/home/mubai/cw2-qq-napcat-stage/validation/phone-acceptance-session-tfbezc3q/`,
including `private-preflight.json`, `private-access-provider-log-lines.json`,
`root-first-access-probe-mismatch.json` and
`root-phone-access-acceptance.json`. The pending group subject-correction and
private-secret isolation cases remain separate real-phone gates.
