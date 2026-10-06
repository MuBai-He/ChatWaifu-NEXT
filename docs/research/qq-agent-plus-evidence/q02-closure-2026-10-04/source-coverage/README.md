# 有界未核实状态整链候选：最终冻结结论

执行顺序与四类阻塞只维护在[统一台账](../../q02-closure-ledger-2026-10-04.md)。
本阶段冻结为 **v37 + 原 closure-c1 / SourceAnswerFrame 1.1**，结论为**不采用，Q02 未通过**。
它在隔离 Provider → Agent → Conversation 中完成了四组真实四轮，但全文最终审查
**14 完整 / 2 部分符合**，不能推进角色定向或新 864 回复完整 A/B。没有新增 persona
规则、改写样本或替换失败行；实现与审查均由主代理直接完成，未使用 AGY。

## 四类证据与归属

| 类别 | 本阶段证据 | 最终状态与边界 |
| --- | --- | --- |
| Runtime / Provider | 16/16 实际 Conversation 发布，均为 `provider_frame_rendered`；0 回退、帧拒绝或未执行。局部测试本地 298 / 服务器 278 通过，覆盖完成、取消、reset、stop、历史身份、路由、预算及 schema 拒绝 | 默认关闭的整链候选实现与窄组流程已验证；不能证明历史 v30 RuntimeError 细因，也不能保证供应商普遍可靠 |
| 搜索 / 读取 | 本组使用已有 ICAO 7173、国航 1339 字符全文，0 新搜索/读取；原服务组合与 Cloudflare 条件保持 | 冻结能力评测与实时检索分离；沿用此前有效正文证据，模型自主发现仍待验证 |
| 已得资料 / 跨轮保真 | 16 次 wire 全文和实际前轮答复完整，4/4 末轮保留真实前轮声明的未核实缺口；最终逐条审查仍有 2 条条件作用域歧义 | 状态保留机制有效不等于整体质量通过；条件关系与要求对象仍阻塞本候选 |
| 角色 / 三种 presentation | 本组仅 `detailed_answer` 两文档、两重复；没有新角色定向、三 presentation 全量或物理播放 | 原门槛保持；既有微信文字、桌面真实播放确认继续沿用，不扩展其适用范围 |

候选通过中立、版本化响应 schema 请求成文帧，完成帧校验后才发布自然语言。仅将已完成
generation 的模型声明缺口与其可见来源身份暂存于有界会话状态；正文仍来自已提供
资料或可信 READ 收据。缺口不是事实证明、长时记忆或权限，不能从任意助手散文恢复。
schema 不受支持、畸形/超限帧或迟到输出显式失败，不降级为表面成功。默认参数
`source_answer_frames=False`，没有写生产数据库、设置或迁移；未部署或采用候选。
[ADR 0065](../../../../adr/0065-bounded-source-answer-coverage-candidate.md)是本阶段运行前方案。

## 质量结论与评测器误判

最终审查与冻结全文、当前问题、每组自己实际生成的历史逐条对照，不是独立盲评。

- `frozen_airchina:r1:t2`：禁运项把原文的替代关系写成连接关系，否定作用域不清。
  不能断言一定强化或弱化，但没有清楚保留任一无法确认即可触发的条件。
- `frozen_airchina:r1:t4`：把关闭开关与绝缘要求接在同一条件下，可能把独立绝缘
  要求限于带开关设备。出现“绝缘”关键词不能代替要求对象保真。
- 初次审查把 `frozen_icao:r1:t3` 的计划/事实边界判为部分符合。全文使用“将”，并明确
  区分成员国已执行，撤销该误判。`primary-review.json` 原样保留为初审 13/16；
  [最终审查](primary-review-final.json)为 14/16，修正理由与原审查指纹见
  [复核记录](review-adjudication.json)。没有修改答复或降低原标准。

[最终决定](decision-final.json)维持拒绝。`decision.json` 为复核前决定，保留历史。
新组整链、状态表示和已发生的历史不同，不能把与旧组的数量变化称作因果 A/B 改善，
也不能把这组结果推广为 Gemini 的固有能力或 Q02 全部通过。

## 实际输入、用量与延迟

操作配置沿用 32768 窗口、8192 输出预留/cap、15% 参考余量、21370 输入额度。
完整 wire 包含响应 schema、原文、未核实状态及历史；参考输入 **4165–6729**，
没有原文/历史/tool preamble 省略。HTTP p50/p95 **11265/22834 ms**，回合
p50/p95 **11308/22885 ms**。这是完成时延；帧完整校验后才发布，没有首 token 时延记录。

原 16 条脚本漏传 `request_usage=True`，实际 payload 没有请求流式 usage，故 **16 条
供应商 usage 均未知**。这是已确认的评测采集缺陷，不能归因于供应商不支持 usage。
原脚本和样本不改；`run_coverage_with_usage.py.txt` 只修正该参数，尚未重新执行质量组。

随后只做两次独立 raw-SSE 采集控制，复用两条实际末轮 payload，唯一 wire 改动是
`stream_options.include_usage=true`；不是新的 Conversation 四轮或质量替代样本：

| 控制来源 | 参考输入 | 供应商 prompt / completion / total | reasoning | 完成时延 ms | 参考输入偏差 |
| --- | ---: | --- | --- | ---: | ---: |
| `frozen_icao:r1:t4` | 6615 | 5147 / 293 / 7191 | 1751 | 6992 | +28.521469% |
| `frozen_airchina:r1:t4` | 5831 | 4009 / 356 / 4365 | 未知 | 4878 | +45.447743% |

两次均 HTTP 200、DONE、无错误，无重试、无公网读取；两次 usage 已报小计
**9156 / 649 / 11556**。reasoning 只 1/2 已报，不补零，不给原 16 条补写用量。
误差按 `(参考输入−供应商 prompt)/供应商 prompt` 算；估算器不是原生计数器或已证明
上界。15% 余量不是已证明的误差界，操作预算也不是端点最大上下文或输出能力证明。
当前两条质量缺陷发生在资料完整送达后，不能解释为旧 7292 阈值压缩或自动假定扩大
窗口能修复；没有调整生产窗口。

实际请求始终为指定 `gemini-3.8-flash-high` / 8318 端点。输出标签分别为 control 8 次、
flash-n 8 次，历史组也出现过这些标签；0 收费调用的 `/v1/models` 元数据检查列出 High，
但未提供上下文/输出上限或别名映射。仅记录配置端点响应链结果，不猜测底层模型变化。

## 可复核文件

- `plan.json` / `admission.json` / `exit.json`：运行前门槛、实际配置与全部计数。
- `frozen-sources.json` / `fixtures.json` / `persona.md` / `budget.json`：固定问题、真实资料
  及原 closure-c1。资料作为显式用户提供材料，没有伪造搜索/READ 成功。
- `results.jsonl` / `provider-rounds.jsonl`：实际发布回复、原始帧、终止与错误类别；
  `actual-provider-payloads.jsonl` / `http-outcomes.jsonl` 为全部 16 次实际 wire 和 HTTP。
- `flow-integrity.json` / `accounting.json`：全文、历史、gap 及分项省略/预算/时延核验，
  不能单独批准语义质量。
- `usage-control/`：两次独立用量采集的实际 payload、脱敏可见 SSE 与核算，未保留隐藏推理。
- `candidate-sha256.json` / `candidate-code/` / `post-run-source-integrity.json`：23 项冻结
  源码/测试/ADR 与服务器副本；原运行脚本另有指纹。
- `historical-v36/` / `review-diffs/` / `delta-summary.json`：8 项修改前文件与实际增量。
- `software-checks.json` / 本地与服务器日志：软件检查及其边界；`dry-controls.json`
  为无收费模型的受控流程测试，不当作真实质量证据。
- `identity-probe.json`：只读模型列表，不能证明别名映射或最大能力。
- `prior-manifest.json`：本阶段开始前 225 项历史文件指纹；更新的根 README 原文另存
  `prior-documents/README.md.txt`，不覆盖历史原始回复、用量或失败。
- `manifest.json` / `final-artifact-audit.json`：本阶段最终资料指纹与无模型调用的完整性审计。

下一依赖仍是条件关系及要求对象的保真。按统一台账先判断可审查的实现机制能否满足
原标准，当前候选整组冻结；不在同一版本里修补个别回复，不新增 persona/格式变体或
因局部成功启动全量。生产代码缺陷、采集缺陷、检索问题与回答问题继续分别记录。
