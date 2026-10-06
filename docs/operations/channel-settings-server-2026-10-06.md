# 外联设置发布与 Mac 远程客户端

2026-10-06 10:29（Asia/Shanghai）切换主 Runtime 完成。产品提交 `4898cb1`，
工作区 `/Users/mubai/.codex/worktrees/messaging-short-replies/CW2`，
分支 `mubai/messaging-short-replies`。按用户要求直接实现、审查及部署，未使用 AGY。

## 发布与保留范围

新发布目录为
`/home/mubai/chatwaifu-server/releases/channel-settings-20261006-4898cb1`。
从当时实际运行的 `qq-group-voice-20261005` release 复制，在主 Runtime 上覆盖
116 个已核对的源码、协议、测试与新文档路径，并发布 7 个 Web 构建文件。
既有依赖锁未变，保留原依赖环境；启动入口显式加载当前发布的三个 Python 源码包。
迁移副本确认新协议实际从当前发布导入，现场确认新增 API 可用。

部署前比对 1279 个源文件。产品文件均与冻结基线一致；服务器的
`docs/architecture/external-channels.md` 和 `docs/implementation-status.yaml`
与本地进度文档不同，发布时保留服务器原文件，其他未覆盖源码逐项哈希保持。
本地完整状态及本操作记录以本工作区文档为准，未用本地文档覆盖服务器现场记录。

主状态目录、访问令牌、微信/QQ 凭据、模型路由与预算、表情/照片库均继续沿用。
模型仍为 `gemini-3.8-flash-high`，端点仍为 `https://mubai.website:8318/v1`，
Embedding 及各记忆模型未改。公开网页仍使用 SearXNG/Crawl4AI 原服务；
已有 `dns_resolver=cloudflare` 的公网读取运行约束未修改。
新渠道权限在首次 UI 保存前使用既有默认值，现场 revision 为 0。

切换只重启主 Runtime 及恢复其 HTTPS 依赖；Web Nginx、旧入口跳转代理、
主 STT 与主 GPT-SoVITS 的 PID 不变，13 个运行容器身份一致，NapCat 未重启。
独立 QQ Runtime 继续关闭。

## 数据、权限与接口核验

先在 SQLite 一致副本上演练 43→44：原 82 张表、6614 行、schema 对象、
旧迁移记录及校验和完全保持，新设置表为空；完整性和外键检查通过。
停机后另存最新一致副本，再由新版启动迁移现场数据库。此前 43 条迁移记录和
224 turns、116 generations、103 channel turns、98 deliveries、123 parts
逐行保持；现场 schema 44、完整性与外键检查通过。

QQ 与当前有效微信连接恢复 ready；旧微信绑定保留，原过期绑定未重新绑定。
两个原启用群通过真实新成员观察后重新授权，scene、成员身份和发言子集保持。
931709239 群仍为原 5 人及按需语音开启；另一原群为 2 人、语音关闭。
主动消息策略及连接 presentation 配置保持。重新授权沿用原 CAS/受众机制，
没有直接写路由表或扩大成员权限。

实际 `GET /v1/channels/settings` 返回原权限和旁听预算；
无访问令牌被拒绝，使用无效 revision 的同内容 PUT 返回 409，读回保持不变。
这项检查未保存新策略或临时切换生产权限。前期隔离本地浏览器流程已验收真实
API 保存、读回及刷新持久化，本次生产 UI 只读取。

服务器 Web 的 7 个构建文件及 `/settings/channels` 入口内容与本地冻结产物
哈希相同。Mac 访问新管理 API 为 200，返回允许的
`http://127.0.0.1:5173` CORS 来源。原
`https://mubai.website:18443` 管理 API 同样返回新设置，
使用系统根证书及真实主机名验证，无证书绕过。

服务器生产环境没有 pytest；在备份目录内用冻结锁安装独立测试环境，
没有向生产虚拟环境安装测试依赖。Linux 新设置、群流程、群语音、
表情学习及协议定向测试 **170 passed**，保留原 `audioop` 废弃警告。

## Mac 客户端

当前原生前端从上述隔离工作区运行，开发服务在
`http://127.0.0.1:5173`，Runtime 为
`http://192.168.1.103:18780`。已恢复主目录现有的本地渲染资源，并编译本工作区
原生 host；开发 app 位于该工作区的
`target/debug/bundle/macos/ChatWaifu NEXT.app`。
原访问令牌保留，客户端连接配置文件仍为 0600；地址修改前保留私有副本。
主目录与并行搜索/回答工作区未修改。

原生设置窗口实际显示“已连接远程服务器”、Live2D ready，并在“渠道”读取
远程服务地址、SearXNG/Crawl4AI、原私聊权限及群旁听全部预算。
Mac 未启动本地 Runtime 或 STT/TTS worker。当前打开的 Mac 原生客户端可直接
使用“设置 → 渠道”管理该服务器。

## 备份与验收边界

私有目录
`/home/mubai/chatwaifu-server/backups/channel-settings-20261006-4898cb1`
权限为 0700，保存配置副本、源码清单、迁移演练与停机/切换后一致数据库、
原发布路径及检查记录。发布 provenance 已记录产品提交和核验记录。
公开检查文件见[部署证据](../research/qq-agent-plus-evidence/channel-settings-ui-2026-10-06/deployment-public.json)
和[Mac/Web 核验](../research/qq-agent-plus-evidence/channel-settings-ui-2026-10-06/mac-and-web-public.json)。

旧 Runtime 只接受 schema 43，不能仅把源码链接切回后使用 schema 44。
若新版启动失败且新设置表仍为空，已准备的保护性回退会先备份最新数据库，
只移除未使用的新表与第 44 条迁移记录，保留最新业务事实，再恢复旧代码；
本次启动成功，没有执行回退。只要新策略已保存，就不能删除它或直接用旧备份覆盖
当前库，必须先制定兼容策略并保存最新事实。

本次没有手工发送 QQ/微信、合成真实语音或重新验收手机收件/播放。
源码测试、部署状态及原生 UI 读取已通过；Q02 回答质量、群语音播放等原有
待验收项未被本次设置页发布改写。
