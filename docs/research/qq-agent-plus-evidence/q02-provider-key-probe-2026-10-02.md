# Q02 当前配置密钥与搜索供应商探针

日期：2026-10-02。执行约束：不调用 AGY，由主代理直接执行和审查。探针只在进程内读取当前 CW2 本地聊天密钥，未输出、写入或保存密钥值。

## 模型端点

当前 Q02 端点为 `https://mubai.website:8318/v1`，目标模型为 `gemini-3.8-flash-high`。进程内使用 `/Users/mubai/Desktop/CW2/.local/config/model-secrets.json` 的 `chat` 密钥：

- `GET /models`：HTTP 200，返回 18 个模型 ID，包含 `gemini-3.8-flash-high`、`gemini-3.7-flash-high`、`gemini-3.6-flash-high`、`gemini-3.1-flash-lite`、`claude-sonnet-4-6` 和 `claude-opus-4-6-thinking`。
- `POST /chat/completions`：HTTP 200，模型返回 `OK`，约 5.3 秒；响应包含 `prompt_tokens`、`completion_tokens`、`completion_tokens_details.reasoning_tokens` 和 `total_tokens`。请求的 `gemini-3.8-flash-high` 在响应 `model` 字段中归一化为 `gemini-3.8-flash`，因此评测必须同时记录请求模型和供应商返回模型，不能只凭请求参数确认命中 High 变体。
- 带同一密钥的工具调用探针：HTTP 200，Gemini 返回 `web.search` tool call，约 5.3 秒；供应商 usage 可读。
- 旧的主工作树 SQLite 路由仍记录 `http://192.168.1.103:8317/v1 / gemini-3.7-flash-high`；该 8317 地址本轮 TLS 连接失败，不作为当前 Q02 端点。

## 搜索与抓取供应商

### Jina Reader

`https://r.jina.ai` 不需要额外 API key。对 CAAC 官方页面
`https://www.caac.gov.cn/XWZX/MHYW/202506/t20250626_227805.html` 的运行时适配器探针结果：

- `dns_resolver=system` 被公共地址守卫拒绝，因为本机 DNS 返回 Fake-IP `198.18.0.249`；这不是来源站点失败。
- `dns_resolver=cloudflare` HTTP 200，约 20.1 秒，正文 8,286 字符；正文保留 `3C`、`召回`、`6月28日`、`型号`、`批次`。
- 标题为“民航局：禁止旅客携带无3C标识及被召回的充电宝乘坐境内航班”。

因此 Jina 适合作为当前可用的 reader，调用时必须显式使用 Cloudflare DNS 或修复本机公共 DNS；它本身不解决搜索发现问题。

### Firecrawl

当前配置和环境中没有 Firecrawl API key。用聊天密钥请求 `POST https://api.firecrawl.dev/v2/search` 返回 HTTP 401 `Unauthorized: Invalid token`；聊天密钥不能代替 Firecrawl 密钥。后续直接探针发现同一接口当前允许不带认证的匿名试用路径并返回 HTTP 200，但它没有租户额度或稳定性保证，只在隔离 Runtime/评测器通过显式 `firecrawl_allow_anonymous=true` 使用。没有真实 Firecrawl key 前，不宣称其生产 Search/Scrape 链路可用，也不切换默认配置。完整结果见 [Firecrawl 匿名 Runtime 批次](q02-firecrawl-anonymous-runtime-batch-2026-10-02.md)。

### 搜索候选

- DuckDuckGo Lite 当前请求返回 HTTP 202 challenge，运行时已将其归类为 `web_search_challenge`；不使用伪装浏览器 UA 绕过风控。
- Jina Search (`s.jina.ai`) 返回 HTTP 401，要求独立 API key。
- Google/Bing HTML 页面虽返回 200，但本轮没有得到稳定、可验证的 CAAC 官方结果排序；页面含 challenge/重定向结构，不作为生产适配器。

## 结论

模型密钥可直接用于 8318 的 Gemini 真实模型和工具调用复测；搜索密钥并不存在，不能用模型密钥冒充 Firecrawl/Jina Search 认证。当前最可靠的无新增密钥组合是 DuckDuckGo Lite（发现可能被 challenge 阻塞）+ Jina Reader（`cloudflare` DNS），因此 Q02 的真实来源批次仍需在可用搜索发现服务或用户提供 Firecrawl/Jina Search 密钥后重跑。默认 `duckduckgo_lite + builtin`、生产 persona、生产模型路由和生产数据库均未修改。

## 真实 Runtime 语音链路复测

同日使用上述 8318 的 `chat` 密钥和现有 TTS 密钥，在临时目录创建隔离
`RuntimeContainer`；临时 SQLite、audio 目录和 secret store 在进程退出后删除，生产
数据库、配置和密钥文件的修改时间保持不变。探针脚本与结果分别为
[`q02-real-runtime-tts-playback-20261002.py`](../../../.local/research/q02/q02-real-runtime-tts-playback-20261002.py)
和同名 `.json` 结果文件。

- Chat 路由为 `openai_compatible / gemini-3.8-flash-high / https://mubai.website:8318/v1`，临时实验上下文窗口为 32768；请求内容估算 2171 token，未省略历史或工具前缀。适配器请求 usage 后，供应商返回 `prompt_tokens=1542`、`completion_tokens=12`、`reasoning_tokens=296`、`total_tokens=1850`。第一次连接尝试由适配器记录为 connection error 并重试，最终回合成功；因此延迟记录为包含重试的约 12.0 秒。
- 真实 `ConversationService.submit_text` 使用 `output_modes={"text","audio"}` 生成一条完成回复：`你好，这是 Q02 真实语音链路测试。`，持久 generation 状态为 `completed`，并产生 `assistant.audio_chunk_queued`。
- TTS 选择 `aliyun_cosyvoice_realtime / cosyvoice-v3.5-plus`，使用当前密钥中绑定的 CosyVoice 音色；实际 PCM/WAV 为单声道 24 kHz、4460 ms，音频资产和 `playback_segments` 均存在。
- 通过正式 `PlaybackService.acknowledge` 提交 `started(0 ms)`、`progress(2230 ms)`、`stopped(reason=ended, 4460 ms)`。最终数据库状态为 `state=completed`、`played_pts_ms=4460`、`stop_reason=ended`，`spoken_text` 已写入；spoken memory fact 仍为正常的异步 `pending` 状态。

这次证明了 **8318 Gemini -> ConversationService -> CosyVoice -> audio asset ->
PlaybackService ACK** 的真实 Runtime 路径，不证明物理扬声器实际播放或用户听见；也不证明 QQ/微信外部投递、稳定搜索发现或当前法规答案质量已经通过。真实供应商回传模型名不在 Runtime 的 provider-neutral 完成事件中，本批以请求模型和 provider usage 为准，模型名归一化证据仍见本文件前面的 HTTP 探针。
