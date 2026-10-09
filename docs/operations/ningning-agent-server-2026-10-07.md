# 宁宁自主 Agent 部署：192.168.1.103

> 这是 2026-10-07 的历史部署记录，服务版本、迁移和渠道状态对应当时的验收窗口。
> 本 PR 仅归档证据；Agent 实现已进入 main，不执行新部署，也不表示服务器仍处于下述状态。

2026-10-07 已切换服务器主 Runtime 与 Web 前端。服务使用原状态目录、访问令牌、模型配置、QQ 登录及 Calendar 凭据。桌宠与 QQ 继续使用同一个主 Runtime。

- Web：[能力与任务](http://192.168.1.103:18780/settings/agent)。连接类型选择远程 Runtime，地址为 `http://192.168.1.103:18780`，沿用原访问令牌。
- 发布目录：`/home/mubai/chatwaifu-server/releases/ningning-agent-20261007-e60eb37`。
- 备份目录：`/home/mubai/chatwaifu-server/backups/ningning-agent-20261007-e60eb37`，仅服务器所有者可访问。
- 状态目录：`/home/mubai/.local/share/chatwaifu-server`。
- 服务：用户级 `chatwaifu-runtime.service`、`chatwaifu-web.service`、`chatwaifu-https.service`，均为 active。

## 来源与部署调整

实现来源为 `mubai/ningning-autonomous-agent` 的未提交工作区，基线 `7f9bab6e25837be68fb44d480ec5a7b7eeed323c`。原实现快照 SHA-256 为 `e60eb37ed2516540f816bc8031506b1c3f3ea7abde49c220e178898010d92b7e`。部署保留旧服务器文件，叠加 169 个文件；记录位于发布目录的 `deployment-source-manifest.json`。这是发布快照，不代表提交、合并或推送。

部署调整摘要 SHA-256 为 `1b5c9d6e0f63d5e70ea16aa895676c2b9a4d4d98226bb356995727f3ccc0d4db`。

- 保留正在运行的 NapCat 4.18.28。官方 4.18.28 与 4.18.33 的七个已审核场景查询接口，请求与响应契约逐一一致。导入器保存精确兼容版本、来源及 SHA-256；未审核版本仍拒绝执行。新增可执行动作会使旧兼容证据失效，需要重新审核。版本来源为 [官方 OpenAPI](https://github.com/NapNeko/NapCatDocs/blob/main/src/api/4.18.28/openapi.json)。
- Runtime 使用独立 `runtime-env`，按冻结的 uv 锁文件安装生产依赖。PptxGenJS 4.0.1 安装于独立 `node-runtime`，不改动原发布虚拟环境。
- LibreOffice 24.2.7、Noto 中文字体与 Poppler 使用独立 Docker 镜像。镜像 ID 为 `sha256:d45320d409dd7a98684d15b49b407127bb2c3eae56f9fc93eca3358d34e9bb8f`；运行时按 ID 调用，禁止网络、只读根文件系统、移除能力，限定 CPU、内存、进程数及时间，仅挂载本次临时文档目录。
- `tools/agent/docker_document_renderer.py` 提供受限 CLI。收到取消会停止并移除本次容器；容器自身另有 55 秒期限，覆盖宿主进程被强制终止的情况。
- 增加独立 systemd PATH drop-in，使主 Runtime 找到文档工具。Nginx 为 `.mjs` 增加 JavaScript MIME 类型，PDF.js Worker 的实际 HTTP 返回类型已核对。
- 前端未嵌入服务器访问令牌。所有连接令牌保留在原私有配置或浏览器会话中。

## 数据和运行状态

停服前重新取得一致性 SQLite 备份。版本 44 升至 46 后，83 张原表、7020 条原数据及原迁移账本逐项核对保留，完整性与外键检查通过。较早的在线演练备份包含 7011 条原数据；最终部署采用停服时的最新数据。

模型配置、私有配置与凭据文件 SHA-256 保持不变。原 13 个正在运行的 Docker 容器 ID 保持不变；无关用户服务 PID 保持不变。QQ 私聊连接与当前有效微信连接均为 ready。另一个微信连接在部署前已为 `weixin.session_expired`，该既有状态保留。

两个 QQ 群路由部署前均为关闭、`reconnect` 暂停，部署后保持相同状态，没有自动启用或扩大成员权限。群自主参与均为 `off`；桌宠决策为 `legacy`；候选开发关闭。要开启群功能，需先在群管理中重新核对当前成员与原授权，再按群设置参与模式。

能力目录可读取 202 项能力，文件任务、产物及候选开发接口可正常访问。Word/PPT 显示“需要授权”，文档渲染依赖已经配置。其余 QQ 官方动作仍按实现状态显示“需要适配”。

## 验证与范围

- Linux Agent 与工具调用用例：首次 157 通过、2 个文档渲染失败。修复容器渲染调用后，文件及真实 Word/PPT 三项检查全部通过；相关 159 个不同用例最终通过。
- Linux QQ/NapCat 回归：569 通过。
- 实际 Word、长内容分页 PPT、ZIP 结构、PDF 渲染及中文内容核对通过。
- 运行中渲染取消后 0.214 秒完成清理，无残留容器。
- 部署前后均执行当前模型的真实 completion 预检，成功；仅列出模型并不作为证据。
- 浏览器连接真实服务器，复用原所有者会话，读取完整能力目录、Word/PPT 能力及 QQ 群参与设置，未修改设置、创建任务或发送消息。页面错误与 Agent API 失败均为零。
- Web 发布 10 个文件的 HTTP 内容哈希与构建产物一致；PDF Worker MIME 正确。HTTP 与通过系统信任根校验的 HTTPS 健康检查均为 `ok`。
- 部署调整通过 Ruff、格式、严格 Pyright 及架构边界检查。原实现的完整源码、前端、协议和模型评测证据仍见 `docs/research/ningning-agent-2026-10-07/README.md`。

没有向真实 QQ 发送测试消息或文件，也没有写入真实 Calendar。手机收到并打开文件、真实日历冲突处理与写入后回读，仍需单独验收。浏览器此次验证现有页面和设置读取；文档预览交互的既有隔离浏览器验证不等同于生产产物验收。

## 备份与回退

备份保留原源代码和 Web 链接目标、原配置、加密渠道凭据、systemd 单元、Nginx 配置及在线与停服前数据库副本。发布目录保存来源、Web 哈希、迁移、切换、验证及渲染取消记录。

回退演练在副本上将 46 还原为 44，保留演练数据库中全部旧表的最新事实。此方法只适用于新增 Agent 表尚无数据、相关设置尚未改变的情况。若已经产生新任务、产物或开发数据，应保留最新数据库并修复前进；不能把停服前数据库直接覆盖到正在使用的数据上。旧 Runtime 无法直接读取版本 46。

服务器备份的 `ops` 目录保留本次操作脚本。脚本中的一次性前置检查用于避免重复切换，不能未经核对直接重复运行。生产部署未触发回退。
