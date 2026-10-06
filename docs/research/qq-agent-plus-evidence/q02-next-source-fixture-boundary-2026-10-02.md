# Q02 来源夹具与 Runtime 工具边界修复

日期：2026-10-02。执行约束：不调用 AGY，由主代理直接实现、审查和测试。

## 修改

评测器升级到 `1.16.0`，将两类来源证据路径明确分开：

- 直接 Provider 对照路径继续把 fixture `source_snapshot` 作为当前轮的 bounded、untrusted source evidence 投影到 Prompt，用来回答“资料已经进入请求后模型是否保留条件”。
- `--runtime-source-tools` 路径不再注入 fixture 快照。该路径只能通过实际的 `web.search`/`web.read` Runtime Skill 获取来源，避免夹具事实让模型跳过工具调用，也避免把预置资料误记成真实工具证据。
- dry-run 的 `prompt_estimate_scope` 和 metadata `source_snapshot_projection` 会标明当前路径是否禁用 fixture 快照，方便恢复和审计时识别混用。

## 回归证据

新增 `test_runtime_source_mode_does_not_preload_fixture_source_snapshot`：

- direct-provider 对照的 `detailed_answer` 第 2 轮请求包含 `[SUPPLIED SOURCE EVIDENCE]`、`2025-06-28` 和来源快照；
- Runtime source-tool 模式的同一请求不含 fixture 快照；即使受控 Provider 不支持工具，也不会把预置来源冒充工具结果。

本次通过：

```text
uv run pytest tools/tests/test_evaluate_character_scenarios.py -q
52 passed

uv run pytest services/runtime/tests/test_character_kernel.py \
  services/runtime/tests/test_runtime_source_evaluation.py \
  services/runtime/tests/test_tool_calling_agent.py -q
103 passed, 1 existing audioop deprecation warning

uv run ruff check \
  services/runtime/src/chatwaifu_runtime/character_kernel/prompt.py \
  services/runtime/tests/test_character_kernel.py \
  tools/evaluate_character_scenarios.py \
  tools/tests/test_evaluate_character_scenarios.py
All checks passed

uv run pyright \
  services/runtime/src/chatwaifu_runtime/character_kernel/prompt.py \
  tools/evaluate_character_scenarios.py
0 errors

git diff --check
passed
```

这次修改没有触碰默认 persona、生产模型、生产预算或生产数据库。它修复的是评测证据归因，不等于 Q02 发布放行；真实 TTS、QQ/微信投递、物理播放完成 ACK 和用户 ACK 仍需独立设备/凭据验收，真实模型来源批次也应在新评测器版本下重新执行后再比较。
