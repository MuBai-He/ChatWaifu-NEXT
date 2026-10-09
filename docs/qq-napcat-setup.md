# QQ 私聊与模型选择语音：NapCat Linux / Docker 接入

当前接入使用 NapCat 的正向 OneBot 11 WebSocket。每轮把 `send_voice`
作为可选回复工具提供给角色，由模型按本轮语义决定文字或语音，不检查
固定关键词，也不强制调用。普通聊天优先文字，尊重主人本轮表达的偏好。
本阶段限一个主人私聊。新增静态 PNG/JPEG 收图理解、
可选本地表情图片发送和同会话引用；主人私聊语音先转写，再进入同一角色
对话。文件和群消息尚未接入。
独立 Linux 测试服务与 NapCat 已启动，真实扫码登录
和主人私聊配对已确认；手机端已验证“文字 → 按需语音 → 文字”，
语音可以正常播放，独立测试 Runtime 重启后自动恢复绑定和在线状态。
图片和引用的自动回归已通过，手机端实际视觉、表情和引用展示仍待验收。
接收主人语音已在独立服务器部署，手机确认正确理解语音并只回复文字。
新版模型选择策略也已通过录音转写、真实模型决策和手机播放验收：模型自主
调用语音工具，本轮仅投递一条语音。这是单次样本验证，其他语义偏好与故障
场景仍需继续验收。
主动私聊文字已有独立的默认关闭设置；真实启用验收仍待 operator 选择时间和频率。
群聊成员隔离尚未实现。范围与验收见 [QQ 下一片](qq-next-slices.md)。

## 主动文字设置

QQ 连接面板中的“主动文字”仅面向已经配对的主人私聊，默认关闭。
默认空闲 45 分钟、冷却 60 分钟、每日最多 3 次，按 `Asia/Shanghai`
时区在 23:00–08:00 保持安静；一次空闲窗口最多发送一次，过期不补发。
每次保存设置后，需要主人发一条新消息才开始新的空闲窗口。

“检查主动问候资格”只显示资格、时窗和阻止原因，不生成回复或发送 QQ。
最近主动结果分别显示取消原因和实际发送结果；已有的成功 receipt 不会被关闭开关抹掉。
关闭、解绑、新主人输入和权限变化会取消未发送的主动工作。
这项功能只发文字，不调用语音、表情、搜索或其他工具；桌面主动开关不授权 QQ。
入站语音的回复仍由模型按本轮语义决定。
独立测试服务器现已部署这项设置，策略 revision 为零且关闭；真实启用和手机主动
收信尚未验收。源码和自动回归通过不代表已取得主动发送授权。

## 图片与引用

一次消息支持 1–4 张静态 PNG/JPEG，每张至多 5 MiB，总计至多 20 MiB；
下载整批最多 20 秒。图片只供本轮理解，不自动保存照片或学习表情。
GIF、动画、失效或无法读取的图片会回复重新发送提示；新消息可以取消尚未
完成的图片处理。

在 QQ 设置开启“允许角色发送表情图片”后，默认角色可根据对话附上现有
预设或已保存的表情图片。图片发送失败不会撤回已经发送的文字。
这是现有表情库投递能力，不包含生成任意图片或发送任意网页图片。

引用只读取当前主人会话里已接收或已确认发送的文字，引用的语音可使用其
已确认的说话文本。未记录、已删除或不明确的引用会说明无法读取；引用图片
不会重新下载，新的视觉问题请重新发图。引用中的旧语音请求不是本轮方式指令。
角色回复带引用的消息时，会引用当前这条消息，保留手机端回复关系。
设计见 [ADR 0065](adr/0065-bounded-qq-images-and-reply-references.md)。

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

首次扫码成功后，在私有 `.env` 中把 `NAPCAT_ACCOUNT` 设为**角色账号**的 QQ
号码，再用原 Compose 项目重建容器。模板把它传给镜像的 `ACCOUNT`；已验证
的 v4.18.28 镜像 `/app/entrypoint.sh` 会据此使用 QQ 的 `-q` 快速登录入口。
仅挂载 `qq/` 而不指定账号，容器重建后仍可能停在扫码页面。登录凭据过期
或 QQ 要求重新验证时仍需扫码；以实际登录状态和认证后的 OneBot RPC 为准。
不要填主人账号，也不要把已填写的 `.env` 提交到仓库。

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

为当前角色配置可用的 TTS 服务与音色后，可以发送“想听听你的声音”。工具
只接收要说的文字，Runtime 固定当前主人、角色音色和本轮身份。一次成功
语音使用语音气泡承载回答，不额外重复发送整段文字。失败时会把本轮内容
改为文字并说明情况。模型自行决定是否调用，普通聊天优先文字；执行仍限定
当前有效的主人私聊与 generation。桌面手动调用、旧轮次或其他渠道不能借此
发送语音，模型也不能指定其他接收人。

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

## 接收主人语音

接收主人私聊中的一条 QQ 语音，先转写为文字，再进入同一角色对话。
可带引用，不支持语音与文字或图片混合，也不接受多条语音的组合消息。
最终转写进入模型，由角色选择文字或调用本轮语音工具，普通聊天优先文字。
收到语音本身不会自动要求语音回复。

需要配置已认证的本地 `faster_whisper_worker`。未配置、识别失败或音频
过期时，会发送“刚才发来的语音我没听清，能再说一次或发文字吗？”的
文字提示。原始音频不写入 Runtime 数据库或媒体库；成功后的最终转写
按普通主人文字进入对话与记忆规则。被取消或重启打断的旧语音不自动重取。

下载使用固定 NapCat 版本的认证分块接口，限制 5 MiB、20 秒；WAV 限制
完整 PCM16、8–48 kHz、单声道或双声道、60 秒。整个转写准备限制 60 秒。
仅返回可用 WAV 不代表 QQ 的实际语音编码被正确解码，仍需手机验收。

独立服务器 STT worker 使用新的随机 Token、回环端口和私有环境文件，
可只读复用固定离线模型；不要复用另一测试环境正在运行的 worker。
Runtime 配置 `CHATWAIFU_STT__PROVIDER=faster_whisper_worker`、
`CHATWAIFU_STT__WORKER_URL` 和对应的私有 `CHATWAIFU_STT__WORKER_TOKEN`。
worker 建议 `CHATWAIFU_STT_WORKER_PRELOAD=true`、
`CHATWAIFU_STT_WORKER_LOCAL_FILES_ONLY=true`、
`CHATWAIFU_STT_WORKER_MAX_ACTIVE_JOBS=1`。取消后的 CPU 推理或模型加载
仍占容量，额外请求会及时失败；保持环境文件 `0600`，不要输出凭据。

中文识别可在这个独立 worker 配置 `CHATWAIFU_STT_WORKER_BEAM_SIZE=5`
和 `CHATWAIFU_STT_WORKER_CHINESE_INITIAL_PROMPT=以下是简体中文普通话对话。`。
默认仍为 beam 1、无提示；中文提示仅用于 `zh`，不包含语音发送指令，
也不保证识别正确。原转写保持不变；Runtime 不用关键词、简繁转换或
同音猜测决定语音权限。模型按语义理解本轮内容和回复方式偏好，引用与历史
仍是数据。这一行为策略需要真实模型复测，不能由可选工具的存在来证明。

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
主人配对、文字/按需语音/恢复文字和 Runtime 重启已验证，其余场景按表记录。
记录日期、镜像摘要、
CPU 架构、NapCat/QQ 版本、Runtime commit 和测试结果；仅保留非秘密状态
及匿名化消息标识，不保存 Token 或二维码。

| 场景         | 通过条件                                                     | 当前状态                                     |
| ------------ | ------------------------------------------------------------ | -------------------------------------------- |
| 部署边界     | 宿主机仅回环 `13001` / `16099`，挂载目录重建容器后保留       | 回环、持久挂载重建已验证                     |
| QQ 登录      | 扫码后读取真实角色账号，重启后按 QQ 要求恢复                 | 重建后扫码恢复已验证，免扫码待验收           |
| 主人配对     | 正确私聊指令确认；普通首条消息、群指令、错误 CODE 无法绑定   | 正确私聊已验证，其余为自动化证据             |
| 文字闭环     | 手机收正确角色回复；同一事件重放不重复生成                   | 手机收文字已验证，重放为自动化证据           |
| 访问限制     | 其他好友、群聊、文件、视频和不支持的混合媒体不进入角色对话   | 自动化拒绝已验证，手机待验证                 |
| 图片与引用   | 主人静态图片理解正确；引用显示正确；可选择开启表情图片       | 独立部署及自动化已验证，手机待验证           |
| 接收语音     | 正确转写主人语音；模型按本轮语义和偏好选择回复方式           | 普通录音理解及文字、新版录音到语音播放已确认 |
| 文字请求语音 | 本轮文字要求后收到可播放 QQ 语音，音色和正文正确，无重复正文 | 文字及录音请求的手机播放、仅语音已验证       |
| 模型选择     | 同一普通消息可选择文字或语音，提供工具不强制调用             | 自动化两种决策及真实模型语音、手机播放已验证 |
| 语音失败     | 不可用 TTS / 拒绝发送时收到真实状态与本轮文字兜底            | TTS 不可用文字兜底已验证，QQ 拒绝发送待验收  |
| 取消与停用   | 旧轮次未发送语音不会在新轮次或停用后迟到发布                 | 待验证                                       |
| 断线与重启   | 健康状态恢复、凭据保留，确认过的投递不重发                   | Runtime 重启、容器重建后重连和记录保留已验证 |
| 回执丢失     | 未确认投递不自动重发、不报告送达                             | 待验证                                       |

架构约束见 [ADR 0064](adr/0064-qq-napcat-current-turn-voice.md)。搜索/回答优化
合入后，QQ 是否开放只读搜索还需单独验收；接通文字或语音不代表已经接通搜索。

下一步只读搜索集成必须保留当前语音完成回复逻辑，并单独设计 QQ 主人本轮
授权：仅允许 builtin `web.search/search`、`web.read/read`，核对已接纳的主人
私聊和 active session/turn/generation，保留桌面上的权限确认，不创建永久
grants。仅加入工具白名单会等待 QQ 中不存在的本地确认界面，因此当前仍
未开放搜索。搜索窗口提供冻结提交后，再审查合并 provider 配置、Runtime
Skills 注入和工具循环；搜索后按需语音应预留语音调用额度，网页内容不得
启用语音或写操作。组合场景、服务错误、取消/旧结果、零永久授权和桌面
确认回归通过后，才更新 QQ 的能力范围。

### 新群成员批量注册

在 QQ 群管理输入群号并读取成员，先查看 QQ 号和昵称预览。读取不会新增
参与者或身份关联。点击“确认自动注册并关联未关联成员”后，Runtime 会重新
核对成员，并一次性注册缺失的参与者及其 QQ 身份。同名昵称不合并；已有
身份、手动名称及已撤销的关联继续保留。观测超过 60 秒或成员变化时，请重新
读取和确认。

支持 2–2,000 名非机器人自身成员。成员按 50 人分页，可按 QQ 号或参与者名称
搜索。新群成员完成关联后，“允许 AI 回复该成员”默认勾选，可逐个取消；
“允许 AI 回复全部已关联成员”和“取消全部 AI 回复选择”可批量调整。
已保存的群成员权限及撤销关联保持原值。核对共享范围后创建的群路由仍关闭，
需要重新读取并确认启用，才会开始回复。也可手动关联已有参与者。
