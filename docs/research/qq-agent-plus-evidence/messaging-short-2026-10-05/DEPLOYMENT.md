# 消息渠道短回复：部署与恢复记录

2026-10-05，Asia/Shanghai。代码分支 `mubai/messaging-short-replies`，冻结产品提交
`bd0097048abe745d322ea2cb9b9f6dc1355bfaa3`。后续文档提交不改变该产品版本。
本次由主代理实现、审查和验证，未使用 AGY，没有代用户向 QQ/微信发送测试消息。

## 已部署的范围

主服务器 `192.168.1.103` 的 main Runtime 已切到
`/home/mubai/chatwaifu-server/releases/messaging-short-20261005-bd00970/source`。
微信、QQ 私聊/固定群和后续外部消息 Provider 按当前 `external_channel` 来源
选择简短闲聊约束；问候、告别优先一条有用短消息，详细任务保留完整条件和代码。
QQ 固定群补齐原来缺失的消息 profile，并接入已有无损分条、持久节奏和逐条回执。

Local text、Web、桌宠、voice、proactive 的完整编译与部署基线逐字相同。
v7 persona、Gemini 3.8 Flash High、现有 8318 路由、65536 窗口及预算 `{}` 不变；
SearXNG、Crawl4AI、模型凭据、Web 静态文件、STT/TTS 服务不变。
公网 `web.read` 仍使用 `dns_resolver=cloudflare`。

已保存两条连接的显式消息 policy：preferred 30、soft 60、最多三条 text parts、
原有 800–3000ms/6000ms 节奏上限、详细/code bypass。保留 QQ 原表情 opt-in，
微信表情关闭。长度是生成/展示指引，不是丢弃内容的上限。

## 验证及实际边界

| 范围              | 结果                                                                            | 边界                                                    |
| ----------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Python            | 3537 passed、46 平台 skips；Ruff/format、strict Pyright 通过                    | 含 populated 40→41、取消/撤权、逐条回执和未知发送围栏   |
| 前端              | typecheck/lint、111 protocol、22 avatar、409 Web tests、Web/桌面 UI builds 通过 | 前端代码与部署 Web 资源没有随本次修改                   |
| 真实模型定向对照  | 24/24 模型答复、0 Provider errors；保留 payload、usage、延迟和代码执行          | 仅六个固定问题、两重复、A/B；一问上限和技术表述仍有缺口 |
| 主 Runtime/数据库 | health ok、database ready、SQLite 41、quick_check ok、0 FK errors               | 不等于真实手机收到或体验通过                            |
| 源码/状态         | 261 产品文件哈希匹配；44 Web 文件、配置/密钥哈希不变；先前事实保留              | 不恢复旧数据库覆盖新消息                                |

停机迁移时核对原 81 表、3894 行逐字段保留，迁移 40 及之前的 checksum 不变。
既有模型配置、109 turns、56 generations、43 channel deliveries/46 parts、
角色/关系状态、成员身份/场景链接和记忆记录保留。
24 条样本的具体失败和限制见 [定向对照](README.md)，不据此宣布 Q02 或手机 UX 通过。

## QQ 群恢复过程与当前阻塞

首次重新观察成功后，部署脚本误将只含成员 ID 的 `member_fingerprint` 与含身份链接
和发言授权的 `audience_fingerprint` 比较，阻止了恢复。这两个哈希不应比较；该错误
属于运维核对，不是成员真的变化或产品拒绝。正确检查是账号、真实成员 ID 集合、
原场景、身份链接和 speaking grants。后续不使用旧观察绕过 60 秒有效期。

随后完整成员 API 连续返回空数组，而群信息显示三人；缓存开关、字符串/数值群号
对照仍为空，三个已知成员的单人接口没有有效结果。配置 WS 端口与原容器一致。
用户确认群和账号未改。CW2 保留完整名单校验，未手工修改 grants 或 SQL 强制开群。
已安装 NapCat 的 `no_cache` 实现会启动刷新，但缓存存在时可能先返回旧 Map；
这只是源码观察，不能单独解释持续空数组，也不归因于模型、供应商或 DNS。

确认无 active generations/待投递消息后，仅重启原专用 NapCat 容器，容器身份和
持久挂载保留，启动配置仍是原账号。快速登录明确报“登录态已失效，请重新登录”。
QQ 因这次恢复操作暂时离线；该日志不能反证重启前空名单的具体原因。
当前微信 ready，QQ degraded，原固定群 disabled/reconnect、revision 20；
原 scene、账号、成员、participant/link 身份和 speaking grants 全部保留。

已向用户提供临时登录二维码，二维码/令牌/账号 ID 不进入本证据目录。
04:06:43 的 NapCat 日志确认二维码被扫描；04:06:50 返回 `serverErrorCode=168`，
原文为“你的账号近期存在安全风险，部分功能使用受限，请登录最新手机QQ并根据提示恢复账号使用。”
用户认为已经登录后，新的只读登录请求仍未建立有效 OneBot 连接；扫描成功不等于登录成功。
这属于已经确认的 QQ 平台登录阻塞，不归为提示词、成员变更或 CW2 投递失败。

待用户在手机 QQ 按平台提示恢复账号并完成原账号登录后，先验证同账号及完整真实
名单，再用新观察和最新 revision 恢复原群；名单不一致时保持暂停，不扩大参与者
或发言权限。部署恢复尚未完全完成，真实手机短回复体验也没有新增通过记录。
当前结构化状态见 [deployment.json](deployment.json)，恢复后的新结果须另行记录。

## 备份与回退

服务器私有备份目录：
`/home/mubai/chatwaifu-server/backups/messaging-short-before-20261005-bd00970`。
保存停机一致性数据库、配置、加密 vault、原 policy/群授权以及读取失败证据；
不复制这些私有资料到仓库。`rollback.py --check` 及 rollback-source 打开迁移 41
的副本已通过，未实际回退。

回退 source 必须识别 migration 41，保留当前数据库及新事实；先取消未发送的群尾部，
再恢复原产品逻辑/连接 policy。40-only executable 不能直接打开 41 数据库。
群恢复仍需要原账号的新完整观察，回退代码不能替代 QQ 重新登录。
