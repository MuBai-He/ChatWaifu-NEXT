"""Budget complete tool inputs without rewriting task, source or action facts.

The character compiler's character-count heuristic is an estimate. This module
extends it to tool schemas, exchanges and message overhead; it does not pretend
to implement any provider's tokenizer. Image input reserves are estimates too.
"""

from __future__ import annotations

import json
from dataclasses import replace

from chatwaifu_runtime.providers.contracts import LlmInputBudgetReport, LlmRequest

_MESSAGE_TOKENS = 16
_IMAGE_TOKENS = 1024
_PREAMBLE_OMITTED = (
    "[Runtime omitted the model's pre-tool narrative for the input budget. "
    "The executed calls and results follow unchanged.]"
)


class InputBudgetExceeded(RuntimeError):
    def __init__(self, report: LlmInputBudgetReport) -> None:
        super().__init__("mandatory tool input exceeds its estimated budget")
        self.report = report


def _tokens(text: str) -> int:
    return (len(text) + 1) // 2


def _json_tokens(value: object) -> int:
    return _tokens(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def estimate_input_tokens(request: LlmRequest) -> int:
    """Include every transmitted text, schema, argument and result once."""
    messages = 2 + len(request.context) + len(request.history)
    used = _tokens(request.system_prompt) + _tokens(request.user_text)
    used += sum(_tokens(text) for _role, text in (*request.context, *request.history))
    for exchange in request.tool_exchanges:
        messages += 1 + len(exchange.results)
        used += _tokens(exchange.assistant_text)
        used += _json_tokens(
            [
                {"id": call.call_id, "name": call.name, "arguments": call.arguments}
                for call in exchange.calls
            ]
        )
        for result in exchange.results:
            used += _tokens(result.call_id) + _json_tokens(result.content)
    if request.tools:
        used += _json_tokens(
            [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                }
                for tool in request.tools
            ]
        ) + 16 * len(request.tools)
    return used + _MESSAGE_TOKENS * messages + _IMAGE_TOKENS * len(request.images)


def fit_input_budget(request: LlmRequest) -> LlmRequest:
    """Preserve complete results, current user input and latest prior user turn.

    Replace older history in place so source-ledger indices retain their meaning.
    Never clip source bodies, arguments, errors, permission outcomes, or schemas.
    The orchestrator can close its tool phase if schemas no longer fit; mandatory
    input overflow is explicit rather than silently deleting evidence.
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
        saving = _tokens(exchange.assistant_text) - _tokens(_PREAMBLE_OMITTED)
        if saving > 0:
            exchanges[index] = replace(exchange, assistant_text=_PREAMBLE_OMITTED)
            used -= saving
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
        if used <= limit:
            break
        role, text = history[index]
        marker = (
            f"[Runtime omitted an earlier {role} message for the input budget. "
            "Its details are unavailable; do not reconstruct them.]"
        )
        saving = _tokens(text) - _tokens(marker)
        if saving > 0:
            history[index] = role, marker
            used -= saving
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
        estimated_image_tokens=_IMAGE_TOKENS * len(request.images),
    )
    if report.estimated_input_tokens > limit:
        raise InputBudgetExceeded(report)
    return replace(fitted, input_budget_report=report)
