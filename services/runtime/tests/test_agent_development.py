"""Actual sandbox execution, disabled-by-default development and explicit activation."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from chatwaifu_protocol.agent import AgentDevelopmentPolicy, CandidateCreate
from chatwaifu_protocol.base import JsonObject
from chatwaifu_protocol.skills import SkillInvocation, SkillRunState
from chatwaifu_runtime.bootstrap.container import RuntimeContainer
from chatwaifu_runtime.config.settings import Settings
from chatwaifu_runtime.providers.contracts import (
    LlmRequest,
    LlmResponseCompleted,
    LlmStreamEvent,
    LlmToolCall,
    LlmToolCallRequested,
)


class CandidateModel:
    kind = "scripted"
    supports_tool_calling = True

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        assert request.tools[0].name == "implement_candidate"
        schema: JsonObject = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }
        yield LlmToolCallRequested(
            LlmToolCall(
                "implementation",
                "implement_candidate",
                {
                    "name": "Uppercase",
                    "description": "Convert text to uppercase.",
                    "input_schema": schema,
                    "output_schema": schema,
                    "code": "def run(arguments):\n    return {'text': arguments['text'].upper()}\n",
                    "tests": [
                        {"arguments": {"text": text}, "expected": {"text": text.upper()}}
                        for text in ["abc", "", "宁宁a"]
                    ],
                },
            )
        )
        yield LlmResponseCompleted("tool_calls")


class MalformedFirstCandidate(CandidateModel):
    def __init__(self) -> None:
        self.requests = 0

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.requests += 1
        async for event in super().stream(request):
            if isinstance(event, LlmToolCallRequested) and self.requests == 1:
                value = dict(event.call.arguments)
                value["tests"] = [{"arguments": {}, "expected_output": {}}]
                yield LlmToolCallRequested(LlmToolCall(event.call.call_id, event.call.name, value))
            else:
                yield event


async def test_candidate_sandbox_review_hash_and_activation(runtime_settings: Settings) -> None:
    container = RuntimeContainer(runtime_settings)
    await container.start()
    try:
        session = await container.sessions.create_session("default")
        service = container.agent_development
        model = MalformedFirstCandidate()
        service.model_factory = lambda: model
        request = CandidateCreate(
            session_id=session.session_id, goal="实现大小写转换能力", source_ref="owner-test"
        )
        with pytest.raises(PermissionError, match="disabled"):
            await service.create(request)
        await service.configure(AgentDevelopmentPolicy(enabled=True))
        candidate = await service.create(request)
        with pytest.raises(PermissionError, match="daily"):
            await service.create(request)
        async with asyncio.timeout(10):
            while True:
                current = await service.repository.get(candidate.candidate_id)
                assert current is not None
                if current.state not in {"queued", "developing"}:
                    break
                await asyncio.sleep(0.01)
        assert current.state == "tested", current.test_summary
        assert model.requests == 2
        assert current.artifact is not None and current.package_sha256 is not None
        assert current.plugin_id not in {s.skill_id for s in container.runtime_skills.list()}
        with pytest.raises(ValueError, match="identity"):
            await service.approve(current.candidate_id, current.revision, "0" * 64)
        # Simulate interruption after immutable installation but before CAS approval.
        await container.runtime_skills.install_plugin(service.root / current.candidate_id.hex)
        approved = await service.approve(
            current.candidate_id, current.revision, current.package_sha256
        )
        assert approved.state == "approved" and approved.plugin_id is not None
        created = await container.runtime_skills.invoke(
            session.session_id,
            SkillInvocation(
                skill_id=approved.plugin_id,
                capability="execute",
                arguments={"arguments": {"text": "Abc"}},
            ),
        )
        assert created.confirmation_request_id is not None
        await container.runtime_skills.decide_confirmation(
            created.confirmation_request_id, "allow_once"
        )
        result = await container.runtime_skills.wait_for_terminal(created.skill_run_id)
        assert result.state is SkillRunState.SUCCEEDED, result.error
        assert result.result is not None and result.result.data == {"text": "ABC"}
        detail = container.capabilities.inspect(approved.plugin_id + "/execute")
        assert detail.descriptor.status.value == "authorization_required"
        assert container.capabilities.project(
            (approved.plugin_id + "/execute",), allowed_skill_ids=frozenset({approved.plugin_id})
        )
        await container.runtime_skills.set_plugin_enabled(approved.plugin_id, False)
        assert not next(
            s for s in container.runtime_skills.list() if s.skill_id == approved.plugin_id
        ).enabled
    finally:
        await container.stop()
