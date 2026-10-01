# Q02 来源条件候选与原生写入拒绝复核

2026-10-01，两批生成均固定在 `29431786cfc2e8a9f15183b707d75c6f525be359`，沿用当前 CW2 所配置的 HTTPS 8318 端点和 `gemini-3.8-flash-high`。未调用 AGY、切换账户或使用 Claude。全部生成终止后才修改测试或整理公开证据；默认 persona v4、生产设置和来源政策保持，**Q02 仍未通过**。

## 来源条件提示 A/B：没有采用证据

此前完整 Conversation 补测中的一条清单把认证撤销/暂停背景混入公告明确禁止类别。本次只在冻结评测请求中追加通用要求：区分生效要求、背景、理由和例子，保留范围与例外，明确推断和未支持细节。没有把具体法规答案写进生产提示。

18 对/36 条文本重放包括九份先前实际 Conversation 的来源清单输入，以及三个合成说明各三重复。合成说明分别检验访客证与员工例外、设备型号/累计运行检查、拍摄资料的条件许可与未采纳手机禁令；它们明确标为 supplied-document fixture，不冒充实际 READ。历史输入覆盖三种 presentation，合成说明仅 single_text。不是新的来源抓取、完整 Runtime 或 persona A/B。

主代理逐条读取全部原文，在打开映射前冻结 A/B 判断，SHA-256 为 `8273713063d60640abee23d4ad71883f6f0ec1cd79635efb857ceea256f79252`。主代理知道候选设计，`blind=false`，不能冒充独立盲评或人工通过。结果为基线 18/18、候选 18/18 符合核心条件，**18 对全部平手**；本批双方均未复现目标背景混淆。两条回复从 URL/历史推断公告发布日期但未标注推断，单列风险，没有凭此断言日期错误。

因此**不采用候选政策**，也不抹掉原完整 Conversation 的失败。小样本、默认随机采样和历史输入不能证明错误已经消失，更不证明当前完整法规覆盖。

审计核对了 36 个唯一键、每条实际输入 hash、36 次请求/终态、逐段 Provider delta 与完整回复、冻结判断 hash，以及 `build_messages` 后唯一的政策差异。全部输入估算在各自冻结额度内，最大估算 5074、供应商最大输入 3530。36 次成功 Provider 请求均报告输入/输出/总量，分别为 85214/7420/112372 token；reasoning 已含总量，35 次已知合计 19738、一次未知，不补零。价格与美元账单未知。进程关闭后的 httpcore async-generator 清理警告保留，不据此重跑已完整保存的回复。

完整冻结输入、原文、逐项判断、映射、原始 delta 和 Provider 用量见[来源 A/B 证据](source-rule-scope-ab-evidence-2026-10-01.json)。公开 JSON 将原 `pair_key` 字段名投影为 `pair_id`，标识值与模型原文不变；原始文件 hash 和判断冻结 hash 保持。

## 实际 READ 后的原生 WRITE 拒绝

此前九个提醒拒绝控制只拒绝了前置 organizer READ，没有走到 WRITE 确认。本次通过实际 RuntimeContainer/ConversationService 与完整十二 Skill 目录，为即时消息、普通文本和默认语音来源各运行一次查询设备后创建提醒请求。每个 Runtime 使用独立 SQLite 和配置，通过实际 TaskRepository 创建本地合成设备；没有真实设备连接、生产账户或外部发送。

三次 organizer READ 均在 `allow_once` 后进入实际隔离读取，返回唯一设备 ID、空提醒列表和调度状态。模型随后都提交原生 `schedule.create`，参数使用实际查询到的设备 ID，标题“检查充电宝”，时间 `2026-10-02 09:00 Asia/Shanghai`、不重复。三次 WRITE 确认均拒绝，`permission_denied` 出现在权限门之后、写入适配器之前；最终文字都明确提醒没有创建，没有重复调用或承诺送达。

实际初始决定为 **native auto**，每个请求的模式/工具数为 auto/8 → auto/8 → auto/0；这是本次措辞在有界语言政策之外的原生选择，不能称为 required 模式的写入拒绝测试。Provider 原始调用、两次 Skill 终态、确认顺序、实际 READ 输出与写入参数逐项核对。三库的 `assistant_tasks`、`assistant_deliveries`、`assistant_operations` 和 `permission_grants` 均为零，所有 Runtime 已正常停止，其他适配器均隔离。

共三条可见回复、九次 Provider 请求、六次 Skill 执行；主代理非盲复核三条均符合本拒绝任务。九次均报告输入/输出/总量：29190/777/33385 token，另报 reasoning 3418 已含总量；最大输入 4121、最大估算 4868，全部冻结预算审计通过。价格和美元账单未知。

原始事件、实际调用 hash、确认、隔离 READ、身份模板 v7、用量和数据库计数见[原生写入拒绝证据](gemini-read-write-denial-evidence-2026-10-01.json)。仅证明 WRITE 拒绝与诚实收尾；成功创建、持久提醒、设备投递和真实语音仍未由这三次控制验收。

## 继续开放的门槛

默认 persona v4、候选只用于评测、草稿 PR 和旧 Claude 批次的十四个缺项保持。来源当前性、完整条件与引用、技术质量、全十二场景/三重复/三展示和受控 Runtime 接受门槛继续开放。两批窄证据均不能批准 Q02。
