"""One bounded native function result; prose never controls Runtime state."""

from uuid import uuid4

from pydantic import BaseModel

from chatwaifu_runtime.providers.contracts import (
    LlmProvider,
    LlmRequest,
    LlmResponseCompleted,
    LlmToolCallRequested,
    LlmToolDefinition,
)


async def structured_result[T: BaseModel](
    llm: LlmProvider, model: type[T], name: str, system_prompt: str, evidence: str
) -> T:
    if len(evidence.encode()) > 96_000:
        raise ValueError("decision evidence exceeds bound")
    request = LlmRequest(
        generation_id=uuid4(),
        user_text=evidence,
        system_prompt=system_prompt,
        tools=(
            LlmToolDefinition(
                name, "Return the requested structured decision.", model.model_json_schema()
            ),
        ),
        tool_choice="required",
    )
    result: T | None = None
    completed = False
    async for event in llm.stream(request):
        if isinstance(event, LlmToolCallRequested):
            if result is not None or event.call.name != name:
                raise ValueError("invalid decision tool batch")
            result = model.model_validate(event.call.arguments)
        elif isinstance(event, LlmResponseCompleted):
            completed = event.finish_reason == "tool_calls"
    if result is None or not completed:
        raise ValueError("model did not return a complete structured decision")
    return result
