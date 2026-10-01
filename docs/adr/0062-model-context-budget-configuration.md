# ADR 0062: Explicit model context budgets

- Status: Accepted
- Date: 2026-10-02

The previous chat allowance came from a default 8192 window minus a fixed 900,
while upstream character, memory, history and tool projections had independent
caps. Focused controlled comparisons show that increasing the final allowance
can recover omitted facts, but increasing only the window cannot recover facts
already removed upstream. They do not establish general quality acceptance.

Each role's selected provider/model/endpoint stores a versioned `ModelContextBudget`.
It separates an optional configured model input limit, optional model output limit,
operational combined window, output reservation, requested output cap, reference
estimation margin, section policy, local history count, memory candidate count and
tool result byte limit. Model name alone never establishes proxy capability.

The effective complete-request reference allowance is
`floor(min(context_window - output_reserve_tokens, input_token_limit) / (1 + margin))`,
omitting the second bound when unknown. An exhausted allowance is invalid; there
is no artificial minimum that grants input beyond the configured window.
Requested output must fit its reservation and configured model output limit.
The adapter sends the request cap; a proxy can ignore it. Tests against the
owner's configured endpoint did ignore both tested output-cap parameter names.
The reservation is therefore an operating policy, not a provider-enforced guarantee.

`legacy` retains previous section caps. `scaled` removes their fixed ceilings and
allocates character 18%, memory 16%, history 34%, summary 8%, photo 1/12 and source
ledger 1/10, retaining the existing small-section minimums. Final wire fitting
still counts all messages, schemas, results and wrappers; allocation estimates
alone never authorize dispatch. Memory retrieval receives a character allowance
derived from its allocation and a separately configured candidate count. Its
privacy, relevance, state, namespace and pinned/recent retrieval policies remain
independent. Local history count is configurable; the existing same-scope
cross-surface ledger can add up to 12 records. Web-read extraction and source
receipt count/visibility caps remain explicit independent limits.

The full immutable budget enters generation admission and nonsecret route identity.
Changing budget or route affects the next generation, not a request waiting on
retrieval. Default budget values preserve historical route-hash construction;
template v12 identifies the new compiler. Reports identify the compiler's
half-character estimator, allocation limits and selected-section omissions.
The final input report remains the offline cl100k chat-JSON reference. Neither
is a native tokenizer or a universal upper bound. Provider-reported usage remains
separate; raw private contents are not logged by these diagnostics.

Migration 36 adds `budget_json` with compatible defaults; existing operational
windows are never increased automatically. An older client omitting budget while
saving the same route preserves it. Omitting budget when selecting a different
provider, model or endpoint resets to unspecified-capability legacy defaults.
The shared settings UI clears inherited budget settings when editing a route.
Explicit supplied budgets are validated as part of the new route configuration.
Memory summary/extraction use their own configured input guard and output cap;
oversized mandatory evidence fails explicitly rather than being silently sliced.

The scenario evaluator accepts `--context-window` and `--model-budget-json`, uses
them for dry-run and execution, and rejects mixed-budget resume. Evaluator 1.14.0
keeps previous result identities immutable. Hardware, live channels, owner data,
default persona and unrelated CI policy are outside this slice.

See the [investigation, limitations and pre-implementation plan](../research/qq-agent-plus-evidence/q02-model-budget-investigation-2026-10-01.md).
