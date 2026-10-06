"""Budget complete inputs without rewriting task, source or action facts.

Use the Provider domain's bundled chat-wire reference for complete requests.
This is an estimate, not a native provider tokenizer or universal upper bound.
"""

from __future__ import annotations

from dataclasses import replace

from chatwaifu_runtime.providers.contracts import LlmInputBudgetReport, LlmRequest
from chatwaifu_runtime.providers.input_estimation import (
    ESTIMATED_IMAGE_TOKENS,
    estimate_reference_input_tokens,
)

_PREAMBLE_OMITTED = (
    "[Runtime omitted the model's pre-tool narrative for the input budget. "
    "The executed calls and results follow unchanged.]"
)


class InputBudgetExceeded(RuntimeError):
    def __init__(self, report: LlmInputBudgetReport) -> None:
        super().__init__("mandatory input exceeds its estimated budget")
        self.report = report


def estimate_input_tokens(request: LlmRequest) -> int:
    """Include every transmitted text, schema, argument and result once."""
    return estimate_reference_input_tokens(request)


def fit_input_budget(
    request: LlmRequest, *, protected_history_indices: tuple[int, ...] = ()
) -> LlmRequest:
    """Preserve complete results, current user input and latest prior user turn.

    Replace older history in place so source-ledger indices retain their meaning.
    Never clip source bodies, arguments, errors, permission outcomes, or schemas.
    The orchestrator can close its tool phase if schemas no longer fit; mandatory
    input overflow is explicit rather than silently deleting evidence. A caller
    selecting optional prior bodies can reserve relevant history first; that
    history remains ordinary assistant prose, never authoritative source data.
    """
    if request.input_budget is None:
        return request
    limit = request.input_budget.estimated_token_limit
    original = used = estimate_input_tokens(request)
    history = list(request.history)
    exchanges = list(request.tool_exchanges)
    previous = request.input_budget_report
    omitted_history = set(previous.omitted_history_indices if previous else ())
    omitted_preambles = set(previous.omitted_tool_preamble_indices if previous else ())

    for index, exchange in enumerate(exchanges):
        if used <= limit:
            break
        candidate = list(exchanges)
        candidate[index] = replace(exchange, assistant_text=_PREAMBLE_OMITTED)
        candidate_used = estimate_input_tokens(
            replace(request, history=tuple(history), tool_exchanges=tuple(candidate))
        )
        if candidate_used < used:
            exchanges = candidate
            used = candidate_used
            omitted_preambles.add(index)

    latest_user = next(
        (index for index in range(len(history) - 1, -1, -1) if history[index][0] == "user"),
        None,
    )
    # Assistant prose is lower priority than supplied user facts. Keep current
    # context/memory and the last prior user request, even when they cannot fit.
    order = [index for index, (role, _) in enumerate(history) if role == "assistant"]
    order += [
        index for index, (role, _) in enumerate(history) if role == "user" and index != latest_user
    ]
    for index in order:
        if index in protected_history_indices:
            continue
        if used <= limit:
            break
        role, _text = history[index]
        marker = (
            f"[Runtime omitted an earlier {role} message for the input budget. "
            "Its details are unavailable; do not reconstruct them.]"
        )
        candidate_history = list(history)
        candidate_history[index] = role, marker
        candidate_used = estimate_input_tokens(
            replace(request, history=tuple(candidate_history), tool_exchanges=tuple(exchanges))
        )
        if candidate_used < used:
            history = candidate_history
            used = candidate_used
            omitted_history.add(index)

    fitted = replace(request, history=tuple(history), tool_exchanges=tuple(exchanges))
    report = LlmInputBudgetReport(
        estimated_token_limit=limit,
        estimated_original_tokens=max(original, previous.estimated_original_tokens)
        if previous
        else original,
        estimated_input_tokens=estimate_input_tokens(fitted),
        omitted_history_indices=tuple(sorted(omitted_history)),
        omitted_tool_preamble_indices=tuple(sorted(omitted_preambles)),
        estimated_image_tokens=ESTIMATED_IMAGE_TOKENS * len(request.images),
    )
    if report.estimated_input_tokens > limit:
        raise InputBudgetExceeded(report)
    return replace(fitted, input_budget_report=report)
