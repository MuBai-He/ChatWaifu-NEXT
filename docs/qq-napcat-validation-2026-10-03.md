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
transport fault reconnect, NapCat container recreation and QQ session recovery,
and an actual send/receipt interruption window. Real character-voice quality,
answer quality, long-term memory behavior and integration with the concurrent
search workstream still need their own acceptance.

See [setup and operations](qq-napcat-setup.md) and
[ADR 0064](adr/0064-qq-napcat-current-turn-voice.md).
