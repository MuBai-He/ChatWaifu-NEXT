# Q02 WeChat Runtime delivery evidence (2026-10-03)

> **Correction (2026-10-03):** The earlier row below is a real WeChat
> provider control round, but its running `chat` route was
> `demo/chatwaifu-demo`. It must not be counted as evidence for Gemini model
> quality or a Q02 real-model pass. The corrected authenticated Gemini round
> is recorded at the end of this document and had a separate delivery failure.

This record covers one real, user-authorized WeChat channel round after the
`weixin_ilink` QR authorization was confirmed in the client. It is a delivery
check, not a claim that the user saw or heard the reply.

## Environment

- Worktree: `mubai/qq-agent-plus-implementation`
- Runtime: local source Runtime on `127.0.0.1:8765`
- Channel provider: `weixin_ilink`
- Connection status at the check: `ready`, enabled, revision `1`
- Model route: the running local `chat` route, authenticated through the
  configured Runtime provider; credentials are intentionally omitted.
- Test input: `Q02测试：请回答 2+2，并保持简短。` (fixed test text entered in
  the bound WeChat conversation; no private data).

## Authoritative evidence

The Runtime log and SQLite state were read after the provider poll completed.

| Layer | Evidence |
| --- | --- |
| Provider poll | `message_observed`, then `ingest_returned`, `duplicate=false` |
| Generation | `assistant.generation_started` and `assistant.generation_completed` |
| Channel turn | `status=completed`, `error_json=NULL`, reply length `120` characters |
| Delivery plan | `part_count=2`, instant-message profile, 3 second cadence delay |
| Provider send | Two `POST .../ilink/bot/sendmessage` responses were HTTP `200` |
| Part state | Both parts `status=delivered`, attempt `1`, no part error |
| Delivery state | `status=delivered`, `delivered_at=2026-10-02T16:50:15.659446+00:00` |
| Persisted events | `channel.delivery_part_acknowledged` for both parts, followed by `channel.delivery_plan_completed` and `channel.delivery_acknowledged` |

The corresponding local counts after the round were:

```text
channel_connections=1
channel_turns=1
channel_deliveries=1
channel_delivery_parts=2
```

The connection and delivery identifiers remain in the local SQLite row and
event store for correlation; this document does not copy account keys,
sender keys, access tokens, or message payloads.

## Acceptance boundary

This proves the real WeChat provider path from inbound poll through Runtime
generation to provider delivery ACK. It does **not** prove that the message
was displayed to or read by the user, and it does not prove physical audio
playback. The WeChat desktop window subsequently showed that it needed a
phone login, so a user-visible ACK still has to come from the user on the
phone. Q02 remains open until that ACK and the separate physical playback
check are recorded.

## Corrected Gemini route check (2026-10-03)

After the control round, the running `chat` route was updated and read back as
`openai_compatible / gemini-3.8-flash-high / https://mubai.website:8318/v1`.
The saved budget was `context_window=32768`, output reserve/request cap
`8192`, estimate margin `0.15`, `scaled` sections, history `32`, memory
candidate limit `24`, and tool-result limit `131072` bytes. The endpoint model
list was authenticated and contained `gemini-3.8-flash-high`; the API response
confirmed that a chat key was configured without returning it.

The fixed test text was admitted through the local channel ingress using the
bound connection's secure gateway token. The generation completed with the
reply `2+2 等于 4。` and did not use the Demo fallback. The reply also said
that the model name was not exposed to the character, which is expected from
the nonsecret prompt context and is not evidence against the route.

This injected ingress did not carry the provider-native WeChat reply context,
so the delivery plan failed before `sendmessage` with
`channel_context_missing` (first part failed; second part was cascade-cancelled).
It proves the real Gemini generation path, but it is **not** a successful
WeChat delivery or user-visible ACK. A real inbound message observed by the
WeChat poller is still required for that final boundary.

## Corrected real Gemini WeChat round (2026-10-03, 02:13 Asia/Shanghai)

The requested test text was sent from the bound WeChat conversation and was
observed by the real `weixin_ilink` poller. This round is separate from both
the earlier Demo control round and the synthetic ingress above.

| Layer | Authoritative evidence |
| --- | --- |
| Real inbound | `external_message_id=7511850923595736072`; `channel_turn_id=6f4d2b6f-948a-4ce3-91eb-39036f2246d0`; persisted user text exactly matched the test text |
| Route | `backend_kind=openai_compatible`; prompt identity recorded `provider=openai_compatible`, `model=gemini-3.8-flash-high`, `presentation_profile=instant_message`, endpoint digest only |
| Prompt budget | estimated budget `21370`, used `4321`, output reserve `8192`, margin `0.15`, `dropped_history_turns=0`, no omitted persona/photo/summary content |
| Generation | `generation_id=3d280e0c-3a43-4549-90f7-6d87b6c4acee`; state `completed`; reply `2+2 等于 4。` |
| Delivery | `delivery_id=85eb460b-9bbc-4e3d-a7b3-f2468218aff3`; state `delivered`; one required text part, attempt `1` |
| Provider ACK | part and plan acknowledgement events persisted; provider message ID `chatwaifu-85eb460b9bbc4e3da7b3f2468218aff3-000` |
| Client visibility | WeChat window visibly showed the new `Q02真实模型复核` message and the adjacent reply `2+2 等于 4。` at 02:13 |

The persisted interaction trace does not contain provider-reported token usage
for this short channel round, so the prompt budget estimate is not presented as
vendor usage. The real WeChat/Gemini generation and delivery boundary now
passes; this record still does not prove that the user heard a physical audio
response. The separate TTS/playback artifact has `playback_completed=true` but
does not contain user-heard confirmation.

## Appendix: separate desktop audio round

This document records a WeChat **text** channel round only. It must not be
joined with the separate `desktop-pet` audio round: that round used the TTS and
PlaybackService path and was later followed by the user's explicit “听到了”
confirmation. Its machine evidence and confirmation scope are recorded in
[Q02 物理语音播放与用户确认](q02-physical-playback-user-confirmation-2026-10-03.md).
