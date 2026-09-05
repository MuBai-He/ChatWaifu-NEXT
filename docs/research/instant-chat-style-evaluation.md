# Instant-message conversational style evaluation

Date: 2026-09-05. Baseline: PR 16 merge `4efe1dd`.

## Scope

Improve ordinary instant-message replies at generation time: one immediate topic,
brief reactions, fewer unsolicited suggestions and follow-up questions, and endings
that can simply end. Explicit requests for detail or multiple questions retain their
requested content. Other presentation profiles and global character canon retain
their existing policy. No reply truncation, splitter changes, transport changes, or
new architecture contracts are involved. Sticker work is deferred for this slice.

## Method and observations

`tests/fixtures/conversation/instant_chat_style.json` defines eight synthetic inputs,
including brief acknowledgement, goodbye, verbose-history pressure, and two explicit
detailed tasks. `instant_chat_style_observed.json` records actual baseline and final
outputs from the locally configured `gemini-3.1-flash-lite` chat provider. Each variant
was sampled once per case through the real PromptCompiler and provider adapter,
using the default character, neutral kernel, answer/gentle response plan, no recalled
memory, fixture history, and instant_message profile. Requests bypassed the channel
and did not write conversation records or send WeChat messages. No credentials are
included. Compare the baseline compiler at the revision above with the current
compiler using the same fixtures and model configuration to repeat this evaluation.

The final six casual samples were 8–26 characters; five had no question marks and
one had one. Both requested tasks preserved their content: three topics with two
sentences each, and three explicit questions. These are descriptive samples, not
statistical reliability measurements or automatic quality scores.

Initial English-only guidance still reopened acknowledgement with advice/questions.
A second revision removed most questions but fabricated having made tea. The final
instruction explicitly forbids inventing activities to sound conversational and
keeps Chinese examples as style references. The final sample did not repeat that
fabrication, but this is not a proof of truthfulness across conversations.

## Remaining acceptance

Generic reassurance remains in some samples; the acknowledgement still adds a
suggestion about resting. One response echoes a style example. These are remaining
quality weaknesses, not a claim of fully natural character dialogue. Actual Runtime
planner state, memory and longer history can affect replies. Owner-visible WeChat
conversation acceptance is pending; no UI or transport acceptance follows from this
model-only evaluation. An intermediate run also emitted an HTTP stream cleanup warning
after all outputs completed; the final run completed without that warning.

## Checks

Runtime suite: 525 passed, 11 platform-specific skips. Ruff, Pyright, web and desktop
UI builds passed; final prompt adjustment also reran all 30 bubble presentation tests.
Existing frontend bundle-size and dynamic-import warnings remain.
