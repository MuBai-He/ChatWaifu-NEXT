# Q02：工具相关性与操作必要性修复

2026-10-01。普通调侃或告别不再因为词语与工具说明相关，就被强制要求执行外部操作。Conversation 和评测路径使用相同的当前用户意图规则：普通对话使用 native auto 并保留完整角色提示；明确外部操作、来源 URL、时效性外部事实和自然提醒请求继续使用 required。

## 修改前的证据

固定在 fb6ee17/评测器 1.8.2 的[完整 Gemini 批次](real-model-gemini-full-v4-v7-2026-10-01.md)中，六条普通调侃被强制搜索，六条告别被纠正为必需函数决策，最终全部进入 Runtime fallback。另六条实际法规查询遇到 `web_search_challenge`，属于搜索可用性问题，不能由放行普通对话修复。

真实 Conversation、完整注册 Skill 路由、一次 allow_once READ 后的告别控制也失败，见[修复前记录](source-goodbye-full-router-before-2026-10-01.json)。新增回归在旧代码为四项失败、一项通过：optional 普通回答被丢弃、optional 空答未报错、真实 Conversation 告别及调侃各多一次纠正。这个受控实验没有付费调用或生产数据。

## 实现与边界

复用既有 `LlmRequest.tool_choice` 和 native function 协议，不从模型文字判断或执行操作，不新增模型分类调用。意图只来自当前用户文本；短 READ 纠正还要求可信本地上一轮本身是操作请求及整句匹配，避免将“是不是真的呀”嵌入角色问句后恢复旧来源操作。

required 保留安全/时间决策上下文、一次纠正和无工具结果时丢弃未核实完成说明；auto 使用完整角色/历史，完成且非空的普通答案可以发布。native auto 真正请求函数时仍经过原权限、确认、结果及写后关闭路径，工具前叙述不发布。非法或缺失终态、空答、取消及过期 generation 均阻止输出；冻结完整输入预算仍先于 dispatch。

“不用再提醒我”这种不带对象的对话收束不会操作已保存提醒；“明天不用再提醒我会议”及明确取消/创建请求仍走操作路径。该语言规则没有能力 ID 分支，也不是覆盖全部自然语言的分类器；未识别措辞仍由 native auto 决定是否请求函数，不能越过权限。auto 输出本身不是外部动作或事实的成功证据，仍需真实质量复核。

非敏感日志记录 generation、mode、schema 数。提示身份版本 v5→v6 使新 generation 的上下文身份体现规则变化，旧版本/schema 1.0 仍兼容；评测器 1.9.0 指纹包含新增意图模块，拒绝混合旧批续跑。[ADR 0061](../../adr/0061-optional-native-tool-decisions.md)记录决定和回退。

## 验证与后续

实际完整注册 Conversation 的新调侃与告别回归保留 prior READ 原文，Provider 请求为首次读/总结两次加当前正常回答一次，只有一次 READ，零额外 Skill run；此前失败路径是四次 Provider 请求。source_summary 原控制仍无工具重放，fresh 查询与自然提醒仍 required。

40 项意图区分控制，加上 native auto 函数、拒绝结果、空答、错误/缺失终态、取消后迟到输出、预算溢出及 READ 纠正，补齐明确查询/写入与普通对话边界。完整 Python 为 **1845 通过、46 平台跳过**；Web 312、lint/typecheck 和 Web/正式桌面 UI 构建通过。Ruff、全量 Pyright、架构边界和文档构建通过；协议生成后的 Schema、TypeScript 与 fixtures 无差异。精确暂存扫描与新提交远端 CI 单列，不借 fb6ee17 的 CI 通过替代。此次没有重建冻结 Runtime 安装包，不将源码检查称为安装包验收。

本次没有生产配置、用户数据或默认 persona 变更，没有真实渠道发送或设备播放。修复后的付费 Gemini 行为、搜索可用性、完整三种 presentation 和受控 Runtime 操作仍需继续验收；Q02 未通过，PR 保持草稿。
