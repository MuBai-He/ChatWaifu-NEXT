"""Resolve configured model budgets without guessing endpoint capabilities."""

import math
from dataclasses import dataclass

from chatwaifu_protocol.character import ModelContextBudget


@dataclass(frozen=True, slots=True)
class ResolvedContextBudget:
    input_tokens: int
    persona: int
    memory: int
    history: int
    summary: int
    photo: int
    source_ledger: int
    retrieval_characters: int


def resolve_context_budget(window: int, budget: ModelContextBudget) -> ResolvedContextBudget:
    available = window - budget.output_reserve_tokens
    if budget.input_token_limit is not None:
        available = min(available, budget.input_token_limit)
    total = math.floor(available / (1 + budget.estimate_margin_ratio))
    if total < 1:
        raise ValueError("model configuration leaves no estimated input budget")
    scaled = budget.section_policy == "scaled"
    persona = max(700, total * 18 // 100)
    memory = max(300, total * 16 // 100)
    history = max(700, total * 34 // 100)
    if not scaled:
        persona, memory, history = min(1800, persona), min(1400, memory), min(3600, history)
    return ResolvedContextBudget(
        input_tokens=total,
        persona=persona,
        memory=memory,
        history=history,
        summary=max(700, total * 8 // 100) if scaled else 700,
        photo=max(250, total // 12) if scaled else min(1000, max(250, total // 12)),
        source_ledger=max(600, total // 10) if scaled else min(1200, max(600, total // 10)),
        # The retriever currently measures characters, not native tokens. Match
        # its units explicitly; privacy/ranking and candidate limits still apply.
        retrieval_characters=max(700, memory) if scaled else 700,
    )
