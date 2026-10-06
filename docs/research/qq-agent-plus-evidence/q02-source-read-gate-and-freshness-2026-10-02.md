# Q02 来源读取闸门与时效发现修复

日期：2026-10-02。执行没有调用 AGY；真实模型使用当前 CW2 配置的 `chat` 密钥在进程内注入，密钥未打印、未写入证据或仓库。

## 修复

- `AgentTurnOrchestrator` 现在识别 `web.search` 的成功候选结果。搜索片段只作为发现证据，若没有成功的 `web.read` 正文，模型不能直接收尾回答。
- 搜索后模型若直接输出，运行时最多追加一次 required 的 `web.read` 纠正；没有暴露可用 reader、reader 失败、预算溢出、Provider 不可用或调用上限耗尽时，返回明确的“原文未成功读取，尚未核实”状态。
- 空结果集仍可诚实报告为空；截断或缺少 `results` 字段的搜索结果按待读取处理。
- 对包含“规定/规则/法规/公告/最新/生效”等时效意图的查询，搜索层保留原始 `query`，并生成不超过 256 字符的 `effective_query`，追加通用“最新 修订 生效”提示。结果 URL、标题或摘要含有明确日期时，按日期稳定降序排列；这只是发现排序提示，不是来源有效性证明。带明确历史年份且没有当前意图的查询不追加提示。
- `web.search` manifest 升至 1.2.0，输出契约增加 `effective_query`；来源上下文的 metadata 投影也保留它。

## 确定性验证

来源读取闸门新增测试覆盖：搜索成功后强制读取、读取缺失时只返回证据缺口、空结果处理、预算/调用边界；时效查询覆盖实际发送查询字段和 2015/2025 日期排序。相关 Runtime/Python 子集为 `149 passed`，Ruff、Pyright 和 `git diff --check` 通过。

## 真实批次

1. 原始基线目录 [`q02-jina-runtime-source-20261002`](../../../.local/research/q02/q02-jina-runtime-source-20261002/) 使用 32768 窗口、8192 输出预留、15% 余量、Jina reader。12/12 完成；DuckDuckGo 三次搜索中两次读取了 2015 CAAC 页面，一次搜索后未读取。三次法规回答和三次 Checklist 都遗漏 2025-06-28 的 3C/召回型号或批次条件。
2. 读取闸门目录 [`q02-jina-runtime-source-20261002-read-gate-v2`](../../../.local/research/q02/q02-jina-runtime-source-20261002-read-gate-v2/) 因漏传预算 JSON 使用了旧的 900 输出预留/legacy 配置，不能作为推荐预算对照。它仍显示：两次成功搜索都完成了 `web.search -> web.read`，一次搜索失败只返回未核实回退。
3. 正确冻结预算目录 [`q02-jina-runtime-source-20261002-freshness-v4`](../../../.local/research/q02/q02-jina-runtime-source-20261002-freshness-v4/) 显式使用 `recommended-budget.json`：12/12 完成，预算为 32768 窗口、8192 输出预留、15% 余量、scaled 分项；供应商 prompt/completion/reasoning/total 为 `47304/5658/8372/61334`，p50/p95 为 `9601/16514ms`。本次 3 个 t2 搜索全部收到 `web_search_challenge`，运行时均返回“尚未核实”，没有旧来源被当作已核实事实。

## 结论与限制

读取闸门已经在运行时层阻止“搜索片段直接回答”，这解决的是工具消费完整性，不等于来源内容正确。时效查询增强和日期排序已有确定性覆盖，但 DuckDuckGo Lite 的 challenge 使 v4 无法验证真实的 2025 更新排序；Firecrawl Search 没有配置 API key。Q02 仍未通过，不能把当前批次当作法规质量通过，也没有修改生产默认搜索、模型、persona、预算或数据库。真实 TTS、QQ/微信投递、播放完成 ACK 和用户 ACK 仍是独立未验收层。
