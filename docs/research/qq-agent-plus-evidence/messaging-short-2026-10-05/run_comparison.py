# Frozen Chinese sample punctuation is retained verbatim.
# Source bootstrap must precede imports from the selected checkout.
"""Fixed-context output-contract comparison; no Runtime startup or channel sends.

Run with the deployed Runtime interpreter, candidate prompt module and output dir.
Credentials are read only on the host, never serialized. Samples are synthetic.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

SOURCE = Path("/home/mubai/chatwaifu-server/source").resolve()
STATE = Path("/home/mubai/.local/share/chatwaifu-server")
for relative in ("services/runtime/src", "packages/protocol-python/src", "packages/worker-sdk/src"):
    sys.path.insert(0, str(SOURCE / relative))

import httpx2  # noqa: E402
from chatwaifu_protocol.channels import ChannelPresentationPolicy  # noqa: E402
from chatwaifu_protocol.character import (  # noqa: E402
    AffectState,
    CharacterKernelSnapshot,
    RelationshipState,
    ResponsePlan,
)
from chatwaifu_protocol.memory import MemoryContextPacket  # noqa: E402
from chatwaifu_runtime.characters.service import CharacterService  # noqa: E402
from chatwaifu_runtime.config.settings import load_settings  # noqa: E402
from chatwaifu_runtime.conversation.models import ConversationSourceContext  # noqa: E402
from chatwaifu_runtime.external_channels.presentation import (  # noqa: E402
    InstantMessageDeliveryPlanFactory,
)
from chatwaifu_runtime.providers.contracts import (  # noqa: E402
    LlmInputBudget,
    LlmRequest,
    LlmResponseCompleted,
    LlmTextDelta,
)
from chatwaifu_runtime.providers.input_estimation import (  # noqa: E402
    estimate_reference_input_tokens,
)
from chatwaifu_runtime.providers.model_config import (  # noqa: E402
    LocalModelSecretStore,
    ModelRoleConfig,
)
from chatwaifu_runtime.providers.openai_compatible import (  # noqa: E402
    OpenAiCompatibleLlmProvider,
)

CASES = (
    ("greeting", "在吗？", "answer", "gentle", ()),
    ("tired", "今天加班到现在，累死了。", "comfort", "gentle", ()),
    ("teasing", "我又把钥匙忘家里了哈哈。", "tease", "playful", ()),
    (
        "goodbye",
        "嗯，我去睡了，晚安。",
        "answer",
        "gentle",
        (
            ("user", "今天有点累"),
            (
                "assistant",
                "下班后可以做个计划，喝水、散步，早点休息。需要的话我帮你整理明天的安排。",
            ),
        ),
    ),
    (
        "no_advice",
        "今天心情有点差，别给建议，也别问原因，陪我聊两句就好。",
        "comfort",
        "gentle",
        (),
    ),
    (
        "detailed_code",
        (
            "详细写一个 Python asyncio 例子：主"  # noqa: RUF001
            "任务用 TaskGroup 同时运行两个子任务，子"
            "任务 A 抛 ValueError，子任务 B 等"
            "待时会被取消。展示可运行完整代码，捕获异常组；解释"  # noqa: RUF001
            "取消和资源清理为什么放在 finally，不能吞 "
            "CancelledError。"
        ),
        "answer",
        "serious",
        (),
    ),
)


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


class Models:
    def __init__(self, config):
        self.config = config

    def get(self, role):
        assert role == "chat"
        return self.config

    async def complete(self, *_args, **_kwargs):
        raise AssertionError("Fixed short fixtures must not need a summary model")


class Recorder(httpx2.AsyncBaseTransport):
    def __init__(self):
        self.delegate = httpx2.AsyncHTTPTransport()
        self.attempts = []

    async def handle_async_request(self, request):
        attempt = {"payload": json.loads(request.content)}
        self.attempts.append(attempt)
        response = await self.delegate.handle_async_request(request)
        attempt["http_status"] = response.status_code
        return response

    async def aclose(self):
        await self.delegate.aclose()


async def main(args):
    out = Path(args.output)
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    assert not (out / "results.jsonl").exists(), "Do not duplicate an existing run"
    with sqlite3.connect(f"file:{STATE / 'data/chatwaifu.db'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute("SELECT * FROM model_role_configs WHERE role='chat'").fetchone())
    config = ModelRoleConfig.model_validate(
        {
            **{
                key: row[key]
                for key in (
                    "role",
                    "provider",
                    "model",
                    "base_url",
                    "timeout_seconds",
                    "context_window",
                    "updated_at",
                )
            },
            "enabled": bool(row["enabled"]),
            "budget": json.loads(row["budget_json"]),
        }
    )
    assert config.model == "gemini-3.8-flash-high"
    assert config.base_url == "https://mubai.website:8318/v1"
    settings = load_settings(STATE / "runtime.toml", STATE / "server.env")
    key = LocalModelSecretStore(settings.config_dir / "model-secrets.json").get("chat")
    assert key, "Configured key required on the server"
    baseline_path = SOURCE / "services/runtime/src/chatwaifu_runtime/character_kernel/prompt.py"
    compilers = {
        "baseline": module(baseline_path, "message_baseline").PromptCompiler(Models(config)),
        "candidate": module(Path(args.candidate), "message_candidate").PromptCompiler(
            Models(config)
        ),
    }
    characters = CharacterService(SOURCE / "characters")
    characters.start()
    character = characters.get("default")
    now = datetime(2026, 10, 5, tzinfo=UTC)
    kernel = CharacterKernelSnapshot(
        character_id="default",
        user_scope="local",
        revision=1,
        affect=AffectState(updated_at=now),
        relationship=RelationshipState(stage="familiar", interaction_count=40, updated_at=now),
    )
    source_context = ConversationSourceContext(
        provider_id="qq_napcat",
        connection_id=UUID(int=1),
        principal_scope="local",
        account_key="900",
        chat_type="direct",
        conversation_key="fixture-chat",
        sender_key="999",
    )
    common = dict(
        character=character,
        kernel=kernel,
        memory=MemoryContextPacket(token_budget_used=0),
        as_of=now,
        source_context=source_context,
    )
    ordinary = ResponsePlan(
        intent="answer", tone="gentle", expression="neutral", rationale="fixed comparison"
    )
    isolation = {}
    for origin in ("local_text", "voice", "proactive"):
        before = await compilers["baseline"].compile(
            **common,
            plan=ordinary,
            history=(),
            user_text="在吗？",
            presentation_profile=None,
        )
        after = await compilers["candidate"].compile(
            **common,
            plan=ordinary,
            history=(),
            user_text="在吗？",
            presentation_profile=None,
            conversation_origin=origin,
        )
        assert asdict(before) == asdict(after), f"Unexpected local change: {origin}"
        isolation[origin] = {
            "byte_equal": True,
            "sha256": hashlib.sha256(after.system_prompt.encode()).hexdigest(),
        }
    manifest = {
        "model": config.model,
        "context_window": config.context_window,
        "budget": config.budget.model_dump(mode="json"),
        "source": str(SOURCE),
        "baseline_prompt_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        "candidate_prompt_sha256": hashlib.sha256(
            await asyncio.to_thread(Path(args.candidate).read_bytes)
        ).hexdigest(),
        "persona_sha256": hashlib.sha256(
            (SOURCE / "characters/default/persona.md").read_bytes()
        ).hexdigest(),
        "fixed_time": now.isoformat(),
        "repeats": 2,
        "cases": CASES,
        "isolation": isolation,
        "scope": (
            "compiled fixed output-con"
            "tract answer comparison; "
            "no tools, Runtime fallbac"
            "k, live channel send or p"
            "layback"
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    policy = ChannelPresentationPolicy(
        profile="instant_message", preferred_chars_per_part=30, soft_max_chars_per_part=60
    )
    factory = InstantMessageDeliveryPlanFactory()

    async def sample(arm, case, repeat):
        case_id, user_text, intent, tone, history = case
        plan = ResponsePlan(
            intent=intent, tone=tone, expression="neutral", rationale="fixed comparison"
        )
        kwargs = {"conversation_origin": "external_channel"} if arm == "candidate" else {}
        compilation = await compilers[arm].compile(
            **common,
            plan=plan,
            history=history,
            user_text=user_text,
            presentation_profile="instant_message",
            **kwargs,
        )
        request = LlmRequest(
            generation_id=uuid4(),
            user_text=user_text,
            system_prompt=compilation.system_prompt,
            character_name=character.display_name,
            context=compilation.context,
            history=compilation.history,
            pre_user_system_prompt=compilation.pre_user_system_prompt,
            input_budget=LlmInputBudget(compilation.report.budget),
            max_output_tokens=config.budget.max_output_tokens,
        )
        assert estimate_reference_input_tokens(request) <= compilation.report.budget
        recorder = Recorder()
        provider = OpenAiCompatibleLlmProvider(
            base_url=config.base_url,
            model=config.model,
            api_key=key,
            timeout_seconds=config.timeout_seconds,
            transport=recorder,
            request_usage=True,
        )
        result = {
            "case": case_id,
            "repeat": repeat,
            "arm": arm,
            "prompt_report": compilation.report.model_dump(mode="json"),
            "wire_reference_tokens": estimate_reference_input_tokens(request),
        }
        chunks = []
        completed = None
        start = monotonic()
        try:
            async for event in provider.stream(request):
                if isinstance(event, LlmTextDelta):
                    chunks.append(event.text)
                elif isinstance(event, LlmResponseCompleted):
                    completed = event
            result["status"] = (
                "model_reply" if completed and chunks else "incomplete_model_response"
            )
        except Exception as error:
            result["status"] = "provider_error"
            result["error_type"] = type(error).__name__
        result.update(
            {
                "latency_ms": round((monotonic() - start) * 1000),
                "reply": "".join(chunks),
                "attempts": recorder.attempts,
                "completion": asdict(completed) if completed else None,
            }
        )
        parts = (
            factory.create_parts(result["reply"], policy=policy) if result["reply"].strip() else ()
        )
        result["simulated_parts"] = [part.model_dump(mode="json") for part in parts]
        encoded = json.dumps(result, ensure_ascii=False)
        assert key not in encoded, "Secret must not enter evidence"
        with (out / "results.jsonl").open("a") as stream:
            stream.write(encoded + "\n")
        print(
            json.dumps(
                {
                    "case": case_id,
                    "arm": arm,
                    "repeat": repeat,
                    "status": result["status"],
                    "latency_ms": result["latency_ms"],
                }
            ),
            flush=True,
        )

    for repeat in (1, 2):
        for case in CASES:
            arms = ("baseline", "candidate") if repeat == 1 else ("candidate", "baseline")
            await asyncio.gather(*(sample(arm, case, repeat) for arm in arms))
    print("COMPLETED: 24 synthetic model samples; no channel delivery", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
