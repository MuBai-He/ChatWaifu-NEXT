# 主实例切换到 QQ/v7 合并版及 3.8 Flash

2026-10-05 00:30（Asia/Shanghai）现场核验完成。用户在原 QQ 测试环境完成
升级后，要求把服务器主实例也切到合并版，并修改聊天模型。本次直接操作，
未使用 AGY；属于临时测试分支部署，未将 Git 主分支或质量验收状态改为已通过。

## 运行版本与入口

- 临时分支：`mubai/qq-v7-server-test-20261004`。
- 主实例运行代码：`98756c91bf036f58a618e90e0f0e04fd42989b39`。
  后续提交仅更新部署记录。
- 现有主服务：`chatwaifu-runtime.service`，沿用状态目录
  `/home/mubai/.local/share/chatwaifu-server` 和原数据库。
- 源码及 Web 发布目录：
  `/home/mubai/chatwaifu-server/releases/qq-v7-20261004-98756c9`。
  原 `/home/mubai/chatwaifu-server/source` 和 `web` 切为该发布的符号链接，
  主实例使用独立安装的发布依赖。
- 主网页仍为 <http://192.168.1.103:18780/>；原加密入口端口 `18443` 保留。
  Nginx 配置、进程及认证凭据未修改。
- 实际承接 QQ 的现有 `chatwaifu-qq-stage.service` 继续运行原状态目录；
  QQ 管理入口仍为 <http://192.168.1.103:18782/>。未混合主实例的微信数据
  与 QQ 实例的账号、群及私聊数据。

## 聊天模型

主实例与 QQ 实例均通过各自 Runtime 的模型配置 API，把 `chat` 从
`gemini-3.1-flash-lite` 改为 `gemini-3.8-flash-high`。该标识来自现有接入服务
实时返回的模型清单，是当前可用的 3.8 Flash。

保留各实例原来的 Provider、地址、密钥、超时和 65536 上下文预算。主实例的
记忆抽取/摘要仍为原有 `gemini-3.8-flash-high`，Embedding 仍为 `voyage-4`；
QQ 的记忆抽取/摘要和 Embedding 保留原有配置。未修改其他模型角色或密钥。
主实例与 QQ 实例修改后均实际返回探测文本；主实例切到新版 Runtime 后再次
验证，`chat` 探测返回 `status: ok`，2 个字符。探测未创建聊天会话或向账号发送
验收消息。

## 数据、静态资源及 Provider

主数据库从 SQLite 35 升到 40。切换前的数据库副本升级保留 72 张原表的全部
原有字段和记录；迁移校验和匹配，`quick_check` 通过，外键错误为零。
现场数据库达到 40，完整性及外键检查均通过。24 张事实表的原有字段和记录
与停机备份一致，包括 17 个会话、26 轮 turns、14 个 generations、角色/关系
状态、原微信连接和绑定以及 2 条渠道消息/投递。连接在线时间与轮询 checkpoint
更新时间属于正常运行字段；此次 checkpoint 只变化 `updated_at`。

主 Web 发布补入原来的 37 个 Live2D vendor 文件，包含 Cubism Core、桥接模块、
模型、贴图、表情、动作和 shader。原模型清单及独立语音 worker 的缓存/配置
保留。逐文件校验 1251 个发布源码文件，并通过 HTTP 校验 7 个新 Web 文件和
37 个保留的 vendor 文件，共 44 个静态文件。

原主服务的 systemd `config/public-web.env` 覆盖 TOML 中的 Provider 配置，
有效 SearXNG 为 `18080`、Crawl4AI 为 `11235`，仍使用原读取凭据。按该实际配置
读取 Python 官方 TaskGroup 文档成功，返回 3000 字符、命中 focus，使用
`dns_resolver: cloudflare`。QQ 实例继续使用此前的 `18081`/`11236`；两者验证
记录分别保存。地址验证、认证和默认关闭的 QQ 只读开关在主实例保留。

## 现场验证与边界

- 原主网页 HTTP 200，带原 Origin 的认证 API 通过；无认证管理请求仍为 401。
- Runtime `ok`、数据库 `ready`，原微信连接 `ready`、revision 1。
- 新 Runtime 加载的角色包哈希为
  `6fb5522cd016b491d1ae714ebe84f8594cadf9c463a9eb1b887f2a9f2c5eb96e`。
  在 3.8 Flash 的现有预算下，三种展示模式均未省略 v7 persona 文本。
- 一次性票据 WebSocket 收到 `system.runtime_started`。
- 原 `18443` 入口验证 TLS 1.3、证书链和主机名，通过认证读取到新版聊天模型。
  `chatwaifu-https.service` 依赖主 Runtime，停机时随之停止，切换后已恢复。
- 其他 8 个服务的进程/状态未变，包括主 Nginx、主语音 worker、QQ Runtime/
  Web/代理及 QQ 语音 worker；11 个既有凭据配置文件哈希未变。
- QQ 连接继续 ready；原两人群仍启用、revision 15。主动消息仍关闭、revision 6。

本轮没有做真实 QQ 手机对话、头像实际渲染或麦克风到播放的端到端验收。
v7 回答质量及先前未完成的手机故障项仍按原验收记录继续，运行健康和连通性
不代表这些质量项已经通过。冻结代码先前的自动化检查见
[QQ/v7 初次部署记录](qq-v7-server-test-2026-10-04.md)。此次没有修改运行代码。

私有现场记录位于主发布目录的 `PRIMARY-DEPLOYMENT-VERIFIED.json`；两实例
各自保存 `CHAT-MODEL-BEFORE-3.8-FLASH.json` 与 `CHAT-MODEL-3.8-FLASH.json`。

## 备份与回退

主实例原源码、Web、配置及一致数据库备份位于
`/home/mubai/chatwaifu-server/backups/qq-v7-before-20261005-98756c9`。
该数据库备份是在聊天模型改为 3.8 Flash 后、数据库升级前取得。

旧 Runtime 只支持 SQLite 35，不能直接打开现场 40 数据库。因此主实例的
`rollback.sh` 会先保存当前数据库副本，核对切换后是否出现新的业务事实。
没有新增或修改业务事实时，恢复旧源码/Web、runtime.toml 和 35 数据库，同时
保留当前渠道轮询 checkpoint，再启动主 Runtime 与关联 HTTPS 转发。
若已有新聊天、记忆或设置变更，脚本保留当前发布并拒绝直接恢复旧数据库，
需要先协调这些新数据。不会直接用旧备份覆盖新业务事实。

已在独立副本演练：恢复 35 数据库并保留现场 checkpoint 后，原 Runtime 的
35 迁移目录能够重新打开它，完整性检查通过；没有切换或覆盖现场数据库。
脚本 shell 语法检查通过。QQ 实例继续保留此前独立备份与回退脚本。
