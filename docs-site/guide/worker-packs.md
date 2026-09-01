# Qwen3-TTS 与 faster-whisper Worker Packs

`.cwpack` 是 ChatWaifu NEXT 的版本化本地 AI 分发单元。每个 pack 自带可迁移的 x64 CPython、Worker
代码、原生库、模型与带校验和的 manifest；安装后的 Runtime 负责动态端口、Bearer Token、健康检查、
重启与激活版本。

::: warning 发布与许可边界
原生 Windows x64/RTX 3090 已完成 CUDA 与安装态技术验收，但宁宁 checkpoint、Live2D 与声线仍是
owner-only 资产，不能因构建通过就公开发布。`-SkipModelSmoke` 只能排查构建环境，不能作为发行验收
结果。
:::

## 为什么不塞进基础安装包

- CUDA/PyTorch 与模型权重体积大、更新周期不同。
- Qwen checkpoint 与角色声线可能只允许 owner 本地使用。
- STT 可以选 CPU pack，TTS 可以选 CUDA pack，不强制每台机器下载全部能力。
- Pack 能被完整校验、原子安装和单独回滚，不污染不可变的 App 资源目录。

## faster-whisper Base CPU int8

准备一段真实、短的 PCM16 语音 WAV（8–48 kHz，mono/stereo 均可），然后在 Windows PowerShell：

```powershell
.\tools\windows\bootstrap_x64.ps1
.\tools\windows\build_faster_whisper_worker_pack_x64.ps1 `
  -SmokeWav C:\validation\speech.wav `
  -PackVersion 0.1.0
```

默认会从固定 revision 下载公开 `Systran/faster-whisper-base`，完整复制到 pack 并用 offline flags
启动安装后副本完成转写 smoke。已有审查后的本地快照可增加：

```powershell
-ModelSource C:\models\faster-whisper-base
```

## Qwen3-TTS CUDA 12.6

传入已解压、你有权使用的 CustomVoice checkpoint：

```powershell
.\tools\windows\bootstrap_x64.ps1
.\tools\windows\build_qwen3_tts_worker_pack_x64.ps1 `
  -ModelSource C:\models\nene-qwen3-tts\checkpoint-epoch-0 `
  -Voice ayachi_nene_local `
  -PackVersion 0.1.0
```

构建器会校验 Qwen `custom_voice` 配置与 speaker，固定 Qwen 源 revision、PyTorch/torchaudio 2.7.1
`cu126`、SDPA 与 `cuda:0`，随后从安装后的 pack 分别生成中文、日文非静音 WAV。

::: info Qwen Torch 不是原生流式
当前官方 Torch wrapper 会先生成完整 waveform，再交给 Worker，因此 capability 正确报告
`native_streaming=false`。Worker Protocol v2 支持 PCM chunk，不代表所有 backend 都能边推理边输出。
若未来上游提供真正增量 generator，必须同时补上取消、late chunk、sequence 与有界缓冲测试后才能改标记。
:::

## 输出与校验

```text
dist/windows/worker-packs/*.cwpack
dist/windows/worker-packs/*.cwpack.sha256
dist/windows/worker-packs/smoke/
```

两个构建器都会检查 `.exe`、`.dll`、`.pyd` 的 PE machine `0x8664`，生成并完整验证 Zip64 archive，
原子安装到临时 pack root，再使用临时 loopback Token 启动 installed entrypoint、执行真实推理、卸载并
确认端口关闭。

## 安装到 ChatWaifu NEXT

基础 NSIS 与 `.cwpack` 分开分发。用户可以第一次完全不装本地模型，先用文字聊天或云端 Provider；
之后打开 **设置 → 数据 → Worker Pack 管理 → 选择并安装**，选中单个 `.cwpack`。应用会完整校验、
按当前用户原子安装并自动重启本地 Runtime。重装或普通卸载会保留已安装的 pack；安装成功后日常运行
不依赖下载的归档，但修复同版本仍需要完全相同的原始文件。

发布/恢复人员也可以从普通 PowerShell 使用仓库脚本：

```powershell
.\tools\windows\install_worker_pack_x64.ps1 `
  -ArchivePath .\dist\windows\worker-packs\chatwaifu-faster-whisper-base-cpu-int8-0.1.0.cwpack

.\tools\windows\install_worker_pack_x64.ps1 `
  -ArchivePath .\dist\windows\worker-packs\chatwaifu-qwen3-tts-nene-cu126-0.1.0.cwpack
```

仅检查归档而不切换活动版本：

```powershell
.\tools\windows\install_worker_pack_x64.ps1 `
  -ArchivePath C:\path\to\worker.cwpack `
  -VerifyOnly
```

重启 ChatWaifu NEXT 后，冻结 Runtime 会在动态、认证的 loopback 端口启动活动 Worker。Qwen 的真实
CUDA probe 或 Whisper 离线加载失败时，Runtime 必须保留可用 fallback，不能把损坏 Provider 宣告为可用。

## 安全与许可

- 不要把 Hugging Face/ModelScope Token 写进 pack 或脚本参数日志。
- 不要把角色 checkpoint、参考音频、smoke 音频提交到仓库。
- 每个 pack manifest 都要记录模型与依赖许可；角色声线还要单独完成授权审查。
- 公开下载目录需要签名、许可清单与目标机器验收，当前尚未开放。
