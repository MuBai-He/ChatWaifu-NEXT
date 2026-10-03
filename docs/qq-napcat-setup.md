# QQ 私聊与按需角色语音：NapCat Linux / Docker 接入

当前接入使用 NapCat 的正向 OneBot 11 WebSocket。默认回复文字；本轮明确
说“用语音说晚安”或“把这段读给我听”时，角色才能调用 `send_voice`。后续
普通消息恢复文字。本阶段限一个主人私聊，收到的图片、语音、文件和群消息
不会进入角色对话。独立 Linux 测试服务与 NapCat 已启动，真实扫码登录
和主人私聊配对已确认；手机端已验证“文字 → 按需语音 → 文字”，
语音可以正常播放，独立测试 Runtime 重启后自动恢复绑定和在线状态。

本文只启动 NapCat。CW2 使用已有的 Runtime；Linux 常驻入口见
[源码服务器指南](operations/source-server.md)，客户端远程连接见
[轻量客户端指南](operations/remote-client.md)。端点地址始终由 **Runtime
所在机器** 解析，不是浏览器或设置窗口所在机器。

并行验证使用独立源码与状态目录。该服务器建议将 NapCat 放在
`/home/mubai/cw2-qq-napcat-stage/`，将测试 Runtime 状态放在
`/home/mubai/.local/share/chatwaifu-qq-stage/`，测试 Runtime 的
`runtime.toml` 中 `[runtime].port` 设为 `8771`；若启动测试 Web，使用
`18781`。只读取现有模型参数及所需资源；原聊天、记忆和账号状态保留在
原实例，正在进行搜索优化的 Runtime 与服务继续运行。
这些路径与端口是隔离配置，创建目录并不表示真实 QQ 已经接通。

本次测试 Runtime 已使用现有聊天模型参数和独立凭据。新的
`chatwaifu-qq-stage-tts.service` 在回环 `8773` 提供 GPT-SoVITS，代码配置、
工作目录和缓存独立，模型权重及参考音频供读取复用；一次真实本地合成
已生成有效 WAV。记忆提取/摘要暂用 demo，embedding 用 local-hash，
本次联调先验收 QQ 文字与按需语音，不据此判断记忆或回答质量。

2026-10-03 的现场部署位于 `/home/mubai/cw2-qq-napcat-stage/napcat/`。
NapCat 容器为 `cw2-qq-stage-napcat-napcat-1`，健康检查通过，WebUI 返回
HTTP 200。完整官方镜像经过逐层哈希校验后离线装载；部署目录中的
`IMAGE-PROVENANCE.json` 记录官方摘要与实际不可变 image ID，部署使用
`docker-compose.stage.yml` 和 `pull_policy: never`，防止启动时重新进行网络拉取。
通用模板仍使用官方仓库摘要。详细检查见
[本次验证记录](qq-napcat-validation-2026-10-03.md)。

本次测试部署的启停必须使用原来的 Compose 项目和覆盖文件，避免另建一个
容器争用端口。在上述 `napcat/` 目录执行：

```sh
docker compose -p cw2-qq-stage-napcat -f docker-compose.yml -f docker-compose.stage.yml up -d
docker compose -p cw2-qq-stage-napcat -f docker-compose.yml -f docker-compose.stage.yml stop
```

工作站目前有独立 SSH 隧道：测试网页为 `http://127.0.0.1:18881`，
连接页的 Runtime 地址填 `http://127.0.0.1:8871`，NapCat WebUI 为
`http://127.0.0.1:8080/webui/`。隧道断开后需重建；这些服务均未公开到 LAN。
Runtime 管理 Token 保存在服务器的
`/home/mubai/.local/share/chatwaifu-qq-stage/server.env`；NapCat 管理密码在
部署目录的私有 `.env`，OneBot 独立 Token 在 `onebot-token`。它们用途不同，
请在各自输入框中填写，不要把文件内容贴到聊天、URL 或提交到仓库。

## 准备独立部署目录

在选定 Linux 服务器安装 Docker Engine 和 Compose 插件。准备角色的 QQ
账号和主人自己的 QQ 账号。将本仓库 `deploy/qq-napcat/docker-compose.yml`
及 `.env.example` 复制到私有、长期保留的部署目录，例如
`/home/mubai/cw2-qq-napcat-stage/`。
以下命令在该目录执行；不在仓库中填写或保存真实凭据。

```sh
umask 077
cp .env.example .env
chmod 600 .env
mkdir -p data/config data/qq
chmod 700 data data/config data/qq
id -u
id -g
openssl rand -hex 32
```

编辑 `.env`：填入真实 `NAPCAT_UID` / `NAPCAT_GID`，设置 `NAPCAT_DATA_DIR`，
将生成的随机值填入 `NAPCAT_WEBUI_TOKEN`。这个密码只用于管理 WebUI。
稍后还需单独生成一个 OneBot WebSocket Token，两者不要混用。
若更改 `NAPCAT_DATA_DIR`，先在该路径创建 `config/` 和 `qq/`，由部署用户
拥有并保留 0700 权限；模板拒绝隐式创建不存在的绑定目录。

`NAPCAT_IMAGE` 必填。模板固定官方 `v4.18.28` 的多架构摘要
`sha256:2cc70b45244ae3fabc657ffc3aaa4de8fb893518171e519659ab83d80e6d36b5`。
2026-10-03 已核对该发布和 Docker 标签存在，支持 amd64 / arm64；这只确认
镜像可选，不表示本机已经拉取、启动或登录 QQ。
[官方发布](https://github.com/NapNeko/NapCatQQ/releases/tag/v4.18.28)、
[官方镜像标签元数据](https://hub.docker.com/v2/repositories/mlikiowa/napcat-docker/tags/v4.18.28)

更换版本时选择已核对的官方 `mlikiowa/napcat-docker` 发布标签，
优先固定 `repository@sha256:digest`，不要填写浮动 `latest`。选择版本后可先拉取，再用
`docker image inspect --format '{{index .RepoDigests 0}}' <已拉取的镜像引用>`
读取摘要并固定它；将实际版本、摘要和服务器架构记录在下方验收记录中。
官方仓库声明 Linux amd64 / arm64 支持及两个持久化目录。
[镜像与挂载说明](https://github.com/NapNeko/NapCat-Docker)

为使首次启动就使用明确的管理密码，可在启动前生成 `webui.json`。先核对
自己的 `.env` 内容，再执行以下命令。它保留已经存在的配置，不会静默轮换
旧密码；令牌必须至少 32 个字符，建议使用上面 32 随机字节的十六进制结果。

```sh
set -a
. ./.env
set +a
python3 - <<'PY'
import json
import os
from pathlib import Path

token = os.environ["NAPCAT_WEBUI_TOKEN"]
if len(token) < 32:
    raise SystemExit("Set a strong WebUI token before starting")
target = Path(os.environ["NAPCAT_DATA_DIR"]) / "config" / "webui.json"
if not target.exists():
    with target.open("x", encoding="utf-8") as stream:
        json.dump({"host": "0.0.0.0", "port": 6099,
                   "token": token, "loginRate": 3}, stream)
    target.chmod(0o600)
PY
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
```

官方入口的 `WEBUI_TOKEN` 只在 `webui.json` 不存在时初始化。已有数据目录
使用文件中的密码；后续登录过程也可能要求换密码。以当前配置为准，使用
WebUI 的密码修改功能维护它，不要以重建容器代替密码轮换。
[入口行为](https://github.com/NapNeko/NapCat-Docker/blob/main/entrypoint.sh)

## 登录角色 QQ，启用正向 WebSocket

模板使用独立端口避免碰到服务器上已有 Runtime / 搜索服务：
`127.0.0.1:16099` 是容器 `6099` 的 WebUI，
`127.0.0.1:13001` 是容器 `3001` 的 OneBot。
可通过 `.env` 的 `NAPCAT_WEBUI_PORT` / `NAPCAT_WS_PORT` 修改宿主机端口。
容器内服务监听 `0.0.0.0`，宿主机端口仍只
绑定回环；不要把容器监听地址误当成公网暴露设置。

若从工作站操作，在工作站建立 SSH 隧道，将 `operator@napcat-host` 换成
自己的服务器登录信息：

```sh
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:8080:127.0.0.1:16099 \
  -L 127.0.0.1:3001:127.0.0.1:13001 \
  operator@napcat-host
```

打开本机 `http://127.0.0.1:8080/webui/`，输入当前 WebUI 密码。在 QQ 登录
界面生成二维码，用**角色账号**扫码并完成 QQ 要求的确认。按 NapCat 当前
页面提示处理登录后的管理密码刷新。看到已登录账号后再继续，容器运行或
WebUI 可访问本身不代表 QQ 已登录。[官方登录与配置流程](https://doc.napneko.icu/config/basic)

在 NapCat 网络配置中新建 **WebSocket 服务端 / 正向 WS**，设置：

| 字段                 | 值                                                   |
| -------------------- | ---------------------------------------------------- |
| 启用                 | 是，保存时启用                                       |
| 名称                 | `CW2`，与其他连接名不同                              |
| Host                 | `0.0.0.0`，供 Docker 端口映射访问                    |
| Port                 | `3001`                                               |
| 消息上报格式         | `array`                                              |
| 上报自身消息         | 关闭                                                 |
| Token                | 独立生成的强随机值，至少 16 个字符，建议 32 随机字节 |
| Debug / 原始事件日志 | 关闭                                                 |

保留现有网络配置；不用打开 HTTP 服务或反向 WebSocket。相应的账号配置
文件由 NapCat 管理，通常为 `onebot11_<角色QQ号>.json`。不要把别人的
无鉴权模板直接覆盖到当前连接。[正向 WS 配置字段](https://doc.napneko.icu/config/basic)

## 在 CW2 配对主人

确认 CW2 Runtime 在线且凭据存储可用。Linux 无桌面环境按源码服务器指南
显式配置 `channel_credential_backend = "encrypted_file"`，保留密钥与密文；
复制数据库不能迁移连接凭据。

在设置 → 消息渠道 → QQ 中打开设置，填写端点和 **OneBot Token**：

| Runtime 的位置                       | CW2 中的端点              |
| ------------------------------------ | ------------------------- |
| 与 NapCat 在同一 Linux 宿主机        | `ws://127.0.0.1:13001`    |
| 在工作站，SSH 隧道也运行在这台工作站 | `ws://127.0.0.1:3001`     |
| 与 NapCat 跨机器、采用 TLS 反代      | 已配置的 `wss://...` 地址 |

点击“开始 QQ 配对”。Token 输入框会立即清空。按页面显示的实际 CODE，
从**主人账号**向角色 QQ 发送一条完整私聊文本，例如页面 CODE 为
`PAIR1234` 时发送 `CW2 PAIR1234`。不要发送字面量 `CW2 CODE`。普通聊天
不会自动认领主人；不需要在设置中手工输入发件人 id。配对过期就重新发起；
取消或离开设置页会取消仍在等待的配对，已确认连接不会因此被删除。

配对后先点击“检查 QQ 连接”，再从手机私聊发普通文字。检查成功只说明
服务连接状态，实际收到正确角色的回复才是文字闭环。一个 QQ 账号只交给
一个 Runtime 消费；不要同时启动另一份开发实例处理这个账号。

## 语音、跨机器传输和 WSS

为当前角色配置可用的 TTS 服务与音色后，再发送“用语音说晚安”。工具
只接收要说的文字，Runtime 固定当前主人、角色音色和本轮身份。一次成功
语音使用语音气泡承载回答，不额外重复发送整段文字。失败时会把本轮内容
改为文字并说明情况。没有有效当前轮次语音授权时，工具不能发语音；本版
没有持续“以后每轮都语音”的开关。

CW2 将最多 120 秒、8 MiB 的 WAV 内容作为受限的 `base64://` 消息数据
交给 NapCat；容器不需要挂载 CW2 的音频目录。NapCat 负责转成 QQ 的
`record` 语音格式。实际音色、转码兼容和手机播放需要验收；NapCat 声明
支持 `record` 并不等于本次部署已播放成功。
[消息能力](https://doc.napneko.icu/develop/msg)、[音频处理](https://doc.napneko.icu/develop/file)

跨机器直连时使用有效、受信任证书的 WSS 反代，保留 WebSocket Upgrade、
长连接与 `Authorization: Bearer ...` 请求头，再转到服务器回环 `13001`。
不要通过 URL 的 query 或用户名密码字段传 Token；不要用远端明文 `ws://`
或关闭证书校验。WebUI 仍通过 SSH 访问，无需给它新增公网入口。WSS 的
具体证书和反代配置由现有服务器方案提供；本模板没有部署 TLS 代理。

## 停止、恢复、升级和异常投递

设置中的“停用”停止接收和回复并保留配对；“断开 QQ 连接”移除 Runtime
连接与它的凭据。停止 NapCat 可在其部署目录执行 `docker compose stop`；
重新启动使用 `docker compose up -d`。Runtime 的退出流程取消并等待传输、
合成和未发布投递任务。NapCat 或 SSH 断线后连接会降级并重连；设置页
恢复 Runtime 连接时重新读取健康状态，不会自动再次发起配对。

OneBot `echo` 只用于匹配请求和回执。CW2 发送前保存持久标记，确认发送
回执后保存 QQ 消息 id，再确认内部投递。如果请求可能已发送、回执却
丢失，会显示 `qq_delivery_unknown` / 发送结果待确认并停止自动重发。
先在 QQ 中核对实际内容，再决定是否重新提出一条新请求。未知结果不能
当成已送达，也不能通过重启强制重发；语音合成成功不是 QQ 发送成功，
QQ 回执也不代表主人听过。

升级前停用 CW2 QQ 连接并停止 NapCat，分别备份 NapCat 的 `config/`、
`qq/` 和 Runtime 状态及安全凭据，保留权限。更改明确镜像版本/摘要后再
启动、检查登录和配对、重跑真实收发验收。备份含账号登录状态和凭据，
不要提交到仓库或分享原始日志。`docker compose down` 删除容器但保留
这两个绑定目录；删除数据目录会丢失对应配置和登录状态。

## 验收记录

下表是目标环境的现场检查清单。部署的回环边界与健康状态已验证；
真实账号场景仍待验证。记录日期、镜像摘要、
CPU 架构、NapCat/QQ 版本、Runtime commit 和测试结果；仅保留非秘密状态
及匿名化消息标识，不保存 Token 或二维码。

| 场景       | 通过条件                                                   | 当前状态                               |
| ---------- | ---------------------------------------------------------- | -------------------------------------- |
| 部署边界   | 宿主机仅回环 `13001` / `16099`，挂载目录重建容器后保留     | 回环已验证，重建待验证                 |
| QQ 登录    | 扫码后读取真实角色账号，重启后按 QQ 要求恢复               | 登录已验证，重启待验证                 |
| 主人配对   | 正确私聊指令确认；普通首条消息、群指令、错误 CODE 无法绑定 | 正确私聊已验证，其余为自动化证据       |
| 文字闭环   | 手机收正确角色回复；同一事件重放不重复生成                 | 手机收文字已验证，重放为自动化证据     |
| 访问限制   | 其他好友、群聊、图片、语音、文件不进入角色对话             | 待验证                                 |
| 显式语音   | 本轮要求后收到可播放 QQ 语音，音色和正文正确，无重复正文   | 手机播放、仅语音已验证，音色质量待验收 |
| 恢复文字   | 下一轮普通问题只有文字，没有继承语音授权                   | 手机确认只有文字，已验证               |
| 语音失败   | 不可用 TTS / 拒绝发送时收到真实状态与本轮文字兜底          | 待验证                                 |
| 取消与停用 | 旧轮次未发送语音不会在新轮次或停用后迟到发布               | 待验证                                 |
| 断线与重启 | 健康状态恢复、凭据保留，确认过的投递不重发                 | Runtime 重启恢复已验证，故障断线待验收 |
| 回执丢失   | 未确认投递不自动重发、不报告送达                           | 待验证                                 |

架构约束见 [ADR 0064](adr/0064-qq-napcat-current-turn-voice.md)。搜索/回答优化
合入后，QQ 是否开放只读搜索还需单独验收；接通文字或语音不代表已经接通搜索。
