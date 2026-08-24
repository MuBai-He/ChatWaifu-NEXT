# ChatWaifu NEXT 记忆系统方案调研

- 状态：Phase 11 已选择并实现方案 A；方案 B/C 仅保留可替换端口
- 日期：2026-08-24
- 当前决策：方案 A 是持久化真值；B/C 不安装模型或数据库，只能向统一排序器贡献候选与分数

实现边界和数据流见 [结构化记忆内核](../architecture/structured-memory-kernel.md)。

## 当前基线与必须解决的问题

当前 Demo 只有明确的“请记住/请忘记”：SQLite 保存短文本，精确归一化去重，召回时取最近
记录。协议层其实已经有 `MemoryProposal`、来源事件、置信度、敏感等级、有效时间、
`superseded/contradicted/tombstoned` 状态和 `MemoryContextPacket`，但执行链路尚未实现。

无论选择哪套存储，写入都必须经过：

```text
已提交对话 -> 候选提取 -> 类型与隐私策略 -> 相关记忆检索
           -> 去重/冲突判定 -> proposal -> 审批策略 -> 事件 -> projection
```

读取都必须经过：

```text
当前问题 -> 候选召回 -> 权限/隐私过滤 -> 相关性与时效排序
         -> 去冲突 -> token budget -> 带 provenance 的 ContextPacket
```

长期记忆不能等于转录全文；没有来源证据、无法删除、已冲突或超过隐私范围的内容不能注入
模型。评估至少覆盖信息提取、跨会话推理、时间推理、知识更新和“没有证据时拒答”，这与
[LongMemEval](https://arxiv.org/abs/2410.10813) 的五类能力一致。

## 方案对比

| 方案                    | 核心表示                             | 本地复杂度 |   语义召回 | 冲突/时间 | 可解释与删除 | 适合阶段           |
| ----------------------- | ------------------------------------ | ---------: | ---------: | --------: | -----------: | ------------------ |
| A. 结构化事实 + FTS5    | typed records / episodes / events    |         低 |         中 |        高 |           高 | 现在，推荐         |
| B. A + 本地向量混合检索 | A 的真值 + embeddings 索引           |         中 |         高 |        高 |           高 | A 稳定后           |
| C. 时序知识图谱         | episode/entity/edge/community        |         高 |         高 |      很高 |         中高 | 关系推理需求明确后 |
| D. 模型管理的分层记忆   | core context + archival memory tools |       中高 | 取决于模型 |        中 |           中 | 仅作上下文编排实验 |

## 方案 A：结构化事件投影 + SQLite FTS5（推荐）

把用户偏好、身份事实、关系状态、共同经历、承诺和近期 episode 分成明确类型。记录是
event projection，来源事件是证据；更正产生 `supersede/contradict`，不原地覆盖历史。

写入建议分三档：

1. 明确“记住/忘记”继续直接进入高置信 proposal。
2. 普通对话自动提取低风险候选，默认在“记忆收件箱”提示用户，而不是静默写入。
3. 敏感信息、第三方信息、医疗/财务/认证信息永不自动写入；需要逐条确认。

去重先做确定性规则：规范化哈希、同一 subject/predicate 候选、FTS 相似候选，再让小型判定器
输出 `add/noop/supersede/contradict`。召回分数可组合 FTS `rank`、重要度、置信度、时间衰减、
命名空间匹配和最近使用惩罚。SQLite 官方 FTS5 已提供 BM25/rank，并且当前仓库已启用它，
因此这一方案没有新的数据库部署面：[SQLite FTS5](https://www.sqlite.org/fts5.html)。

优点是 local-first、可审计、容易做隐私 UI、删除和备份，并直接复用现有 ADR 0008/0010 与
协议。缺点是用户换一种说法时召回可能不足，多跳关系推理也有限。

## 方案 B：结构化真值 + 本地向量混合检索

写入、冲突和删除仍由方案 A 的 records/events 决定；向量只是可重建索引，不是 source of
truth。召回同时取 FTS、向量和 pinned/recent 候选，再用统一排序器融合。这样能找回“措辞不同
但含义相近”的偏好和共同经历。

可选实现是 SQLite 向量扩展或进程内索引。当前 Runtime 通过 `SemanticMemoryIndex` 端口接收
语义候选，默认实现为 `NullSemanticMemoryIndex`，不加载模型也不持久化向量。`sqlite-vec` 能在 SQLite 中存储和查询多种向量，
但其官方 README 明确仍是 pre-v1、可能发生破坏性变更，所以必须藏在 `EmbeddingIndexPort`
后，不能写进领域层：[sqlite-vec](https://github.com/asg017/sqlite-vec)。嵌入模型也必须走
本地 worker/provider adapter，记录 `embedding_model/version`，更换模型后可重建。任何实现都不能
绕过方案 A 的记录状态、来源、隐私和删除规则。

优点是中文口语和同义表达召回明显更稳，仍保留 SQLite 单文件体验。代价是模型下载、索引
迁移、打包原生扩展和召回漂移；应在 A 的 LongMemEval 子集和产品用例上证明收益后再默认开启。

## 方案 C：时序知识图谱

把对话 episode、人物/地点/物品实体、关系边和社区摘要分层；关系边保存 `valid_from`、
`valid_to` 与 ingestion time。新事实使旧边失效，但保留历史。Graphiti/Zep 论文采用
episode、semantic entity 和 community 三层子图，并强调连续更新与事实有效期：
[Zep/Graphiti paper](https://arxiv.org/abs/2501.13956)。

它最适合“宁宁什么时候知道某件事”“用户以前喜欢 A、现在改成 B”“共同经历涉及哪些人”这类
跨会话、多跳、时间问题。缺点是实体消歧、边抽取、图存储、删除传播和隐私治理都更复杂；对
当前单角色基础 Demo 属于过早引入。Runtime 已保留 `TemporalMemoryGraph` 端口，默认
`NullTemporalMemoryGraph`；方案 A 中的 subject/predicate、valid time 和 source edges 可在未来
无损投影成图，图结果只贡献候选，不能成为第二套记忆真值。

## 方案 D：模型管理的分层记忆

让模型像管理虚拟内存一样在核心上下文、近期消息和外部 archival memory 之间分页，并通过
工具主动搜索或改写记忆。这来自 [MemGPT](https://arxiv.org/abs/2310.08560) 的分层上下文
思路。它适合研究“角色何时主动回忆”和 context budget 编排，但不适合让模型直接成为记忆
真值或权限主体：模型可能漏写、误改或绕过隐私策略。

如果采用，只把它放在 Retrieval/Context Assembler 上层；所有写入仍必须生成
`MemoryProposal` 并经过方案 A 的 policy、provenance 和事件提交。

## 为什么不直接集成某个现成记忆 SaaS/框架

[Mem0 论文](https://arxiv.org/abs/2504.19413) 的“提取、整合、检索”路径与本项目方向一致，
并报告了相对全上下文更低的延迟和 token 成本；这些结果说明结构化持久记忆值得做，但不代表
其默认数据模型、云服务或评测数字能直接替代本项目的隐私、事件和删除语义。当前应借鉴流水线，
不把第三方框架放到领域核心。

## 已实现与后续评估门

### 推荐选择：A → 评估门 → 可选 B

1. 已完成 SQLite records、sources、proposals、FTS5 projection 与显式命令兼容路径。
2. 已完成自动候选提取、默认 `suggest`、敏感逐条确认，以及收件箱、来源、更正、置顶和忘记 UI。
3. 已完成确定性去重、subject/predicate 冲突与 supersede；歧义仍停留在 proposal。
4. 已完成 FTS + recent/importance/confidence 排序和 token-budgeted ContextPacket。
5. 下一步建立中文角色用例与 LongMemEval 五类能力子集，记录 proposal precision、false recall、更新
   正确率、拒答率和检索延迟。
6. 只有当“同义表达漏召回”成为主要错误且 B 在固定评测上显著提升，才增加可选本地 embedding。
7. 只有当多实体时序问题成为产品重点，再评估 C；不提前引入图数据库。

当前默认策略已经固定为“明确普通命令立即写入；普通对话只产生可审核建议；敏感信息即使明确
要求记住也必须逐条确认”。是否放宽成静默自动写入，应当由评测结果与独立产品设置决定，不能由
提取器自行改变。
