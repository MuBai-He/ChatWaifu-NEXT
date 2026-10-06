# Q02 实际来源读取切片

2026-09-30。v5 定向评测仍有事实遗漏，且评估器直接调用 Provider，没有执行 Runtime 工具。当前本地部署只读清点得到已安装插件和 MCP 连接各零个，内置能力没有公开网页读取。沿用 ADR 0007 的 Runtime Skill 边界补足实际读取能力；不把固定法规答案写进 persona 或长期记忆。

本切片由 `runtime_skills` 拥有：新增 `web.read` 1.0.0，输入公开 HTTPS URL、可选正文定位词和输出长度，返回实际来源 URL、标题、正文片段、抓取 UTC 时间、内容指纹、正文总长度、偏移及截断信息。定位词只做文本匹配，不声称语义检索。模型可区分来源内容、抓取时间与法规生效时间；抓取时间不能充当生效时间。

沿用 Registry → PermissionBroker → Executor → BuiltinAdapter → HTTP adapter。能力要求 `web.public.read` 权限、逐次确认、30 秒执行上限；用户允许读取后才建立连接。所有工具结果保持不可信数据标签，网页指令不能改变角色、权限或系统状态。模型应引用实际 URL，并如实说明失败、截断和未找到定位词。

HTTP adapter 复用现有 DNS 地址固定 transport。只支持全球可路由地址上的 HTTPS 443，禁用环境代理、认证、Cookie 和自动重定向；每次重定向重新验证 URL/DNS 并固定连接地址，最多三次。原始网络数据与解压正文都有限额。只读 HTML/XHTML/纯文本；脚本、样式、导航及隐藏元素不进入正文，不执行网页代码。DNS、HTTP、类型、体积、超时等失败规范化；父任务取消原样传播并关闭响应/客户端。

无新数据库表、凭据、外部搜索供应商或前端直接请求。网页正文及带查询参数的 URL 不进入常规持久日志；现有审计只保存结构摘要。每次读取创建并关闭 HTTP 客户端，无后台任务或全局缓存。权限授予与工具安装保持独立，不改用户现有配置。

实际网络验证发现本机将民航局和 Python 文档域名解析为 `198.18.*`，默认系统解析应继续拒绝这些非公网地址。加入输入 `dns_resolver: system | cloudflare`，默认仍为系统 DNS。显式选择 Cloudflare 时，在同一次权限确认后经证书验证及固定地址的 `https://cloudflare-dns.com/dns-query` 查询 A/AAAA；该服务会收到来源域名，不收到网页路径、查询参数或正文。DNS 响应有 16 KiB 限额，核对 Question、状态、截断、别名和地址类型，所有最终地址仍须全球可路由。原始私网 IP 与 `.local`/`.localhost` 不发加密 DNS 请求。无自动供应商切换、无修改系统 DNS/代理、无接纳 Fake-IP；返回实际 resolver 元数据。接口依据 [Cloudflare 官方 DNS JSON 文档](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)。

验收包含真实 manifest/schema、路由、权限拒绝不发请求、允许后来源进入 Agent 工具交换、持久审计不含正文、重定向进入私网拒绝、DNS 固定复用、体积/解压限额、无效 HTML/类型/HTTP 错误、超时和取消。受控 HTTP 只证明软件链路，随后需实际公开来源和指定真实模型验收。完整 Q02 十二场景、三种 presentation 和受控 Runtime 的质量门槛仍需保留；网页读取本身不能证明 Q02 已通过。公开网页发现/搜索另行接入同一边界，不能把已知 URL 验收写成通用搜索通过。

## 当前验收

新增测试模块及现有 Agent 集成测试共 63 项通过；完整 Python 回归 1613 通过、46 项平台相关跳过，Ruff lint/格式、Pyright、Web/桌面和文档构建通过。源码直接实现和审查，没有使用 AGY。实际受控 Runtime 的三次读请求均先处于 `waiting_for_confirmation`，以独立合成会话的 `allow_once` 放行后成功。来源元数据与内容指纹见[读取记录](public-source-runtime-validation-2026-09-30.json)，没有在常规日志或此记录中复制整页正文。

系统 DNS 的两次原始尝试都以 `web_url_forbidden` 退出，解析结果是非公网 Fake-IP 范围。显式加密 DNS 后两份真实来源可读；首次 Python 定位词命中较早示例，片段没有 `return_exceptions`，改用参数名再次读取才覆盖对应原文。本记录保留首次不足的结果，不能把 `focus_matched` 或关键词存在等同于回答正确。所有片段都按实际范围显示截断。未运行新一批付费模型回答，Q02 质量状态仍未通过；后续先接来源发现与真实 Runtime 评估，再按原门槛复核。
