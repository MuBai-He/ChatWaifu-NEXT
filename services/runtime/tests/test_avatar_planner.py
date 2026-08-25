from uuid import uuid4

from chatwaifu_protocol.character import ResponsePlan
from chatwaifu_runtime.avatar.planner import SemanticAvatarCuePlanner


def test_planner_uses_semantic_nene_capabilities_for_explicit_intent() -> None:
    planner = SemanticAvatarCuePlanner()

    cues = planner.plan_user_turn("宁宁，你可以唱一首歌吗\uff1f")

    assert [(cue.kind, cue.name) for cue in cues] == [
        ("expression", "curious"),
        ("motion", "sing"),
    ]
    assert cues[1].duration_ms == 8_000


def test_planner_prefers_emotional_safety_signal_and_ignores_ambiguous_turns() -> None:
    planner = SemanticAvatarCuePlanner()

    assert planner.plan_user_turn("我今天有点难过")[0].name == "sad"
    assert planner.plan_user_turn("继续吧") == ()


def test_response_plan_stays_within_manifest_and_suppresses_consecutive_motion() -> None:
    planner = SemanticAvatarCuePlanner()
    session_id = uuid4()
    plan = ResponsePlan(
        intent="reassure",
        tone="shy",
        expression="shy",
        motion="headpat",
        rationale="test",
    )
    capabilities = {"expressions": ["neutral", "shy"], "motions": ["headpat"]}

    first = planner.plan_response(session_id, plan, capabilities)
    second = planner.plan_response(session_id, plan, capabilities)

    assert [(cue.kind, cue.name) for cue in first] == [
        ("expression", "shy"),
        ("motion", "headpat"),
    ]
    assert [(cue.kind, cue.name) for cue in second] == [("expression", "shy")]

    unsupported = planner.plan_response(
        uuid4(),
        plan.model_copy(update={"expression": "angry", "motion": "sing"}),
        capabilities,
    )
    assert [(cue.kind, cue.name) for cue in unsupported] == [("expression", "neutral")]
