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

The live staging profile remains the explicit demo chat/fake-TTS first-start
profile. Fixture tests inject model and synthesis behavior; they do not establish
real-model answer quality or real character-voice synthesis.

## Pending real-account acceptance

NapCat v4.18.28 is running and healthy in `cw2-qq-stage-napcat-napcat-1`.
All seven compressed layers and the image configuration matched the official
amd64 manifest before offline loading. The container uses immutable image ID
`sha256:dc4600051247e5716c9ea0db2a0b057a2357ff79d5ea383ba33ae1335ebbeeb7`.
WebUI `/webui/` returned HTTP 200. Docker binds only `127.0.0.1:16099` and
`127.0.0.1:13001`; the latter still needs account-specific OneBot configuration.
Non-secret provenance and deployment checks are recorded in
`/home/mubai/cw2-qq-napcat-stage/napcat/IMAGE-PROVENANCE.json` and
`DEPLOYMENT-ACCEPTANCE.json`. Existing container identities, start times and
restart counts were unchanged.
No QQ account has been logged in, paired, or sent a real test message by these checks.

Remaining acceptance: role QQ login, exact owner pairing, ordinary text then an
explicit voice request then ordinary text again, real phone playback, denied
senders/groups/media, interruptions, transport reconnect, Runtime restart, and
an actual send/receipt interruption window. The demo model and fake synthesis
must be replaced by the intended character/model configuration first.

See [setup and operations](qq-napcat-setup.md) and
[ADR 0064](adr/0064-qq-napcat-current-turn-voice.md).
