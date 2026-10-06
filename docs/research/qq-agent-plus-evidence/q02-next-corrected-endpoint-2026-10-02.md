# Q02 修正端点后的定向复测

日期：2026-10-02。执行约束：不调用 AGY，由主代理直接实现、审查和测试。

## 端点修正

此前收尾探测错误地只访问了本机回环地址。当前 CW2 模型端点是认证后的 `https://mubai.website:8318/v1`：不带 Authorization 的请求返回 `401 Missing API key`，使用当前本地运行时的聊天密钥在进程内注入后，`GET /models` 返回 `200`。

当前清单共 18 个模型，包含：

`gemini-3.8-flash-high`、`gemini-3.7-flash-high`、`gemini-3.6-flash-high`、`gemini-3-flash`、`gemini-3.5-flash-lite`、`gemini-3.1-flash-lite`、`gemini-3.1-pro-low`、`gemini-pro-agent`、`claude-sonnet-4-6`、`claude-opus-4-6-thinking`、`gpt-6.1-sol`、`gpt-6-sol`、`gpt-6-luna`、`gpt-oss-120b-medium`、`gemini-3.1-flash-image`、`gpt-image-2.5`、`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`。

密钥没有写入证据、请求日志或仓库。下面的批次均使用隔离数据库、默认 persona v4、评测器 1.14.0、上下文窗口 32768、输出预留/上限 8192、15% 估算余量和 `scaled` 分项；价格未知，不从 token 用量推算美元费用。

## 定向结果

### 技术题

`technical_help` 三重复、四轮共 12 条：12/12 HTTP 成功，12/12 `stop`，Provider usage 合计 prompt/completion/total/reasoning 为 `28972/2059/35138/4107`，本地参考估算为 `31627/2485`。3 个完整 Python 示例在隔离子进程中实际执行均通过（3/3）。本批没有工具资料，最大参考输入估算 3135，远低于 21370，没有预算裁剪迹象。原始记录：`.local/research/q02/q02-next-technical-gemini-corrected-20261002/`。

### 充电宝法规与清单

`detailed_answer` 三重复、四轮共 12 条：12/12 `stop`，Provider usage 合计为 prompt/completion/total/reasoning `39623/5230/52220/7367`，最大参考输入估算 3326，仍远低于 21370。三次旅行清单和三次法规回答都没有覆盖本轮要求的 **2025-06-28 境内航班 3C 标识及被召回型号/批次限制**；多条回复还把 20000mAh、5V 换算和数量说成过于确定的规则。资料已进入完整角色输入，未见 history/memory/tool 省略，因此归因仍是来源事实没有进入该无工具场景与模型回答质量，而不是输入预算不足。原始记录：`.local/research/q02/q02-next-detailed-gemini-corrected-20261002/`。

### Raft

`topic_switch` 三重复、四轮共 12 条：12/12 `stop`，Provider usage 合计为 prompt/completion/total/reasoning `36057/1584/45119/7478`，最大参考输入估算 2770，未触发预算裁剪。三次技术首轮都能区分 leader 心跳、follower 选举超时和随机化，但继续使用 `50ms`、`150–300ms` 等实现示例，并将 heartbeat interval 与论文的广播时间约束混在一起；这些是需要谨慎标注为实现示例的表述风险，不是 Runtime 丢资料。原始记录：`.local/research/q02/q02-next-topic-gemini-corrected-20261002/`。

### 来源边界与 `default_voice` 写入拒绝

修正端点后重新执行三个展示各三种边界，共 9 条逻辑回复、25 个 Provider 回合。搜索失败和原文不可读均被明确收束为未核实；三种展示的复合请求都成功读取了允许的民航局目录，然后实际尝试 `agenda.manage`，三种均得到 `permission_denied`，三个隔离数据库的 `assistant_tasks` 均为 0，持久权限均为 0。`default_voice` 这次已经覆盖了实际 native write 拒绝路径；它仍然只是 `origin=voice` 的文本输出，没有 TTS、真实渠道、扬声器或用户 ACK。原始记录：`.local/research/q02/q02-next-source-edge-corrected-20261002/`。

## 结论

修正端点后，真实模型和来源 Runtime 都可以继续执行。新增批次没有显示预算不足或内部裁剪导致的质量损失；Q02 仍未通过，原因集中在法规事实完整性、技术表达谨慎度以及真实 TTS/渠道/播放验收尚未完成。默认 persona、生产模型、生产预算和生产数据库均未修改。
