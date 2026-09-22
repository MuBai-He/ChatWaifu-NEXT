# 从源码运行常驻后端

此入口运行同一个 ChatWaifu Runtime，供单主人服务器使用。不需要启动桌面、安装 Node/Rust、构建前端或下载语音模型。桌面与 Web 的远程连接、HTTPS 暴露及跨网络语音配置见 [轻量客户端部署指南](remote-client.md)。服务端安装包暂缓。

## 第一次启动

准备 Python 3.12、uv、GNU Make，并将源码放在一个长期保留的目录。在源码根目录运行：

```sh
make server-setup
make server
```

首次启动会准备固定版本且校验摘要的 NLTK 分句数据（不是语音模型），需要网络；之后复用缓存。默认监听 `127.0.0.1:8765`。`Ctrl+C` 或服务停止信号走 Runtime 现有的退出清理流程。

默认状态目录为 `~/.local/share/chatwaifu-server`，与桌面数据和源码目录分开：

| 路径           | 用途                                                   |
| -------------- | ------------------------------------------------------ |
| `runtime.toml` | 服务配置，首次从 `config/server.toml` 复制             |
| `server.env`   | 首次生成的管理凭据及自行配置的模型密钥，不在日志中打印 |
| `config/`      | Runtime 持久配置和已有的只写密钥存储                   |
| `data/`        | SQLite、照片、表情等持久状态                           |
| `nltk_data/`   | 已校验的分句数据                                       |

初始化不会覆盖已有文件或自动轮换管理凭据；源码更新与服务重启复用这些数据。已有桌面数据不会自动迁移。启动入口明确读取此处的 `server.env`，不会读取源码根目录的开发 `.env`。显式 `CHATWAIFU_*` 进程环境仍按既有配置规则覆盖文件。

自定义目录时，所有命令使用相同参数：

```sh
make server-setup SERVER_ARGS='--state-dir /srv/chatwaifu-state'
make server SERVER_ARGS='--state-dir /srv/chatwaifu-state'
```

目录属于运行服务的用户。源码服务入口持有进程锁，同一目录的第二次启动会直接拒绝，进程退出后自动释放；不要使用其他入口绕过它同时访问这份数据库。同机桌面与服务器同时运行时，也需要给服务器配置不同端口。

## 模型与认证

默认使用离线 Demo 聊天模型，关闭实时语音。首次启动成功不代表已经接通真实模型或微信。

在 `runtime.toml` 的 `[llm]` 中填写真实配置：

```toml
[llm]
provider = "openai_compatible"
base_url = "https://你的模型服务/v1"
model = "你的模型名称"
```

需要密钥时，在私有 `server.env` 中添加 `CHATWAIFU_LLM__API_KEY=...`，随后重启服务。也可通过已有模型管理 API 保存角色使用的模型配置；数据库中已保存的配置按现有规则优先于首次启动默认值。

初始化的 `CHATWAIFU_SECURITY__ADMIN_TOKEN` 是访问本服务的管理凭据，与模型 API Key 不同。不要把它提交到 Git 或编入公开前端文件。私有 API 使用 `Authorization: Bearer <管理凭据>`；WebSocket 使用已有的短时 ticket。

默认保留回环监听。最先可用 SSH 转发访问：

```sh
ssh -N -L 8765:127.0.0.1:8765 用户@服务器
```

之后访问本机 `http://127.0.0.1:8765/v1/runtime/health` 查看就绪状态。健康接口不要求管理凭据；健康通过仅说明后端启动完成。正式远程访问按 [轻量客户端部署指南](remote-client.md) 配置 HTTPS 反代、Host/Origin 与媒体路径，不要直接修改为 `0.0.0.0`。

## Linux：退出 SSH 后持续运行

使用 systemd 用户服务管理后台运行与失败重启。先结束前台的 `make server`，再在源码目录运行：

```sh
mkdir -p ~/.config/systemd/user
make -s server-unit > ~/.config/systemd/user/chatwaifu-runtime.service
systemctl --user daemon-reload
systemctl --user enable --now chatwaifu-runtime.service
```

使用自定义状态目录时，生成 unit 也要传相同 `SERVER_ARGS`。生成的 unit 固定源码、虚拟环境和状态目录的绝对路径，不包含密钥。不要删除或移动正在使用的源码目录；升级可以原地拉取新代码。

为使用户退出登录后仍运行，并在机器启动时恢复，需要服务器管理员启用该用户的 lingering：

```sh
sudo loginctl enable-linger "$USER"
```

日常管理：

```sh
systemctl --user status chatwaifu-runtime.service
journalctl --user -u chatwaifu-runtime.service -f
systemctl --user restart chatwaifu-runtime.service
systemctl --user stop chatwaifu-runtime.service
```

服务失败会重启，120 秒内失败 5 次后停止重试，避免配置错误持续循环。修复后执行 `systemctl --user reset-failed chatwaifu-runtime.service` 再启动。日志由系统 journal 管理，不另建无限增长的日志文件。

macOS 可以使用相同源码入口前台运行；此处的后台服务流程适用于有 systemd 用户服务的 Linux。没有 systemd 的 NAS/容器应由其现有服务管理器执行虚拟环境中的 `python tools/run_server.py run`，不叠加桌面 supervisor。

## 微信、升级和备份

微信轮询与投递属于 Runtime，不依赖桌面窗口。但是扫码接入仍要求可用的系统安全凭据存储：纯无桌面 Linux 未必有可用的 Secret Service/Keyring。缺失时会报告不可用，不会自动降级为明文文件。目标服务器的凭据方案和真实收发需在部署时确认，复制 SQLite 本身不能迁移微信登录凭据。

升级前停止服务，备份整个状态目录（其中含私密资料），再拉取代码、运行 `make server-setup`、启动服务。首次启动会执行已有 SQLite 迁移；若升级失败，不要直接让旧版本读取迁移后的库，应恢复对应代码与备份。若移动源码或虚拟环境，重新生成 unit 并执行 `daemon-reload`。

此阶段的完成范围是源码启动、状态持久化和服务管理入口。目标 Linux 上的 systemd、退出 SSH 后常驻、重启恢复、真实模型和微信消息处理仍需服务器到位后的现场验收。

2026-09-21 本地 macOS 实际进程验证：在独立临时状态目录启动，关闭 stdin 后继续提供服务；未认证私有请求返回 401；创建的会话和管理凭据在 SIGTERM 正常清理及重启后保留；另一进程不能同时获取同一状态目录的锁。源码初始化与 unit 生成命令已执行，日志和 unit 未包含生成的凭据。

### 微信绑定返回 503

若 `/v1/channel-auth-sessions` 返回 `channel_secure_store_unavailable`，
表示运行后端的设备没有可用或已解锁的系统凭据库，并非客户端连接失败。
当前渠道凭据适配器依赖 OS keyring；无桌面 Linux 上的
`keyring.backends.fail.Keyring` 不可用于保存微信授权。
桌面端默认继续使用系统 keyring。无桌面 Linux 服务器可在 `runtime.toml` 的
顶层（第一个 `[section]` 前）显式设置：

```toml
channel_credential_backend = "encrypted_file"
```

新的源码服务器配置默认启用此选项；已有配置不会自动覆盖。重启 Runtime
后会初始化 `config/channel-vault-key/master.key` 和
`data/channel-vault/credentials.fernet`。目录必须由 Runtime 用户拥有且为
0700，文件为 0600。不要配置明文 keyring fallback，也不要删除密钥尝试修复
错误。密钥丢失、文件损坏或权限不符会拒绝读写，保留已有数据。

将密钥与密文分开备份；仅复制数据库不包含渠道授权。服务器账户/root 能
读取密钥并解密，此方案不防御主机账户失陷。扫码仍需用户在微信确认。
先确认二维码可以生成，再验收扫码收发及服务器重启后的恢复。客户端 Mac
钥匙串不会自动成为远程服务器的凭据库。见 ADR 0056。

2026-09-22 局域网服务器已启用该后端：Linux 临时凭据读写/删除通过，生产
目录与文件权限通过；Runtime 重启后原密钥和密文保持不变且仍可读取。
微信授权接口重启前后均返回 201/pending，二维码内容非空；验证会话已取消。
用户扫码绑定、实际消息收发及已绑定凭据的重启恢复仍需实机验收。

### 客户端网络恢复

桌宠首次加载及设置页首次连接/健康检查失败后，按 1、2、4、8、16、30 秒
退避重试（之后最多每 30 秒一次）；网络恢复事件触发立即尝试。
设置页错误提示提供“立即重连”。401/403 等不可重试客户端错误停止定时
重试，需检查连接配置。卸载或切换连接会取消请求和重试计时器。
已建立的桌宠事件/音频连接继续使用原有 socket 恢复机制。
渠道页在恢复连接后重新读取列表；不会自动重放发送消息或扫码绑定等写操作。

### Runtime 直接终止 TLS

源码启动器支持成对设置环境变量 `CHATWAIFU_SERVER_TLS_CERT`（PEM 完整证书链）
和 `CHATWAIFU_SERVER_TLS_KEY`（PEM 私钥）。只设置一个会拒绝启动；均不设置
仍使用 HTTP。启动器禁用 uvicorn 的代理头解释，避免把转发头当作真实 TLS。
证书和密钥路径可通过 systemd drop-in 的 `Environment=` 配置，目录 0700、
文件 0600；不得提交密钥或含密钥的压缩包。

当前服务器 Runtime 在 `127.0.0.1:8765` 终止 TLS，用户级 systemd socket
监听 `0.0.0.0:18443`，通过 `systemd-socket-proxyd 127.0.0.1:8765`
透传加密 TCP。公网入口为 `https://mubai.website:18443`，不需要 443。
`security.allowed_hosts` 包含域名，个人助理的 `google_oauth_https_origin`
与该入口完全一致。OAuth 环境文件仅由 Runtime 用户读取。

旧 LAN nginx 的 `/v1/` 上游改为 `https://127.0.0.1:8765`，开启
`proxy_ssl_server_name on`、`proxy_ssl_name mubai.website`、
`proxy_ssl_verify on`、`proxy_ssl_verify_depth 3`，并使用系统 CA 文件
`/etc/ssl/certs/ca-certificates.crt`。不要关闭证书校验来解决上游 502。
旧 HTTP 入口用于普通连接，Google 授权应使用新的 HTTPS 入口。

2026-09-22 已验证域名 TLS 信任链、鉴权状态接口、旧 LAN 健康接口。
现有证书有效期截至 2026-11-29；目前是手工安装，不代表已配置自动续期。
续期时替换状态目录 `tls/fullchain.pem` 与 `tls/privkey.key`，保留权限，
重启 `chatwaifu-runtime.service`，再验证 HTTPS 和 LAN 代理。首次配置前的
备份位于服务器 `chatwaifu-server/backups/https-20260922/`。
