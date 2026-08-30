# 模型与 TTS 配置

模型和声音都可以直接在 Web 或桌宠设置里修改，不需要反复编辑 `.env`。前端只操作 Runtime 的
typed API；Provider Key 是只写字段，Runtime 保存到 Git 忽略、权限受限的本地文件，不会回显到浏览器。

## 四个模型角色

打开 `CONFIG / 设置 → 模型`：

| 角色              | 负责什么                   | 推荐选择原则                                |
| ----------------- | -------------------------- | ------------------------------------------- |
| Chat              | 当前轮对话、Tools 选择     | 角色一致性、首 token 延迟、工具调用能力     |
| Memory extraction | 从已提交用户轮提取候选记忆 | 结构化 JSON 稳定性、低成本                  |
| Memory summary    | 合并/压缩对话和记忆上下文  | 长上下文、事实保持                          |
| Embedding         | 混合召回的语义向量         | 标准 `/embeddings`、稳定维度、中文/日文覆盖 |

每张卡独立保存 provider、model、base URL、context window、timeout 与 API Key。OpenAI-compatible
地址通常以 `/v1` 结尾；Embedding 服务必须真的实现 `POST /embeddings`，只返回 200 但响应不是标准
embedding JSON 的聊天服务器不能使用。

Demo fallback 的含义也不同：Chat/提取/总结的 `demo` 是确定性本地逻辑；Embedding 的 `local_hash`
是无需模型的可重建测试投影。它们适合开发与回归，不代表真实模型质量。

## OpenAI-compatible 示例

在对应模型卡中填写：

```text
Provider   OpenAI-compatible
Base URL   http://127.0.0.1:1234/v1
Model      你的服务实际暴露的 model id
API Key    留空（仅当本地服务确实不要求）
```

先点“测试”，确认服务支持该角色实际调用的 endpoint，再保存。聊天路由、提取、总结、Embedding 可以
来自不同服务；不要为了省一个设置把四者硬绑到同一模型。

## TTS Provider

`声音 → API / Provider` 使用一个注册表渲染所有可配置云声音；会话中的输出声音选择只切换已可用的
Provider。切换前 Runtime 会取消活动 generation，避免旧声音与新声音重叠。

| Provider                  | 路径                         | 情绪/风格                                                   | 原生流式                      |
| ------------------------- | ---------------------------- | ----------------------------------------------------------- | ----------------------------- |
| Qwen3-TTS MLX             | 本地 profile / Apple Silicon | 取决于模型，当前角色 profile 不宣称通用情绪控制             | 以实际 worker capability 为准 |
| GPT-SoVITS CPUFast        | 本地 profile                 | 参考音频与模型能力为主                                      | 以实际 worker capability 为准 |
| Qwen3-TTS Torch CUDA      | Windows `.cwpack`            | CustomVoice speaker                                         | **否（当前完整 waveform）**   |
| 阿里云百炼 Qwen3-TTS VC   | 云端实时音色                 | 保留复刻声线，不接受 CosyVoice 式情绪 instruction           | 是                            |
| 阿里云百炼 CosyVoice      | 云端实时音色                 | 支持基础情绪 instruction 的模型可合并 Character Kernel 语气 | 是                            |
| macOS say / Fake / Kokoro | 轻量回退                     | 非宁宁训练声线                                              | adapter 能力为准              |

百炼 Qwen VC 的复刻 `voice_id` 与创建时 `target_model` 严格绑定；设置里的实时模型必须完全一致。
CosyVoice 的 `instruction` 只有在所选模型支持情绪指令时才生效。两者只接收当前待朗读句段，不会上传
本地数据库、完整记忆或 Live2D 资产。

## 本地 Qwen3-TTS 与 GPT-SoVITS

本地 profile 是机器级配置：

```bash
mkdir -p .local/config
cp config/tts-profiles.example.toml .local/config/tts-profiles.toml
```

随后填写：

- 引擎 environment 与 vendor 目录。
- Qwen model directory 或 CustomVoice 的准确 speaker name。
- GPT 与 SoVITS weights。
- 参考 WAV、逐字准确 transcript 与语言。

`.local/` 被 Git 忽略。不要把绝对路径、参考音频、checkpoint 或 API Key 搬进 `characters/`、前端 env
或提交的 TOML。引擎环境准备好后：

```bash
make setup-stt-worker
make setup-neural-tts-workers
make demo
```

## 数据落点

源码开发默认使用：

```text
.local/data/chatwaifu.db
.local/data/audio/
.local/data/plugins/
.local/data/plugin-trash/
.local/models/
.local/config/model-secrets.json
.local/config/tts-profiles.toml
```

桌面安装版使用 Tauri 的 per-user config/local-data/log root，不写入不可变安装目录。卸载基础应用默认保留
用户配置与数据；公开发行前仍要提供清晰的数据删除与迁移说明。
