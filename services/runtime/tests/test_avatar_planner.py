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
