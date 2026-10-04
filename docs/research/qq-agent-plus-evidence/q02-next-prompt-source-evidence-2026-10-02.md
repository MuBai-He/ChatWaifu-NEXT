# Q02 Prompt and Source-Evidence Follow-up

日期：2026-10-02。执行约束：不调用 AGY，由主代理直接实现、审查和测试。

## Implementation

本轮修改了 Character Kernel 的共享提示契约，而不是把充电宝法规或 Raft 答案写入 persona：

- 当前法规、安全和合规问题优先消费本轮 supplied source/tool evidence，保留生效日期、范围、阈值、例外、禁止项、识别/召回条件和未核实项。
- 把来源事实整理成 Checklist 时，先保留所有阈值分支和批准例外，再压缩表达。
- 技术协议回答区分规范与实现配置，并区分 `broadcastTime`、heartbeat interval 和 election timeout；具体数值默认标注为实现示例。
- 评测器 1.15.0 不再丢弃 fixture 中已有的 `source_snapshot`。它会把当前轮快照作为 bounded、untrusted source evidence 投影到 source-ledger，且 dry-run 逐轮估算并计入该证据；这不是 memory，也不是生产法规数据库。
- Raft 首轮加入同样的规范来源快照，明确论文的 `broadcastTime ≪ electionTimeout ≪ MTBF` 关系、心跳间隔与选举超时的区别，以及具体毫秒数只能作为实现示例。共享提示同时禁止在没有来源或规范资料时把记忆中的当前阈值、日期或协议数值说成已核实事实。
- 独立法规审计按 CAAC 概念 ID 选择主快照，额外技术快照独立保留，避免 fixture 增加来源后破坏报告类型或把 Raft 当成法规覆盖目标。

## Real-model evidence

固定端点为认证后的 `https://mubai.website:8318/v1`，模型为 `gemini-3.8-flash-high`，上下文窗口 32768、输出预留/上限 8192、15% 估算余量、`scaled` 分项。价格未知，按用户授权使用 `--no-cost-ceiling`；密钥未写入仓库或证据。

带 source snapshot 的 `detailed_answer` 三重复、四轮共 12 条保存在 `.local/research/q02/q02-next-source-evidence-v2-gemini-20261002/`：12/12 `stop`，Provider usage 合计 prompt/completion/reasoning 为 `39480/5956/10761`，延迟 p50 `9442ms`、p95 `16116ms`。法规轮 3/3 保留 2025-06-28、3C/CCC、召回型号/批次、100/160Wh 分支、正确的标称电压换算和禁用 USB 输出 5V 的边界；后续 Checklist 3/3 保留全部分支。离线事实审计为 `fully_covered=3/3`、`missing=0`、`check_passed=true`；5V 仍是人工复核提示，不把关键词覆盖当成法律正确性。

最终代码下的 `topic_switch` 三重复、四轮共 12 条保存在 `.local/research/q02/q02-next-topic-v2-gemini-20261002/`：12/12 `stop`，Provider usage 合计 prompt/completion/reasoning 为 `35938/1563/5696`，延迟 p50 `6709ms`、p95 `8879ms`。三条首轮均区分心跳间隔、选举超时和 broadcastTime；150–300ms 被描述为论文/实现示例，未再把 50ms 当作协议固定值。

本轮修改后的 `topic_switch` 三重复、四轮共 12 条保存在 `.local/research/q02/q02-next-topic-v3-gemini-20261002-auth/`：12/12 `stop`，供应商 prompt/completion/reasoning/total 合计为 `31573/1401/7842/40816`，延迟 p50 `7695ms`、p95 `11605ms`。三条首轮都明确说明论文不规定固定毫秒数，并区分 `broadcastTime`、heartbeat interval 与 election timeout；150–300ms 只作为实现示例。后续番茄炒蛋话题四轮均完成切换，没有把旧技术话题带回。

作为归因对照，source snapshot 投影之前的无资料直答批次仍记录在 `.local/research/q02/q02-next-prompt-fix-gemini-20261002/`：Raft 已改善，但充电宝回答仍漏掉 3C/召回。这说明输入资料是否实际进入请求与模型能力必须分开判断。

## Verification and boundary

相关后端、来源工具、预算和评测测试共 `246 passed`，本轮追加的角色与评测回归为 `102 passed`，TTS/播放 ACK/渠道确定性集成为 `67 passed`，另有 1 个既有 `audioop` deprecation warning；本轮源码/评测文件的 Ruff、格式、Pyright 和 `git diff --check` 均通过。最后一次改动 dry-run 逐轮估算并计入两个来源快照，法规审计 `--check` 对 3 条 source-assisted 样本为 3/3、0 缺项。

这批结果证明“给出来源资料时能完整保留关键条件”的提示与评测路径有效，不证明没有来源时模型会自行知道最新法规，也不构成法律意见。真实 TTS、QQ/微信渠道投递、播放完成 ACK、用户 ACK 和完整 Q02 发布门槛仍未验收，因此 Q02 不宣布通过。
