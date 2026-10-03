# QQ 下一片：主动文本与群成员隔离

本文记录 D2、D3 的实现边界与尚未完成的验收。D2 已按
[ADR 0068](adr/0068-owner-opt-in-qq-proactive-text.md) 实现默认关闭的源码、
迁移 38、管理接口和界面；服务器部署及真实主动文字验收单独记录。
D1 的入站语音和模型选择回复方式已经集成，并取得单次手机播放确认。
D3 按 [ADR 0069](adr/0069-qq-group-member-identity-and-shared-context.md) 集成了
群路由、成员身份、受众隔离、管理接口和共享设置源码。新群路由默认关闭；完整 Runtime
与本地 OneBot 的群聊链路、资源上限、服务器部署及两成员手机验收仍是独立的未完成门。
当前完成状态以 [实施状态](implementation-status.yaml)、相关 ADR 和实际验收记录为准。
本文不改变现有 owner 配对、语音授权或搜索工作流的权限。

## A / B / C / D 的范围

| 阶段 | 正确范围                                                                           | 不应被这一阶段的结果替代的验收                                             |
| ---- | ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| A    | owner 私聊文字、可信配对、连接管理、持久 admission、取消、receipt 与重启发送栅栏   | 拒绝其他 sender、群消息、异常发送结果和长时间账号恢复                      |
| B    | 有界静态图片理解、可选本地表情图片发送、同绑定的权限化文字引用                     | 手机实际图片理解、表情和引用展示；动画、照片留存和表情学习不属于这个静态片 |
| C    | 按 ADR 0067，由模型选择本轮文字或 `channel.voice` 回复；普通文字优先、失败回退文字 | 当前 generation 的主人权限、打断、异常 receipt 和手机播放；不扩展主动权限  |
| D1   | owner 发来的语音经过有界下载、解码和 STT，进入同一文字对话路径                     | 实际语音识别、取消、资源恢复和真实 QQ 接收验收单独记录；本文不宣告其完成   |
| D2   | owner 私聊、显式 opt-in、固定 binding 的主动文字                                   | 默认关闭、quiet hours、TTL、撤销、重复调度与未知发送结果                   |
| D3   | 明确启用的小群、白名单成员、结构化 @ 触发、可信成员与群 scope                      | 群内身份、关系、记忆、历史和受众隐私；不是所有群消息自动接入               |

D1 / D2 / D3 都属于原计划 D。B 的图片引用、C 的按需输出语音不能代表 D 已完成。
[ADR 0064](adr/0064-qq-napcat-current-turn-voice.md) 与
[ADR 0065](adr/0065-bounded-qq-images-and-reply-references.md) 保留原有边界。

## 可复用领域与真实缺口

QQ 仍是适配器，不增加另一套角色、模型、记忆或定时发送主循环。

| 已有领域及源码                                                                                                                                                                                                                                                                          | 可复用能力                                                      | 下一片必须补上的连接点                                                        |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| [SessionService](../services/runtime/src/chatwaifu_runtime/sessions/service.py)，`create_session`                                                                                                                                                                                       | registered participant、不可变 scene、服务端派生 scope/audience | provider/account/sender 到 participant，以及群 route 到 scene 的可信映射      |
| [MemoryService](../services/runtime/src/chatwaifu_runtime/memory/service.py)，`namespaces_for_session`；[历史仓储](../services/runtime/src/chatwaifu_runtime/persistence/sqlite_conversation.py)，`recent_history`                                                                      | 持久 session scope 下的记忆、历史、投影和重置过滤               | 群内第一人称事实的可信主体；不能加载成员的私人 namespaces                     |
| [CharacterKernel](../services/runtime/src/chatwaifu_runtime/character_kernel/service.py)，`_session_scope`、`snapshot`                                                                                                                                                                  | 持久情绪、关系及 CAS                                            | 原有 scene 共用关系；D3 增加群内成员独立 state scope                          |
| [Ambient](../services/runtime/src/chatwaifu_runtime/companion/ambient.py)，`decide_proactive`                                                                                                                                                                                           | quiet hours、busy deferral、cooldown、日预算、审计              | 外部目标 opt-in、固定 route、TTL、发送前重查与有界 pending                    |
| [Conversation](../services/runtime/src/chatwaifu_runtime/conversation/service.py)，`submit_proactive`                                                                                                                                                                                   | 角色、记忆、正常生成，不伪造可见用户发言                        | text-only 外部选项、可恢复主动 intent 与预分配 lineage                        |
| [Channel ports](../services/runtime/src/chatwaifu_runtime/external_channels/ports.py)、[scheduler](../services/runtime/src/chatwaifu_runtime/external_channels/scheduler.py)、[QQ delivery](../services/runtime/src/chatwaifu_runtime/external_channels/adapters/qq_napcat/delivery.py) | plan/part、lease、取消、receipt、unknown journal、重启不重发    | 非入站回复的明确来源、持久目标、权限/route revision 和 expiry                 |
| [Assistant tasks](../services/runtime/src/chatwaifu_runtime/personal_assistant/tasks.py) 与 [其持久化](../services/runtime/src/chatwaifu_runtime/persistence/sqlite_assistant_tasks.py)                                                                                                 | owner 验证、occurrence 去重、过期、撤销与迟到结果的既有模式     | 当前目标是本机 device，不是 QQ；不能直接把 device delivery 表改成外部发送队列 |

以下是 D3 设计所针对的原有缺口。对应源码现已集成，但不能以源码或私聊回归
代替完整群聊和真实受众验收；D2 不扩大现有主人私聊权限：

1. 主人私聊仍由 `ExternalChannelService._admit_ingress` 创建 owner session。
   群聊独立进入 `ChannelGroupService`，迁移 40 为 route、scene、member binding 和
   typed turn lineage 建模；历史 GROUP 来源的旧绑定保留并隔离，允许建立新的安全私聊绑定。
2. Conversation 从持久 session 核验 trusted identity 后保留 QQ route 和 speaker provenance，
   并继续禁止群工具、媒体和照片；提交前后及生成前均保留撤权检查。
3. 群第一人称事实在 scene namespace 内使用持久 speaker 派生的 participant subject。
   同名成员不会因为昵称而合并；缺失可信主来源、第三人称或不匹配的提取内容不能自动
   归到当前成员。语言启发式和本地用例不代表所有自然语言场景都已验收。
4. 群关系和情绪使用 `(scene_id, participant_id)` 的 state scope，记忆仍使用共享 scene
   scope。迁移不把历史歧义事实或私聊关系自动复制给群成员。
5. D2 已从桌面 Ambient 排除历史或当前 channel session，并为 `submit_proactive`
   提供固定来源、预分配 lineage 和纯文字选项。桌面主动开关不授权 QQ，群也不能
   继承这项主人私聊 policy。
6. 群投递使用持久 route 固定的 typed group target，调用 `send_group_msg` 前重查
   lineage、账号、受众和 revision；没有群目标或授权时不能回退到私聊。完整 Runtime
   到真实本地 WebSocket 的群投递仍须单独通过验证。

## D2：owner 私聊的主动文字

### 最小完整片与职责

只提供已配对 owner binding 的 `idle_check_in`，默认关闭。重用正常角色生成、纯策略函数
和 channel delivery scheduler。暂不接日历、Skill completion、群主动广播或自动语音。
新增独立、可撤销的目标授权；远程文字、旧 turn、记忆和引用均不能开启该权限。

初始设置为 idle 45 分钟、cooldown 60 分钟、每日最多 3 次、23:00–08:00 安静时段；
timezone 使用 IANA 名称 `Asia/Shanghai`，TTL 为 15 分钟、上限 60 分钟。
每次保存 policy 都撤销旧 episode；保存后需主人再发一条新消息，才建立新的空闲窗口。
允许关闭整条 route；不能用全局 companion 开关代替 route opt-in。

新增版本化 `ChannelProactivePolicy` 和 `ChannelOutboundIntent`，由 channel 应用服务持有。
SQL 留在 persistence adapter，Conversation 不读投递表，NapCat 不运行生成或记忆逻辑。

- policy：enabled、允许来源、timezone、quiet hours、cooldown、日预算、TTL、revision。
- intent：唯一 request/source-event key；固定 connection/account/binding/character/scope；
  policy 与 route revision；not-before、expires-at；服务端预分配 session/turn/generation；
  生成文本/hash、delivery 引用、状态和可解释终态原因。
- 预算在事务内预留，防止并发 scheduler 都通过先读后写的检查。失败、取消和未知发送
  均不退还预算，避免另一请求立即重复打扰。
- 每 binding 最多一个非终态主动请求，全局最多 32 个；扫描和保留记录有分页及期限。
  使用现有事件唤醒与有限工作任务，不为每次 tick 创建独立生成/发送队列。

### 持久生成、投递与撤销

1. 从已授权的固定 binding 建立 intent；在事务内确定唯一来源 key、lineage 和预算预留。
2. 生成前重查 enabled、scope、账号、revision、quiet hours、TTL 和当前活动 generation。
   使用 `submit_proactive` 的正常角色路径，显式协商 text-only、无工具、外部 source。
3. intent 与 Conversation lineage 的关联必须可恢复。若提交时跨领域不能原子提交，
   预分配 ID 和持久 admission/reconciliation 必须保证重启不启动第二 generation。
4. 生成完成后再校验，才发布固定 route 的 plan；在 claim、账号 preflight 后的实际 send
   前再次校验。权限变化、失效 binding、quiet hours 或 expiry 阻止未发送部分。
5. 新 owner 输入取消旧主动生成及未发尾部。忙时仅在 TTL 内延后；过期进入明确终态。
   重启不补发过期或撤销的积压问候，也不重做已经完成的模型生成。
6. 沿用现有 durable unknown fence。provider ID 已保存而 scheduler ACK 丢失，只补 receipt；
   unknown 不自动重发。关闭或过期不能抹掉已经发生的 provider 成功事实。

迁移 38 为 `channel_deliveries` 增加明确的 outbound-intent 来源关系，并约束一个 plan
恰好选择入站 turn 或主动 intent，不能伪造 QQ 入站 message ID 来挂主动消息；
复用现有 part、lease、ACK 和 journal，不重写发送状态机。scheduler、终态事件及 quote 读取
必须识别来源：主动消息没有自动继承的入站 reply target。

provider receipt 表示服务端接受；不代表手机已读、语音播放或用户处理。D2 只有文字，
不把 provider ACK 伪装成桌面 `presented` 或提醒的 `acknowledged`。

### 管理 API 与界面

operator 管理接口：

```text
GET/PUT /v1/channel-connections/{id}/proactive-policy
POST    /v1/channel-connections/{id}/proactive-preview
GET     /v1/channel-connections/{id}/outbound-intents
POST    /v1/channel-connections/{id}/outbound-intents/{request_id}/cancel
```

PUT 使用 revision CAS；preview 只计算当前资格、时窗和原因，不调用模型、不预留预算、不发送。
冲突的保存或取消不打断有效生成；客户端刷新后由 operator 重新决定。API 返回 sanitized intent 和原因，
不返回凭据、原始 OneBot 数据或允许模型选择收件人的工具。能力登记只有在完整实现后才宣称支持。

已扩展 [QQChannelPanel](../apps/web/src/features/desktop-settings/QQChannelPanel.tsx) 和
[channel client](../apps/web/src/features/chat/runtime-client/channelsClient.ts)：显示固定 owner 目标、
默认关闭的独立开关、时间/频控、预览和最近结果。关闭开关要持久撤销未发送请求，而非只隐藏 UI。
设备提醒 [AssistantDelivery](../apps/web/src/features/personal-assistant/AssistantDelivery.tsx) 继续保持
owner/private 和本机 device 语义，不改成 QQ 投递面板。

## D3：小群结构化 @ 与成员隔离

### 默认入口和可信身份

第一片只接受明确启用的小群、注册且允许发言的成员、结构化 `at.qq == self_id`，并输出文字。
普通群消息不调用模型或写记忆；昵称、文本中的“@机器人”、历史引用、其他成员消息不能授权。
匿名、错误 self-account、自己发送、未知 sender、未知群和 unsupported mixed media 都失败关闭。
新增群媒体、跨成员引用和群主动广播各自需要后续独立权限与验收。

新增 provider/account/sender → participant 的不可变映射。仅 operator 可以链接已有 participant，
不凭相同昵称复用身份，也不自动把 unknown sender 映射到 `local`。owner QQ 身份可以映射到
`local` participant，但在群里仍使用 scene scope。

连接的管理 owner 与具体对话 principal 必须分开：连接可仍由 operator/owner 管理；
每条入站的 scope 从可信 route/member 映射及持久 session 派生，不能沿用连接上的 `local`，
更不能相信 HTTP 客户端、消息正文或模型提交的 `principal_scope`。

| 上下文            | 边界                                                           |
| ----------------- | -------------------------------------------------------------- |
| owner 私聊        | 既有 `local`                                                   |
| 其他成员私聊      | 独立授权的 `participant:<id>`；不因启用群而自动允许私聊        |
| 群共享事实、历史  | `scene:<id>`；只读写这个群的共享 context                       |
| 群内成员关系/情绪 | 服务端派生 `(scene_id, participant_id)` 的 state scope         |
| 群内个人事实      | scene namespace 内可信 participant subject；不同成员不互相更正 |

成员私人 namespaces、owner 私聊记忆/关系、照片、日历和工具授权不得并入群 prompt。
`MemoryRecordDraft.subject_id` 可重用：第一人称由持久 speaker 确定；第三人称不凭 display name
解析为某个注册身份，先保留 review 或拒绝自动合并。背景投影和更正也必须带同一主体/重置栅栏。

成员 state scope 与 memory scope 必须是明确的不同字段/领域输入：可以重用 CharacterKernel
存储与 CAS，但不能为了独立关系把 session memory scope 改成私人 participant scope。
这是对 [ADR 0052](adr/0052-participant-scene-scopes-and-call-context.md) 的扩展，实施前需接受新的 ADR。

### 群路由、受众与 provider 限制

新增持久 group route：connection、固定 group ID、scene、受众和允许发言成员映射、revision。
每个 speaking member 有独立 session；串行化按整个群 route，而非成员 binding。建议一群最多
一个活动 generation 和一个可替换 pending 请求；明确新 @ 是 supersede 还是有界顺序处理。
发送目标只能来自 plan 的固定 route，不能误发给当前成员私聊。发送前复查账号和 route revision。

群号、chat type、稳定 message ID 必须参与幂等与引用边界。若 provider ID 不能证明 account-wide
唯一性，就扩展持久去重键为 connection + chat type + conversation + message ID；原始数值 ID
仍单独用于 OneBot reply，不拼成新的伪 provider ID。跨群同 ID 与同群冲突内容都要回归。

**允许发言的成员不是收到群回复的全部受众。** ADR 0052 的 scene 是 2–32 个注册参与者；
第一片建议限定受控小群。所有实际人类受众须与该 scene 边界相容，不能只登记两名 speaker
却把回复公开给其余群成员。观察到成员变化、身份不明或连接恢复时先停用旧 route，取消旧
generation/plan，核对并建立新 scene；不能静默沿用旧 audience 或合并私有记忆。

Pinned NapCat v4.18.28 的
[action router](https://raw.githubusercontent.com/NapNeko/NapCatQQ/v4.18.28/packages/napcat-onebot/action/router.ts)
包含 `send_group_msg` 和 `get_group_member_list`。后者的
[官方 producer](https://raw.githubusercontent.com/NapNeko/NapCatQQ/v4.18.28/packages/napcat-onebot/action/group/GetGroupMemberList.ts)
返回 `data` 数组；当前 `NapCatClient.call` 只保留 dict，因此需要有界、类型明确的数组读取能力。

依据该 producer 的控制流：`no_cache=true` 会启动 refresh，但随后使用
`memberCache.get(groupId) || await data`；已有 cache 时可能立即返回旧值。
**不能把 no_cache 当作即时受众证明，查询与群发送也不存在原子受众合约。** 群发送期间加入
成员仍可能看到消息。第一片全部内容必须按可分享给当前群成员分类，始终禁止私人 context；
只能声明观察到的变更会触发停用，不能宣称真正原子 membership fencing。
若产品必须保证固定受众秘密不可见，当前 provider 合约不足，不能声称满足该要求。

建议管理 API 是连接下的 group-route 资源：列出可选群、显式创建/停用 route、选择可信成员映射，
修改使用 revision CAS，改变 audience 建新 route/scene。API 不允许模型创建 route 或选择群。
UI 清楚显示“群共享上下文”和当前群目标、成员映射、仅 @ 触发及停用原因；不把群设置合并进
owner 私聊开关。大群支持需另行调整 ADR 和容量，不在第一片暗中放宽 32 人边界。

## 依赖、迁移与可回滚集成

1. 先集成并独立验收 D1 的 gateway/transport/worker 变更，再冻结 D2 分支和迁移基线。
   D2、D3 不重复 STT 部署，不改搜索工作流、NapCat 登录或现有服务。
2. 接受主动副作用及群身份/state-scope 的 ADR，更新 Python protocol 与生成 TypeScript/schema，
   再实现应用 ports、SQL adapters、scheduler、provider helper 和共享 settings UI。
3. D2 新增 policy/intent、唯一来源 key、固定 lineage 和 plan 来源约束；D3 新增 external principal、
   group/member route、revision 与成员 state scope。迁移编号在 D1 集成后分配，避免占用同号。
4. 旧 owner session、memory、relationship、binding 和 confirmed receipt 保持 `local`。
   不自动把旧私有数据复制到群。旧 scene 中主体为 `user` 的歧义个人事实不能自动归某成员；
   启用新群 scope 时不读取这类歧义事实，任何人工重新归属需保留 provenance。
5. 新来源和新 route 都默认关闭。关闭功能并持久取消未发送 intent/parts 是第一回滚路径；
   保留 receipt、unknown fence、scope 及审计，不能删 journal 来“恢复发送”。
6. 新 schema 不能直接交给旧二进制。需要代码降级时先停机并备份新数据，选用兼容新 schema
   的源码；不得用旧数据库覆盖新发生的 admission、发送或 receipt，不做破坏性 down migration。

## 验收包与完成门槛

既有回归可复用，不把其通过数当成 D2/D3 已有覆盖：

- [test_memory.py](../services/runtime/tests/test_memory.py)：`test_participant_scene_memory_and_management_isolation`。
- [test_sessions.py](../services/runtime/tests/test_sessions.py)：populated owner migration 与 scope immutable。
- [test_cloud_realtime_vertical.py](../services/runtime/tests/test_cloud_realtime_vertical.py)：scoped context sync/revoke。
- [test_companion.py](../services/runtime/tests/test_companion.py)：quiet hours、预算、忙时、主动隐藏 turn。
- [test_assistant_tasks.py](../services/runtime/tests/test_assistant_tasks.py)：occurrence、TTL、撤销和迟到结果模式。
- [test_multipart_scheduler_and_recovery.py](../services/runtime/tests/test_multipart_scheduler_and_recovery.py)：
  lease、自动唤醒、tail cancellation、事件事务和重启。
- [QQ 完整 Runtime tests](../services/runtime/tests/test_qq_channels.py) 与
  [adapter tests](../services/runtime/tests/external_channels/adapters/qq_napcat)：真实本地 WS peer、receipt 和发送栅栏。

### D2 的确定性检查

- 默认关闭和未绑定目标：零模型生成、零 provider send；桌面主动开关不能授权 QQ。
- 注入 clock 验证跨午夜、timezone/DST、忙时延后、预算事务竞争和 TTL 后不补发。
- 生成中撤销、生成完成与发布之间撤销、账号 preflight 中撤销、route revision/账号改变。
- 新 owner 消息 supersede、限额竞争、同来源重复 tick、异常生成和 shutdown 的有界清理。
- 在 SQLite 重建容器/scheduler：pending 恢复不生成两次，confirmed provider ID + ACK 丢失只补 receipt，
  unknown 不重发，过期/撤销 intent 不复活，迟到成功仍有真实 receipt。

### D3 必须新增的确定性检查

- Alice/Bob 同名及改名仍独立；同 scene 第一人称姓名/颜色不会互相更正；群成员关系不共享或复用私聊关系。
- owner/成员私人秘密、照片、日历、另一群 memory/history/quote 均不进入群 prompt 或模型工具列表。
- 有效结构化 @、文字伪 @、@其他人、普通消息、匿名、self、unknown sender/group 和 mixed-media 正负例。
- 两群同 message ID、同群同 ID 冲突、正确群发送目标、跨成员/跨群 scope 伪造拒绝。
- audience/route revision 变化、断线恢复、late result、重启、并发 @ 和 bounded pending。
- 模拟 NapCat 成员查询已有 cache 时的 no_cache 行为；不能用一个“fresh=true”假 fixture 代替 provider 限制。

不使用任意 sleep 隐藏竞争；采用受控事件、fake clock、实际本地 WS 和 SQLite fixture。
运行有关 protocol/Runtime/Web 测试、Ruff、严格类型检查、前端 lint/typecheck 和 build；
检查实际 diff，并保持原有 owner text/voice/image 路径的回归通过。

### 真实 QQ 验收独立记录

启用 D2 后在手机确认一次主动文字、quiet hours/关闭后不发、新输入能取消、Runtime 重启不重放。
D3 使用两名真实群成员确认只有 @ 触发、角色向正确群回复、另一群/私聊秘密未进入输出，
并单独验证 route 停用和恢复。手机号、群号、凭据和消息内容不写入公开验收日志。
本地 fixture、服务健康、HTTP 200 或 agent 报告都不能代替这些真实行为。

实现计划无需先追问才能继续：默认关闭、text-only、结构化 @ 可以先作为默认设计。
真正启用时由 operator 选择目标、可信成员映射、主动时间和频率；若要求跨群共享关系/私人记忆，
或要求 provider 无法提供的固定受众保证，须先明确新的产品权限与 ADR，不能隐式开启。
