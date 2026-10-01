# Q01 评测关系状态修复

2026-09-30。修复前夹具只声明 familiar，实际计数与数值仍为初识默认值。请求前的真实 reducer 会重算阶段：主代理独立复现全部12组48轮，均为 acquaintance，包括七组标为熟悉的场景。旧回复、usage和延迟继续保留；熟悉阶段的质量结论需重新评测。离线复原不等于历史请求已记录状态。

修复为七组熟悉场景设置 count=6、familiarity=0.38、trust/affinity/comfort=0.35，五组初识场景显式使用原默认指标。夹具只保留 relationship_stage 和 interaction_count 这组规范字段。用户文本、预期、禁止行为、合成记忆、历史和展示配置均未改变；生产 reducer、关系门槛、persona和PromptCompiler未改。

运行器1.2.0在调用模型前校验所有选中场景，使用真实关系策略拒绝不一致、缺失或无效种子；有效初识旧夹具仍可读。每条新结果包含评测专用快照1.0：强类型 CharacterKernelSnapshot 和 ResponsePlan，经校验后序列化，记录实际编译输入。未知版本、非法指标、无时区时间和额外字段不能作为有效已完成记录续跑。历史记录缺少快照时显示未记录，不回填。盲表展示每轮实际阶段、计数和计划。

主代理直接补齐快照类型与记录校验、删除重复状态表示，并独立验证：

- 73项定向测试通过，包括全部场景轨迹、后置无效场景导致零模型调用、实际编译输入、A/B与重复状态一致性、续跑一致性、版本隔离和旧记录可读性。
- 独立Demo运行288条，所有sample_key唯一；168条熟悉、120条初识。每条完整状态与计划都等于独立使用生产reducer计算的48轮预期值。Demo只验证评测机制。
- Ruff与定向Pyright通过；全仓回归另见实施状态。

原夹具字节存档见[修复前夹具](character-scenarios-pre-seed-fix-2026-09-30.json.txt)，SHA前16位9c5a731b5d92559e；新夹具为1a2281443a1c0fc5。persona为3ab060352dcb8e25，编译器为7c85a6ef6515d89c。核对结果见[Demo状态验证](character-state-demo-validation-2026-09-30.json)，修复前轨迹见[离线审计](character-scenario-stage-audit-before-fix-2026-09-30.json)。

真实模型需使用新夹具重新运行，保留实际输入快照再复核。此修复不批准角色质量，也不改变历史回复或原始评分。
