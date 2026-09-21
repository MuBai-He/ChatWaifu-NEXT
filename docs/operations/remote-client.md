# 服务器与轻量客户端分离

后端长期运行角色、会话、记忆、渠道、模型调用、语音识别和合成。笔记本端只运行界面、Live2D、麦克风采集和音频播放；关闭客户端不会关闭服务器。当前是同一主人连接多台设备，访问令牌具有管理权限，不是多用户账户系统。

## 客户端如何使用

打开 Web 页面或新版桌面客户端，首次会出现「连接 ChatWaifu」。选择「连接远程服务器」，填写服务器根地址（如 `https://api.example.com`）和服务器 `server.env` 中生成的管理令牌。验证通过后进入界面。右下角「远程服务器 · 切换」可更换地址或恢复本地模式，服务器暂时离线时也能打开连接设置。

远程桌面不会启动 Runtime、安装 Python 或下载 STT/TTS Worker。原生启动、托盘重启、Worker 安装入口都受运行方式约束。切换时关闭本地 supervisor，并通知所有桌面窗口重新加载；旧麦克风、播放与连接随旧页面释放。会话 ID 按服务器地址分别保存。

浏览器只把服务器地址写入长期存储；管理令牌保留在当前标签页的 sessionStorage，刷新仍可连接，关闭会话后需要重新填写。桌面将选择和令牌保存在用户配置目录的 `client-connection.json`（Unix 文件权限 0600）；这是用户私有文件，不是系统钥匙串，不应随安装包或备份公开分发。模型供应商的 API Key 保留在服务器，不编入前端。

Live2D 模型和 Cubism Core 仍按 README 的资产准备流程提供给前端；源码未含的私有资产不会因为连接远程后端而自动出现。

最轻的使用方式是在浏览器打开已托管的前端网址，客户端机器无需安装开发环境。从源码运行前端则只需 Node 和项目指定的 pnpm：

```sh
make client-setup
make client
```

也可直接执行 `pnpm install --frozen-lockfile` 和 `pnpm dev:web`。这些命令不启动后端。前端开发服务器只提供静态界面；在连接页填写另一台机器的 API 地址即可。

从源码运行原生桌面另需 Rust 和系统构建依赖：`pnpm --filter @chatwaifu/desktop dev`。构建薄宿主使用 `pnpm --filter @chatwaifu/desktop build`；这两个入口无需 Python。不要使用会准备本地语音模型的旧 `make desktop` 作为轻量客户端入口。已编译的薄宿主在远程模式下无需 Rust、Node 或 Python。

## 服务器暴露 HTTPS 接口

先按 [源码服务端指南](source-server.md) 启动后端并配置常驻服务。Runtime 保持监听回环地址，由反向代理负责 TLS。将 `config/deploy/Caddyfile.example` 的域名与目录替换为实际值：

- `api.example.com` 反代到 `127.0.0.1:8765`，包括 HTTP API 和 WebSocket。
- `chat.example.com` 提供 `pnpm build:web` 生成的 `apps/web/dist/web/` 静态文件。也可以将静态前端部署在另一台机器或静态托管服务。
- 服务器 `runtime.toml` 中添加以下设置，并重启 Runtime；已有表应合并而不是重复声明。

```toml
[security]
allowed_hosts = ["api.example.com"]
allowed_origins = ["https://chat.example.com"]
```

桌面应用自身的来源已在默认允许列表中。从本机开发前端连接时，把实际来源（如 `http://localhost:5173`）加入 `allowed_origins`；协议、主机与端口必须匹配。TLS 代理机器需要可用证书以及相应端口；不要把后端改成裸 HTTP 公网监听。访问令牌放在客户端连接页，不能写进 Caddyfile、URL 或公开的 `VITE_*` 环境变量。

HTTP 请求使用 Bearer；事件和音频流 WebSocket 使用已有的短时、用途与会话绑定 ticket；WAV 回复通过认证下载并使用临时 Blob URL 播放，打断或结束时释放。健康接口仍是公开的就绪探针。

没有域名时可先使用 SSH 隧道，把本机端口转发到服务器，再填 `http://127.0.0.1:8765`。HTTP 例外只允许回环地址。**SSH 的 TCP 转发只解决 API 与 WebSocket，不会自动转发 WebRTC 媒体。**

## 远程语音

客户端语音按钮复用现有 WebRTC 通道、按住说话/开放麦克风、播放回执、打断和重连。浏览器页面本身需要 HTTPS 或 localhost 才能使用麦克风；原生客户端使用系统 WebView 的麦克风权限。

服务器默认源码配置关闭实时语音且使用诊断模型。实际语音先选择服务器可用的识别与合成方式。级联方式的示例：

```toml
[realtime]
enabled = true
connection_mode = "cascade"

[stt]
provider = "faster_whisper_worker"
worker_url = "http://127.0.0.1:8766"

[tts]
provider = "sherpa_kokoro_worker"
worker_url = "http://127.0.0.1:8767"
```

在私有 `server.env` 配置 `CHATWAIFU_STT__WORKER_TOKEN` 与 `CHATWAIFU_TTS__WORKER_TOKEN`，分别与识别和合成 Worker 使用的令牌一致。

Worker 必须在服务器上另行安装并由服务管理器常驻；URL 指向服务器能访问的 Worker，和客户端机器的地址无关。也可在语音设置中配置现有 Cloud Realtime 服务，由服务器持有密钥并遵循已有的外发同意策略。源码启动器不会擅自下载模型或选择付费服务。

跨公网/NAT 的 WebRTC 需要可达的媒体候选地址。`GET /v1/runtime/client-configuration` 是认证且禁止缓存的版本化接口，将 ICE 配置下发给浏览器；服务端 Pipecat 同时使用同一组 STUN/TURN。可以在私有 `server.env` 中配置：

```dotenv
CHATWAIFU_REALTIME__ICE_SERVERS=[{"urls":["stun:turn.example.com:3478"]},{"urls":["turn:turn.example.com:3478?transport=udp","turns:turn.example.com:5349?transport=tcp"],"username":"你的TURN用户名","credential":"你的TURN凭据"}]
CHATWAIFU_REALTIME__ICE_TRANSPORT_POLICY=all
```

替换为自己实际可用的服务。TURN 凭据用于媒体协商，会下发给持有管理令牌的客户端；普通配置展示会去除凭据。配置变更需重启 Runtime。`all` 优先使用可连接的候选；需要强制浏览器走中继可用 `relay`，此时必须配置 TURN。只配置 STUN 不能保证穿透对称 NAT、防火墙或禁 UDP 的网络；TURN 本身的监听与中继端口也需要可达。HTTPS 反代不承载这条媒体链路。

服务器提供后需实际验收：Linux 常驻、真实模型/Worker、笔记本连接、两端音频、打断、网络重连，以及关闭客户端后微信继续收发。当前代码接通了远程模式与 ICE 配置，尚不能把本机回环协商当作真实跨公网语音验收。

2026-09-21 本机验收：通过连接页完成认证并进入 Web 界面；真实浏览器与独立 Runtime 完成 WebRTC 协商（模拟麦克风），切换连接后 peer 关闭；禁用 PCM socket 后，WAV 回退携带认证并成功开始 Blob 播放。Web/桌面前端构建、类型和静态检查、相关既有回归检查通过。公网穿透和真实声音效果仍待服务器实机验证。
