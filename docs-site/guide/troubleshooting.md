# 故障排查

先判断问题发生在哪一层：Web、Tauri Host、Runtime、STT/TTS Worker、Provider，还是私有 Live2D 资产。
不要看到白屏或没声音就同时重装所有依赖。

## `make demo` 报 TTS profile / environment missing

这是预期保护：完整 Demo 需要不入库的本地引擎与声线。全新 checkout 请改用：

```bash
make bootstrap
CHATWAIFU_TTS__PROVIDER=fake make dev-runtime
# 另一个终端
make dev-web
```

需要完整语音时，再按[模型与 TTS 配置](/guide/configuration#本地-qwen3-tts-与-gpt-sovits)创建
`.local/config/tts-profiles.toml` 并准备两个引擎环境。

## `pnpm: command not found`

仓库命令不要求全局 pnpm。运行：

```bash
make bootstrap
```

或直接通过 wrapper：

```bash
uv run python tools/run_pnpm.py install --frozen-lockfile
```

不要把一个不兼容的全局 pnpm 写死进 Makefile。

## `address already in use` / 8765、5173 被占用

`make demo` 会在创建 Worker 前检查固定 Runtime/Web 端口。先正常 `Ctrl+C` 停止旧 Demo；如果旧进程
已失去终端，再确认监听进程属于 ChatWaifu 后结束它。不要用针对整个 Python/Node 进程名的全局 kill。

也可以把 Runtime 与 Web 分开启动，便于看到哪一侧失败：

```bash
CHATWAIFU_TTS__PROVIDER=fake make dev-runtime
make dev-web
```

桌面宿主使用动态 Runtime 端口，日志中的端口每次变化是正常设计。

## 页面能开但没有声音

按顺序检查：

1. 当前 TTS Provider 是否真的显示可用，而不是仅保存了配置。
2. 浏览器/WKWebView 是否已获得一次用户手势以允许播放。
3. Console 是否出现 `AbortError: play() ... pause()`；这通常意味着旧 generation 被打断，播放器必须吞掉预期 abort 而不是重复重播。
4. Runtime 是否生成 `tts.segment_ready`，音频 URL 是否返回 206/200。
5. 当前 generation 的 playback ACK 是否持续推进，是否被新的 turn 取消。
6. Worker capabilities 的 sample rate、format 与 `native_streaming` 是否如实。

完整 WAV 生成成功只证明 Provider 侧合成，不证明桌宠实际播放、字幕推进与 ACK 都成功。

## 麦克风按钮不可用

- 全新 `make dev-runtime` 默认 STT disabled；使用完整 Demo 或安装 faster-whisper Worker Pack。
- 检查浏览器/系统麦克风权限和当前设备是否丢失。
- 默认是按住说话；自由对话需要显式开启，并会听到附近人声。
- VAD 只判断“有人在说话”，不是说话对象识别；开放麦模式应结合“宁宁”等 wake phrase 门控。
- 设备变化或 WebRTC 失败会进入有界重连；显式“断开麦克风”不应自动重连。

## Live2D 白屏或显示 Fake

源码仓库不含 Cubism Core/模型。先运行：

```bash
make check-live2d-vendor
```

Windows 虚拟显卡常无法及时解码 8192px 纹理；开发脚本会保留 `.source.png` 并生成本地 4096px 运行
副本。不要把优化后的私有纹理提交。设置窗口白屏还要区分“第二 WebView 加载失败”和“透明桌宠覆盖
在设置窗口上方”；冷启动、检查真实窗口层级与 surface marker，而不是只看 dev server 已 ready。

## Windows 显示 Local Worker unavailable

缺少 `.local/config/tts-profiles.toml` 或源码 worker venv 时，Windows 开发宿主会降级为 Demo Runtime；
这不会让 App 启动失败。本地语音要么准备源码环境，要么安装对应 `.cwpack`。目标安装版不会读取
Mac 上的路径，也不会自动把另一台机器的模型环境“调起来”。

## `nltk_data ... Security Violation` / 代理 Fake-IP

项目使用固定 revision、SHA-256 校验的 `punkt_tab` 本地归档。运行：

```bash
make setup-nltk-data
```

之后 Runtime 会在导入 Pipecat 前指向 `.local/nltk_data`，不应再触发 NLTK 在线下载。若仍出现日志，
确认启动的是当前 checkout 的 `tools/run_runtime.py`/冻结 Runtime，而不是残留旧进程。

## Embedding 返回 200 仍测试失败

OpenAI-compatible Chat endpoint 不一定实现 `/embeddings`。检查服务日志是否写着 “Unexpected endpoint or
method”，并确认响应包含标准 `data[].embedding` 浮点数组。聊天模型与 Embedding 可以配置为不同 base URL。

## 仍无法定位

收集最小证据：commit SHA、OS/架构、启动命令、Runtime/Worker capability、第一条错误、实际端口、
是否使用私有资产，以及可复现步骤。不要附上 API Key、Token、数据库、参考音频或模型权重。
