# 原文行引用隔离控制

结论和依赖顺序只维护在[统一台账](../../q02-closure-ledger-2026-10-04.md)。
原型 2.0 冻结否决，Q02 未通过；本目录不触发另一条实验计划。

- `airchina/`：两份真实成文帧 T3 的相同输入末轮控制，2 HTTP，真实旧 gap 保留。
- `icao/`：既有完整 T1–T3 的末轮控制，2 HTTP；没有历史结构化 gap，不从散文构造状态。
- 两目录的 `actual-provider-payloads.jsonl` 包含完整正文、实际历史、完整行目录及 schema；
  `results.jsonl` 保留原始帧和 `rendered_reply`。后者仅为隔离成文，未由 Runtime 发布或投递。
- `http-attempts.jsonl` 与 `exit.json` 记录所有真实尝试/终止；0 回退/拒绝，0 新完整四轮。
- `accounting.json` 核对原文/历史/渲染/schema/实际省略与输入新增开销，未评分语义质量。
- `primary-review.json` 为实现作者逐条全文审查，0 完整/4 部分，不称独立盲评。
- `decision.json` 记录不采用决定；保留原文不等于可读、相关或跨轮保真。
- `validation-local.log` / `validation.json` 为软件检查，不能代替质量验收。
- `prototype-sha256.json` 为本阶段代码/测试/ADR 指纹，`server-code-sha256.json` 为服务器
  隔离副本与本地相同的 16 项指纹；原 Runtime v36 的 13 项没有变化。
- `.py.txt` 为实际运行脚本和代码；`historical-adr0064-source-frame.md.txt` 与旧 ADR 指纹
  精确匹配，保留旧方案版本；`prior-manifest.json` 保留本阶段开始前 195 项资料指纹。

两份国航清单都选入无关页脚；ICAO 大段英文照搬，一份重复核心段，另一份未明确
保留指定日期范围。原文行的字面一致不能证明选择覆盖或自然语言转换正确。
行并非完整语义条件，标题和新缺口仍是模型文本。没有新增航空规则或 persona 变体，
没有提高生产窗口，四份完整资料均未被裁剪。

脚本只在服务器进程内读取模型密钥，传回前检查未泄漏。没有隐式推理文本、凭据、
数据库或新渠道投递。返回模型标签与底层行为未验证，费用未知。审计脚本需本仓库
Runtime、protocol-python、model-worker-sdk-python 三个源码目录的 `PYTHONPATH`。
