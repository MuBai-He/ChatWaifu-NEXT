# QQ 与 Q02 v7 临时服务器测试版

日期：2026-10-04。分支：`mubai/qq-v7-server-test-20261004`。
本次由 Codex 直接整合，未使用 AGY。

后续：2026-10-05 按用户要求，主实例也升级到该合并版，主实例与 QQ 实例的
聊天模型均切为 3.8 Flash。当前状态见
[主实例切换记录](primary-qq-v7-flash-server-2026-10-05.md)；本文件保留初次
QQ 实例部署时的验证结果。

## 固定来源与整合

- 共同基线：`e6093459ffa5105892e61172af5ca1804dd89a9b`。
- QQ：独立仓库 `/Users/mubai/Desktop/CW2-QQ-integration` 的
  `mubai/qq-napcat-integration`，`f77e75ea6c02bf4af7dba5344395a8498199194f`。
- 提示词和搜索优化：`mubai/q02-v7-test`，
  `6200595ec1ff7ad0c18fddeaff22e5482b99938b`。
- 整合目录：`/Users/mubai/.codex/worktrees/qq-v7-server-test/CW2`。
- 合并提交：`944deed`，保留两条来源历史。两条原分支、主工作区和本地隔离评测数据库未修改。

解决 CHANGELOG、工具循环、装配、提示编译、会话服务和工具路由的冲突。
保留 QQ 的群身份、私聊记忆隔离、引用、模型选择语音、取消/投递围栏，
以及搜索的 Provider 配置、输入预算、正文读取、资料延续和默认关闭的来源实验。
v7 原始文本保持不变，SHA-256 为
`5dde07c1f242dc1dc197b04240452e17cd56738efbd25e4dbb7d210bcbdaf616`。

## 本次测试范围

按 [ADR 0072](../adr/0072-opt-in-qq-owner-public-web-reads.md)，增加默认关闭的
`public_web.qq_owner_reads_enabled`。仅在服务器独立 QQ 测试配置中启用：
主人私聊可以调用内置只读搜索/网页读取，搜索问答先使用文字；普通聊天仍可由
模型选择本轮语音。群和主动消息不开放工具；长期主动消息保持关闭。
现有网络地址检查、超时、schema、审计和取消仍由 Runtime Skills 执行。

Q02 的回答保真和 v7 全量角色质量仍未通过，手机故障项仍以
[QQ 手机验收](../qq-phone-acceptance.md)为准。部署健康、自动化回归、实际搜索
服务读取和手机看到的结果分别记录，不将旧工作线的通过数字算作本次验收。

## 检查与部署记录

最终 Python 全量回归 3517 通过，46 个 Windows 专属检查跳过。QQ/群/取消/
配置专项 104 通过，其中新增只读整合用例 5 通过，覆盖正常读取、正文延续、
撤权、取消及搜索到读取的完整路径。Python Ruff 和格式检查通过，Pyright
零错误零警告。前端 lint/typecheck、409 项 Web、111 项协议及 22 项 Avatar
测试、Web 构建均通过。协议重生成没有产生差异。

目标为 `192.168.1.103` 的 `/home/mubai/cw2-qq-napcat-stage` 和
`/home/mubai/.local/share/chatwaifu-qq-stage`；使用已有 QQ 绑定及测试数据。
在切换前保存原源码、Web、配置和一致数据库备份，验证现有 SQLite 40 的
迁移校验和与新 Runtime 匹配，并测试副本重开。主服务与独立 ASR/TTS/NapCat
及搜索容器不随此次源码切换修改。
原 QQ 测试的角色目录指向主服务；此次仅将 QQ 测试配置的 `characters_dir`
切到本次测试源码的角色目录，使 v7 与原主服务提示词分开加载。

## 已部署的服务器版本

2026-10-04 22:23（Asia/Shanghai）核验：运行代码提交
`98756c91bf036f58a618e90e0f0e04fd42989b39`。其后的本文件更新属于部署记录，
不改变该冻结代码版本。

- 发布目录：`/home/mubai/cw2-qq-napcat-stage/releases/qq-v7-20261004-98756c9`。
- 源码和 Web 分别通过 stage 下的 `source`、`web` 符号链接切到发布目录。
  发布包不含冻结评测资料目录或任何本地隔离评测数据库。
- 独立测试入口：<http://192.168.1.103:18782/>。页面、带认证的 API 和角色包
  均对应本次发布；`18780` 为现有主服务入口。
- QQ Runtime 为 `chatwaifu-qq-stage.service`，监听 `127.0.0.1:8771`。
  Web 使用 `chatwaifu-qq-stage-web.service`；独立页面/API 代理使用新增的
  `chatwaifu-qq-stage-proxy.service`，配置位于 stage 的 `proxy/nginx.conf`。
  只在 QQ 测试配置中增加该测试入口的 Origin，认证继续启用。
- 主人私聊只读搜索开关已开启。复用现有 SearXNG `18081` 与 Crawl4AI 0.9.3
  `11236`，实际搜索返回 3 条结果；读取 Python 官方文档得到 3000 字符、
  命中 TaskGroup，截断标记如实为 true。读取认证只存放在服务器私有配置。
  系统 DNS 的 Fake-IP 地址被拒绝；显式 `dns_resolver: cloudflare` 成功，
  仍执行公网地址验证和地址固定。

验证结果：Runtime `ok`、数据库 `ready`、QQ 连接 `ready`、NapCat `healthy`，
页面 HTTP 200。带测试 Origin 的认证 API 与一次性票据 WebSocket 通过，收到
`system.runtime_started`；无认证管理请求仍为 HTTP 401。
逐文件校验 1251 个源码文件及 7 个 Web 文件，页面实际提供的 7 个文件也全部
匹配发布清单。v7 角色包哈希为
`6fb5522cd016b491d1ae714ebe84f8594cadf9c463a9eb1b887f2a9f2c5eb96e`，
与 Runtime 返回值一致；在实际 65536 模型上下文预算下，三种展示模式均未省略
persona 文本。SQLite 40 迁移校验和全部匹配，副本重开及现场 `quick_check` 通过。

原 1 个连接、3 个绑定、35 轮消息、35 条投递、38 条投递分片、4 条记忆、模型
配置和迁移记录均保留；连接的在线时间等运行字段除外。7 个受保护服务的状态/
进程 ID 与 7 个凭据文件的哈希未变化。重连暂停后重新观测原群，账号、两名成员、
身份映射和发言权限完全一致，恢复同一场景；路由从 revision 11 经两次维护
重连至 15。主动消息保持关闭、revision 6，旧出站意图与投递未被重放。

本次没有从真实 QQ 账号发送验收消息。手机端语音、群记忆、合成/发送阶段取消
和 v7 回答质量仍需在这一组合版本上继续验收；旧手机记录不代表本次已通过。
服务器私有 `DEPLOYMENT-VERIFIED.json` 保存现场校验结果。

## 回退

原源码、Web、runtime.toml、凭据配置和一致数据库副本保存在
`/home/mubai/cw2-qq-napcat-stage/backups/qq-v7-before-20261004-98756c9`。
执行该目录下的 `rollback.sh`：停止新增代理及 QQ 测试 Runtime/Web，恢复原
源码/Web 链接、原 runtime.toml 与发布清单，再启动原 QQ 测试 Runtime/Web。
不覆盖数据库；已发生的发送、记忆和回执必须保留。若重启暂停群路由，仅在
新观测与原账号、群受众和成员映射完全一致时恢复同一场景；变化则保持暂停。
