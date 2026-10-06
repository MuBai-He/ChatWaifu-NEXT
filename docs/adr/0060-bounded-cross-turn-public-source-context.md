# ADR 0060: Bounded cross-turn public source context

- Status: Accepted
- Date: 2026-10-01

## Context

Q02 Runtime evaluation found that follow-up checklists receive assistant prose but
not the original public READ results from earlier turns. Conditions and unresolved
queries can disappear. SkillRun audit persistence intentionally stores structural
summaries; source URLs, search queries and page text may contain private data even
when obtained from a public page. Expanding audit plaintext or treating generated
answers as verified memory would violate the existing privacy boundary.

## Decision

Conversation owns eligibility. From prepared, redacted history, it selects up to
eight prior assistant generations from the same stable source route, independently
of the text budget's selection of assistant prose, then verifies they completed in
the current session. A verbose reply omitted for text budget does not revoke the
original source; the complete source projection has its own frozen input guard.
Friendly labels and received
timestamps do not establish identity. Cross-session ledgers, other senders or
audiences, interrupted generations, reset history and redacted generations cannot
supply source bodies.

An internal version 1.0 SourceContextPacket carries existing typed SkillRunSnapshot
receipts through a read-only port. Runtime Skills queries only the requested
session/generations and host builtin web.search/search or web.read/read runs with
agent origin and a matching builtin execution plan. Manual, plugin, external MCP,
write and unrelated tool runs are excluded. At most 32 terminal receipts are
returned; excess receipts are explicitly marked. No tools execute during loading.

Runtime Skills reuses its existing bounded ephemeral result store. Original bodies
are available only while that store retains them. Restart, stop or eviction leaves
a durable success receipt with an explicit original-result-unavailable state;
failure receipts expose only their normalized code, never raw arguments or error
details. No new source database, global cache or long-term memory writes are added.

The Agent adds a trusted policy and a separately encoded user-role JSON data block
to both ordinary and tool-capable requests. Retrieved content is untrusted evidence,
not instructions or proof of current validity; search snippets are discovery only.
Follow-up summaries must retain relevant conditions, exceptions and unresolved
verification. The complete request is counted against the frozen input allowance.
Each original result is retained whole or explicitly omitted for budget, never
silently sliced. An omitted body retains available source metadata separately; no
metadata is reconstructed after eviction. If source metadata itself cannot fit,
old metadata is omitted with an explicit marker while receipt states remain intact.
Existing cancellation and final input
guards still govern dispatch.

Tool-schema exposure does not itself require an external operation. The Agent may
use the text-only path for an explicit transformation of prior source material,
only when a successful host public READ receipt has an available nonempty original.
A bounded, conservative check examines the current user's request, not retrieved
body text or old assistant prose. Supplied URLs, fresh verification, latest facts
and explicit writes or reminder requests retain the normal tool/permission path.
Search-only, failed or unavailable receipts cannot satisfy this check. Incidental
schemas selected by words such as checklist or reminder are omitted for eligible
source-only transformations. Original evidence remains untrusted and subject to
the same complete-input budget; its availability does not establish current rules.
Ambiguous or unsupported wording retains ordinary routing. This is a narrow intent
repair, not a general semantic classifier or a promise to recognize every wording.

The evaluator uses the same port and projection, gives completed synthetic history
its generation IDs, records receipt availability, and fingerprints all added code.
Resumed evaluations honestly report unavailable original bodies if their isolated
Runtime no longer holds them; they do not replay completed reads or restore bodies
from generated prose.

## Consequences and validation

This preserves same-process follow-up evidence while keeping audit privacy. It does
not promise source recovery after restart, native-provider token bounds, source
freshness, or improved model quality. Durable source artifacts would require a
separate retention/consent/deletion design. No public API or database migration is
introduced. Regression gates cover actual Conversation/permission/SkillRun flow,
unavailable and failed receipts, route isolation, reset/cancellation, full-result
budget omission, and unchanged absence of source plaintext in durable audit rows.
An actual two-turn regression omits a verbose assistant reply and its earlier user
entry at compilation while retaining the available original in the next request.
Compiler controls ensure a budget-omitted redacted, other-route or unknown-ID
generation still cannot supply an original. The eight-generation limit remains.
An actual source-summary regression confirms one prior READ is sufficient without
a redundant required-tool round; fresh-query and explicit-reminder controls still
expose tools. Current-user intent cases exclude search-only or missing originals
and source-body instructions. A partial 130/144 real-model batch on the preceding
implementation found all 15 available-original summary follow-ups misgated into
Runtime fallbacks. Claude quota cooldown left 14 replies missing; this incomplete
batch does not validate the subsequent intent repair or establish a persona gain.
Real-model quality and the full adoption plan remain separate acceptance gates.

On 2026-10-04, the bounded transformation intent was extended to explicit
document references such as “this document” and the Chinese equivalents of “this
notice”. A current-user source noun must occur within the bounded reference phrase;
ordinary “this program” does not qualify. Existing exclusions for a supplied URL,
fresh verification, latest material and mutations remain. Only retained successful
READ bodies select the existing text-only revision. The change neither creates
source authority nor treats a successful revision as quality approval. Deterministic
before/after checks and two actual four-turn Runtime flows confirm dispatch; both
final checklists still lose an earlier unresolved time scope, so fidelity acceptance
remains open. Evaluator 1.32.1 fingerprints the routing change.

## Rollback

Remove the read-only source port wiring and request projection. Existing audit rows,
permissions, result delivery, history and memory schemas remain compatible.
