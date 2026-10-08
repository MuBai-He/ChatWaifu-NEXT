# ADR 0079: Owner-enabled QQ account operations

Status: Accepted for the owner's 2026-10-07 opt-in; one real poke passed model,
provider and handset acceptance. Other account operations retain separate acceptance.

## Context

The owner requested that the character operate QQ as her own account. Previously
QQ API imports primarily described unavailable actions, group autonomy off exposed
no QQ skills, and a request to poke produced only text. Account operation authority
must have an explicit persisted source rather than be inferred from model prose.

## Decision

Add operator-controlled `ChannelRuntimePolicy.qq_account_enabled`, default false,
using the existing authenticated CAS settings API and Web UI. It enables the new
builtin `qq.account` in already admitted QQ private/group foreground turns even
when group autonomous participation is off. It does not admit additional senders,
change autonomous participation, enable owner desktop skills, Calendar, or private
Runtime memory. QQ recipient IDs can select other friends/groups on that same bot
account. The operator's opt-in authorizes these QQ operations without another
desktop confirmation for every invocation; manifests still declare permissions and
side effects. Server-side authority, not manifest prose, grants execution.

Pin 158 declared operations to the running NapCat 4.18.28 official OpenAPI.
Use progressive discovery/activation for the imported API, with `send_poke`
available directly in the initial QQ tool list. Explicit imperative gesture
requests such as “戳一戳我” require native tool selection; definitions, quotes and
negations do not. Live owner requests at 16:29 and 16:31 on 2026-10-07 exposed
that permissions alone left this phrase on the ordinary text path with zero
tool calls. The gesture predicate and initial projection repair that path without
injecting all 158 operations. This preserves unrelated desktop source routing. Reject
undeclared actions/parameters, invalid schema inputs and oversized requests. Every
call rechecks the live admitted generation, original scene/route, enabled policy,
client identity, logged-in bot account and exact provider version before effects.
The existing foreground cancellation behavior remains in force. No implicit retry
is added for uncertain provider outcomes. Account mode does not itself enable
durable task/background permissions from ADR 0078.

Host administration, raw packets and login secrets remain outside QQ persona
operations. Media uses public URLs/base64 or separately authorized artifact delivery;
local host paths and embedded CQ codes are rejected. Retrieved QQ content remains
untrusted context. Poke defaults to the current speaker and current group, and
success requires an actual provider result. Provider success and client display
are distinct evidence.

## Persistence and verification

The policy is stored in existing versioned settings JSON, requiring no migration.
Python, generated schemas, TypeScript and Zod carry the same flag. Tests cover
policy restart, all capability projections, other QQ recipients, blocked host
inputs, revocation, provider failure, actual gateway execution without desktop
confirmation, and UI CAS save without enabling unrelated permissions. Existing
scene-query compatibility tests continue to cover their separate catalog.

Deployment must preserve live configuration, model routing, database and QQ
identities. A restart requires fresh audience observations before restoring the
same existing routes. Real model invocation and handset effects require a live
QQ request and cannot be inferred from deterministic fixtures.
