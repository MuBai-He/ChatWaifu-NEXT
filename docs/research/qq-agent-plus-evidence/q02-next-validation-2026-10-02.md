# Q02 下一轮收尾验证

日期：2026-10-02。执行约束：不调用 AGY，由主代理直接审查和测试。

## 本次执行

本次在工作树 `mubai/qq-agent-plus-implementation`、提交 `e6093459ffa5105892e61172af5ca1804dd89a9b` 上完成了方案要求的确定性验证：

```text
uv run pytest services/runtime/tests/test_photo_annotations.py \
  services/runtime/tests/test_inbound_multi_image.py \
  services/runtime/tests/test_photo_memory_repository.py \
  services/runtime/tests/test_photo_memory_semantic.py \
  tools/tests/test_evaluate_character_scenarios.py \
  services/runtime/tests/test_runtime_source_evaluation.py \
  services/runtime/tests/test_tool_intent.py \
  services/runtime/tests/test_character_kernel.py \
  services/runtime/tests/test_model_context_budget.py
219 passed, 1 existing audioop deprecation warning
```

```text
pnpm --filter @chatwaifu/web test
52 test files, 313 tests passed
```

`git diff --check` 通过。未发现生产 persona、生产配置、生产数据库或凭据被本次验证写入。

照片注释当前状态：Runtime 只接受来自本轮用户原文的连续 quote，置信度低于 0.9、候选不在当前照片集合或引用不明确时拒绝；明确更正通过 `replaces_id` 让旧注释进入 `superseded`。SQLite 已覆盖幂等、revision fence、删除后拒绝、重启可读和注释数量变化时的语义索引失效保护；Web 详情只显示活动注释。当前没有手动编辑注释的产品入口，因此“自动注释链路通过”不等于“手动编辑功能已验收”。

## 当前端点状态

本次最初只探测了本机回环地址，因而错误得到“端点不可达”。修正为当前配置的 `https://mubai.website:8318/v1` 并在进程内使用已有本地聊天密钥后，`GET /models` 返回 `200`，模型清单和后续真实复测均正常。修正端点后的批次见[修正端点定向复测](q02-next-corrected-endpoint-2026-10-02.md)。

## 结论边界

本次确定性测试证明照片注释、来源投影、工具意图、角色 PromptCompiler、模型预算和 Web 注释界面没有引入测试可见回归。修正端点后新增真实样本已覆盖技术题、充电宝法规、Raft 和三种展示的 native write 拒绝，但不改变结论：Q02 仍未通过；充电宝规则完整性、Raft 参数表述、`default_voice` 的真实 TTS/投递/播放 ACK 仍未满足放行条件。
