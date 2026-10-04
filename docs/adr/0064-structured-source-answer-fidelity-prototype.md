# ADR 0064: Structured source-answer fidelity prototype

Status: Proposed; isolated evaluation only

## Evidence and scope

The Q02 ledger records complete source bodies and complete previous answers on
the wire, yet both final AirChina checklists lose the previously stated unresolved
time scope. Existing one-pass revision and a single-factor removal of its finish
clause also fail. This excludes missing input in those samples; it does not prove
one universal model or prompt cause. READ receipts establish retrieval and have no
typed representation of an answer's unresolved coverage.

Two bounded requests to the configured Gemini 3.8 Flash High endpoint returned
valid declared JSON Schema envelopes. This is observed compatibility, not a
general guarantee of enforcement or semantic correctness.

Before changing conversation lifecycle or provider contracts, test a pure Agent
answer frame in isolation. It represents final prose blocks, their references to
actual source identities, and separately declared unresolved evidence gaps. A
source identity binds the actual URL and body hash. Gap text remains model output,
never a verified fact, memory, instruction, permission or operation.

## Prototype design

- Versioned, immutable local frame contracts; strict bounded JSON parsing with
  fixed error codes. No parsing of arbitrary assistant prose into system state.
- No new facts, aviation thresholds, persona variants or task-specific checklist
  patches. The only new model instruction describes the frame serialization.
- Render newly declared gaps in their selected blocks. On a same-source
  transformation, retain prior gap statements associated with the source identities
  actually used. The model may choose placement; missing placement uses the last
  relevant block. The renderer never treats silence as resolution.
- Freeze supplied documents and already completed first/second-turn history. Run
  the third/fourth-turn pair with actual rendered third-turn output in the fourth
  wire. Preserve raw envelope, rendered reply, retained gaps, actual payload,
  usage, latency, errors and omission audit. This is a diagnostic control, not a
  full four-turn, live-retrieval or role A/B gate.
- Do not dispatch tools, touch production services or add persistence. Bounded
  prototype state lives inside one evaluator invocation. Cancellation propagates;
  invalid/nonterminal frames are rejected before publishing any draft.
- Count the actual `response_format` schema and frame data in wire estimates;
  keep provider usage distinct from the reference estimate and unknown fields.

The Agent owns frame validation and rendering; the evaluator owns this temporary
state. If the control warrants implementation, Conversation must own a bounded
completed-generation store selected by the existing same-route, redaction and
completion fences. Runtime Skills continues to own READ results. Provider adapters
would then map a neutral declared response schema and reject unsupported requests.
That complete lifecycle slice is not implemented or accepted by this ADR.

## Gates and rollback

First validate strict parsing, source identities, placement, non-resolution,
boundedness and the absence of source-less carry. Then directly review every raw
and rendered answer against the frozen originals, the current task and prior
scope; JSON validity, retained fields or keywords do not approve quality. If useful,
implement and test the lifecycle slice before repeating the complete frozen group.
Only after the source and role prerequisites pass may the original full 864-reply
A/B begin. Existing accepted channel/playback evidence remains valid.

Remove the isolated evaluator and pure frame module to roll back. Production
behavior, configuration, database, permissions and existing receipts are unchanged.

## Observed control result

The 1.0 control completed two third/fourth-turn pairs after unchanged previously
completed prefixes. Both rendered fourth turns retained the third-turn gap, but
one merged the requested list into a single paragraph. Version 1.1 adds explicit
paragraph/list-item kinds; the renderer owns numbering and continuation indentation.
It still leaves factual prose to the model. Both layout controls preserve the gap
and list, yet direct review finds a source-relation ambiguity in one and expanded
charging-prohibition scope in the other. One existing revision per draft leaves
the relation ambiguity in one response and produces one complete response.

All ten HTTP attempts, including two schema capability checks, are complete. The
prototype is **not adopted**. Fifteen local/server software checks do not approve
semantic quality. See the single Q02 ledger for accounting, all visible replies,
source comparisons, limitations and the next dependency. Before adding a completed-
generation store or enabling this contract, investigate an explicit binding between
the final claims and the original source conditions. Merely retaining gap text
cannot fix unsupported factual paraphrases.

## Source-unit binding investigation

The next isolated contract removes free factual body text from document-reformat
blocks. It exposes a lossless line-unit view of each available original and lets
the model select source/unit indices and short organizational headings. The Agent
renderer obtains each selected full unit from the actual snapshot, retains its
wording, adds the actual source URL, and uses the existing gap-rendering guards.
It does not repair model text with aviation keywords or infer formal logic from
Chinese conjunctions. Source content remains untrusted quoted data.

All original characters, including blank lines, remain recoverable from the unit
view; nothing is clipped or summarized to meet a token threshold. Invalid indices,
empty quotes, extra body fields and oversized frames are rejected. The prototype
is only a reformatting investigation. Selection coverage, misleading headings,
source-language readability, voice/role behavior and source applicability still
require direct review. Literal binding is not proof that the model understood or
selected all necessary conditions. Do not enable this contract merely because
selected words are identical to a source. Keep the configured endpoint/model,
frozen material and prior history for the directional control, then test both
frozen documents before considering lifecycle integration.

## Source-unit control result

Version 2.0 completed two AirChina and two ICAO fourth-turn controls, preserving
the actual completed prefixes, entire originals and declared output budget. The
AirChina controls reused actual structured third-turn gaps. The ICAO prefixes had
only ordinary third-turn prose, so their typed gap list was empty; no gap state was
invented by parsing that prose. These are not new complete four-turn Runtime flows.

The renderer reproduced the selected original wording in all four controls. Both
AirChina answers kept their earlier scope gap and all substantive conditions, yet
also selected a large unrelated website navigation row. ICAO answers reproduced
long English paragraphs with little Chinese explanation; one repeated the core
paragraph, and the other did not explicitly retain the previously specified date
scope. Whole line selection preserved words but failed to produce a concise,
useful transformation. A line is not guaranteed to be a complete semantic clause.

The added catalog and schema cost 1,141 reference tokens for each AirChina wire and
6,433 for each ICAO wire. All four requests fit the unchanged experimental budget;
no source or history was omitted. That does not establish either superior answer
quality or a need to expand the production window. Supplier internals and model
labels remain unverified.

Author review rates all four answers partial. **Version 2.0 is not adopted.** Keep
the useful gap-retention mechanism and original-reference audit separate from a
quote-only product response. Do not add another persona variant or silently strip
selected source units to make this rejected control appear successful. Before any
full role A/B, a completed-generation lifecycle slice must preserve natural
language presentation as well as explicit unresolved coverage, and its complete
four-turn frozen-source replies must be reviewed. Exact quotation, state fields
and software tests cannot substitute for that gate.
