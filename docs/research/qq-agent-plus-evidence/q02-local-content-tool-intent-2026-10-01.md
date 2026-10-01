# Q02：本地内容生成与检查不强制外部操作

2026-10-01。在 4a80465 的实际完整注册 Conversation 中，写诗、检查算式、检查所给代码、明确不打开 URL 而解释其结构，全部被误设为 required。正常文字被丢弃、额外一次纠正后得到 Runtime fallback。见[四项修改前记录](local-content-tool-intent-before-2026-10-01.json)；这些受控实验没有调用付费端点或外部适配器。

共因是宽泛操作动词与任何 URL 都被当作外部操作。修复仍使用同一当前用户意图政策，只对明确本地内容对象或明确否定的 URL 读取放行 auto；同一句中的保存、发送、读取、个人日历查询等明确外部操作继续 required。英文 and/then/also 与已有中文并/然后/顺便一起识别复合请求。未匹配措辞仍由 native auto 选择函数，权限、确认、预算、取消及工具结果路径保持原有检查。

新增语言与实际 Conversation 回归在旧代码为 10 失败、48 通过；修改后四类实际 Conversation 均一次发布正常文字，零 Skill run，保留完整角色与相关工具投影。复合保存/发送/读取及真实外部事实、提醒请求继续覆盖。它不是覆盖所有语言的分类器，也没有用能力 ID 或模型正文控制操作。

提示身份 v6→v7 记录选择政策变化，历史身份/schema 1.0 仍兼容；评测器 1.9.1 拒绝混合实现续跑。默认 persona 仍是 v4；提示模板版本与 persona 候选版本是两个概念。此前[三展示 Gemini 144 条](q02-gemini-three-presentations-2026-10-01.md)全部属于修改前 4a/v6，不能冒充此修复后的付费验证。

在修复提交 `3ef23da786a8af5ed292062851fb109bca22e087` 固定源码后，新 Gemini 实际 Conversation 补测四类请求、三种展示、各一重复，共 **12 条回复、12 次 Provider**。主代理逐条复核均符合任务要求；全部 auto、完整角色、零 native call/Skill run/确认/外部执行/持久授权。没有用已完成旧记录补样本。所有文字 delta 与可见回复一致；[全部回复、原始 Provider 事件、usage 和输入身份](gemini-local-content-conversation-evidence-2026-10-01.json)可复核。四项判断标准在读取回复前保存，主代理知道设计，非盲、不是人工通过。

供应商输入 33836、输出 818、总量 36300、reasoning 1646 token，全部 12 次上报，reasoning 已含总量。最大实际输入 3388、完整估算 3965，价格与美元账单未知。每展示每类仅一次，不是概率保证或十二场景 A/B；voice origin 仍只是文字输出，所有外部适配器均围栏。这批没有重测来源条件混淆或真实 WRITE。

完整 Python **1863 通过、46 平台跳过**；定向 Agent/Conversation 138 项和 Web 312 项通过。Ruff lint/format、全量 Pyright、架构边界、Web lint/typecheck、Web/正式桌面 UI 构建和文档构建通过，协议生成无 Schema/TypeScript/fixtures 差异。十四个精确暂存文件扫描零发现，原 CI/TTS 文件字节保持；新源码 CI 单独记录，原 4a CI 不算后续源码通过。来源条件混淆、当前搜索可用性、原生 WRITE 确认与整体 Q02 质量仍开放。
