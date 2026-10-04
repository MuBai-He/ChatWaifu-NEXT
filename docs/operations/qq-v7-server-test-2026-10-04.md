# QQ 与 Q02 v7 临时服务器测试版

日期：2026-10-04。分支：`mubai/qq-v7-server-test-20261004`。
本次由 Codex 直接整合，未使用 AGY。

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

基础合并专项回归：270 通过。后续全量回归 3516 通过、46 个 Windows 检查跳过；
撤权访问修复后的 QQ/群/取消/配置专项 104 通过；新增只读整合用例 5 通过。
Python Ruff 和格式检查、Pyright、前端 lint/typecheck、409 项 Web 与 133 项
协议/Avatar 测试、Web 构建均通过。协议重生成没有产生差异。最终全量回归 3517 通过、46 个 Windows 专属检查跳过，Pyright 零错误零警告。
服务器验证在部署完成后更新。

目标为 `192.168.1.103` 的 `/home/mubai/cw2-qq-napcat-stage` 和
`/home/mubai/.local/share/chatwaifu-qq-stage`；使用已有 QQ 绑定及测试数据。
在切换前保存原源码、Web、配置和一致数据库备份，验证现有 SQLite 40 的
迁移校验和与新 Runtime 匹配，并测试副本重开。主服务与独立 ASR/TTS/NapCat
及搜索容器不随此次源码切换修改。
原 QQ 测试的角色目录指向主服务；此次仅将 QQ 测试配置的 `characters_dir`
切到本次测试源码的角色目录，使 v7 与原主服务提示词分开加载。

回退时停止 QQ 测试 Runtime，恢复原源码/Web 和原 runtime.toml，再启动。
不覆盖数据库；已发生的发送、记忆和回执必须保留。若重启暂停群路由，仅在
新观测与原账号、群受众和成员映射完全一致时恢复同一场景；变化则保持暂停。
