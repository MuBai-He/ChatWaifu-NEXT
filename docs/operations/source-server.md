# 从源码运行常驻后端

此入口运行同一个 ChatWaifu Runtime，供单主人服务器使用。不需要启动桌面、安装 Node/Rust、构建前端或下载语音模型。安装包、桌面远程模式、完整公网 Web 和跨网络语音仍是后续工作。

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

之后访问本机 `http://127.0.0.1:8765/v1/runtime/health` 查看就绪状态。健康接口不要求管理凭据；健康通过仅说明后端启动完成。正式公网访问还需 HTTPS 反代、认证入口、Host/Origin 配置和媒体访问控制，不要直接修改为 `0.0.0.0`。

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
