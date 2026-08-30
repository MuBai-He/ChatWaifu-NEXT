# macOS 与 Web 开发

Web 产品是 Galgame 风格对话与 Avatar Lab；Desktop 产品是透明桌宠与独立控制中心。开发时可以只启动
Web + Runtime，也可以让 Tauri 接管本地服务栈。

## Web：最小启动

```bash
make bootstrap
```

全新 checkout 没有私有 TTS profile，先在两个终端运行：

```bash
# Terminal A
CHATWAIFU_TTS__PROVIDER=fake make dev-runtime

# Terminal B
make dev-web
```

- Runtime：`http://127.0.0.1:8765`
- Web：`http://127.0.0.1:5173`
- Avatar Lab：`http://127.0.0.1:5173/avatar-lab`

默认聊天模型是明确标注的确定性 Demo provider；Fake TTS 只是有效 WAV 测试音。你可以在设置里把
聊天/提取/总结/Embedding 分别切到 OpenAI-compatible 服务，密钥不会进入浏览器存储。

## 完整本地语音 Demo

完整 Demo 要求以下本地资产已经准备好：

1. `workers/asr-faster-whisper/.venv`；可通过 `make setup-stt-worker` 准备。
2. `.local/envs/qwen3-tts-mlx` 与 `.local/envs/gpt-sovits-cpufast`。
3. `.local/config/tts-profiles.toml`，其中每条路径都指向当前机器上的合法本地资产。

从示例开始，但不要提交副本：

```bash
mkdir -p .local/config
cp config/tts-profiles.example.toml .local/config/tts-profiles.toml
```

填写完成后：

```bash
make demo
# 不自动打开浏览器
make demo DEMO_ARGS=--no-open
```

这会监督 faster-whisper、Qwen3-TTS、GPT-SoVITS、Runtime 与 Web，并在 `Ctrl+C` 时清理整个进程组。
首次 STT 启动会下载公开的 multilingual `base` 模型，后续复用 `.local/models/faster-whisper/`。

::: info 真实流式边界
本地 worker 都通过统一、可取消的身份与 PCM 协议接入，但 Provider 是否“原生流式”取决于底层模型。
Windows Qwen3-TTS Torch 官方 wrapper 当前先生成完整 waveform，再交给 Runtime，因此必须声明
`native_streaming=false`。把完整 WAV 事后切成小片不会被文档称为原生流式。
:::

## macOS 桌宠

本地 Worker 与 TTS profile 已就绪后运行：

```bash
make desktop
```

Tauri 会启动 Desktop Vite profile、透明桌宠和独立控制中心，并在动态端口监督 Runtime/Worker。
窗口位置、尺寸、置顶、鼠标穿透和字幕状态写入系统应用配置目录。控制中心不是第二个会话或媒体 owner，
因此不会与桌宠重复播放音频。

没有本地 Worker 只想调试桌宠壳时，可以直接使用 package 开发入口并显式允许本地 Worker 回退；
但这不是完整语音验收：

```bash
CHATWAIFU_DESKTOP_OPTIONAL_LOCAL_WORKERS=true \
  uv run python tools/run_pnpm.py --filter @chatwaifu/desktop dev
```

## Live2D

仓库不分发 Cubism Core 或角色模型。只有在你已经合法取得 Cubism SDK for Web 5 R5 后，才执行：

```bash
make setup-live2d-vendor
make check-live2d-vendor
```

脚本把 Core、Framework bridge 与模型写入 Git 忽略目录；缺失时产品应安全回退 Fake renderer。不要把
“本机看得到宁宁”当作资产可以随源码或安装包再分发的证明。

## 开发检查

```bash
make format-check
make lint
make typecheck
make test
make check-generated
make build-web
make build-desktop-ui
```

实时改动还必须覆盖取消、迟到输出、乱序 chunk、有界缓冲、断线恢复和 teardown；仅看到一段 WAV
或一次页面成功不构成完整验收。
