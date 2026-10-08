# Home Assistant MCP 接入与验收

日历与提醒事项使用用户已有的 Google/Apple 服务。Home Assistant 只承担家居控制。

## 当前边界

Runtime 的 MCP Host 已支持 Streamable HTTP。私网目标需要操作员在服务器配置中列出精确来源，连接本身还需开启 `allow_remote`。配置默认为空；这项改动不会自动连接 HA 或改变设备状态。

在 `runtime.toml` 已有 `[security]` 表中合并：

```toml
[security]
mcp_private_origins = ["http://192.168.10.216:8123"]
```

也可用服务器环境变量 `CHATWAIFU_SECURITY__MCP_PRIVATE_ORIGINS='["http://192.168.10.216:8123"]'`。修改后重启 Runtime。只接受 RFC1918 IPv4 字面地址，匹配协议、IP 和端口；不能配置通配符、DNS 名、路径、凭据或 metadata/link-local 地址。

## 接入步骤

1. 从常驻 Runtime 所在机器确认能访问 HA。Mac 能访问不代表服务器也能访问；2026-10-08 已从常驻 Runtime 机器验证网络可达。
2. 在 HA 的设备与服务中添加官方 Model Context Protocol Server 集成，选择对 AI 暴露的设备。
3. 在 ChatWaifu 现有 MCP 管理页添加 Streamable HTTP 连接：URL `http://192.168.10.216:8123/api/mcp`，开启远程访问，network policy 为 `allow`、sandbox 为 `disabled`。HA 访问令牌只填秘密输入框，由 Runtime 的秘密存储保管。
4. 测试连接和发现工具，先读状态。保持现有 Skill 权限、调用确认与主人私聊限制。
5. 用用户选定的一盏灯验证控制与状态回读，再扩大实体范围。不要以工具调用成功代替真实灯具确认。

撤销接入时禁用/删除 MCP 连接，并清除服务器允许项。已建立连接的配置策略以 Runtime 启动时为准，移除允许项后重启使现有连接关闭。HA 侧也可撤销令牌或取消暴露实体。

## 协议兼容性与意图路由

- **MCP 资源模板发现兼容**：实测 Home Assistant 2026.9.3 在正式 Runtime 连接中协商 MCP 协议 `2025-11-25`，支持 `resources/list` 并返回静态 assist-context 资源，但未注册方法 `resources/templates/list`（响应 JSON-RPC `-32601 Method not found`）。此前独立探测使用的协议为 `2025-03-26`。Runtime 在 `_list_resource_templates` 仅针对首分页捕获 `-32601` 并返回空列表；保留所有其他错误码、分页启动后的错误、必选列表方法以及超时/取消，防止全量发现被阻断或局部失败静默漏报。
- **智能家居中英文语义归一化**：针对 HA 工具英文字符描述（如 `intent__HassTurnOn`、`light__HassLightSet`、`climate__HassClimateSetTemperature`、`homeassistant__GetLiveContext`），在 `RuntimeSkillRouter` 对称扩展中英文概念词表（设备开关、亮度控制、空调/温度调节、设备状态查询），支持驼峰命名分词与短语匹配。按工具元数据为中文控制与状态问询提供相关候选；通用词匹配不等于完整意图理解，具体目标与操作仍由模型选择，执行仍经权限与确认。HA 状态工具的实际描述也提及天气，可能在天气查询中成为只读候选。所有投影工具保留严格的本地确认（`confirmation_required=True`）、权限校验、副作用分级以及 `cw_` 不透明命名。

## 验证记录

- 2026-09-22：已有 MCP 网络检查共 20 项通过，包括真实回环 SSE/Streamable HTTP 通信、DNS 固定、TLS 和新增的精确私网来源/拒绝越界场景；修改代码的 Ruff 与 Pyright 通过。真实 HA 工具发现和设备控制待网络与授权完成。
- 2026-10-08：针对 HA `-32601` 资源模板方法缺失与中文意图路由完成针对性修复与回归验证：
  - MCP 连接发现回归测试 9 项全部通过（验证首分页缺失模板方法时保留工具/静态资源/提示词、其他错误码与分页后错误严格失败、常规分页与必选方法不受影响）。
  - RuntimeSkillRouter 路由回归测试通过（验证中文开关、调光调亮、温控、状态查询，以及英文 `turn the light on/off` 召回；未启用技能排除、测试中的无关问询排除、调用确认与权限边界保留）。
  - MCP、Runtime Skill 与能力发现定向回归共 **167 项通过**；修改的 Python 文件通过 Ruff 格式与 lint 检查、严格 Pyright 类型检查，`pnpm build:web` 通过。
  - 常驻服务器部署版本 `home-assistant-mcp-20261008`：复制原 `bubble-typing-20261007` 部署源码，仅覆盖 `runtime_skills/host_connections.py` 与 `runtime_skills/agent_router.py`；其余 265 个 Runtime 文件保持原部署版本。服务重启后健康状态 `ok`，连接状态 `ready`。Web 仍使用 `chat-clean-interface-20261008`。
  - 正式连接成功发现 **24 个工具、1 个静态资源、0 个资源模板、1 个提示词**；访问令牌在 Runtime 本地秘密存储中保管，不回显到浏览器。
  - 真实模型会话输入“走廊灯现在开着吗？请查询实时状态。”，模型调用 `homeassistant__GetLiveContext`；经一次性确认后获得 `off`，回答“我查了一下，走廊灯现在是关着的哦。”。
  - 对走廊灯执行一次开灯与关灯验收：初始状态 `off`，`intent__HassTurnOn` 后 HA 回读 `on`，`intent__HassTurnOff` 后 HA 回读 `off`。最终恢复原状态，测试会话已关闭。每次调用使用一次性确认，没有授予永久执行权限。
  - **验证边界**：已验证常驻 Runtime、真实 HA MCP、模型工具选择、单灯控制及 HA 实体状态回读；未观察灯具实际发光，也未验收其他设备、QQ/微信渠道或实时语音会话。工具成功与实体状态回读不等同于现场物理观察。

官方文档：[MCP Server](https://www.home-assistant.io/integrations/mcp_server/)。

社区参考：[Cannot connect Claude or Cursor to HA MCP_Server](https://community.home-assistant.io/t/cannot-connect-claude-or-cursor-to-ha-mcp-server/874621)。该帖讨论旧版 SSE 代理配置，与本次 `resources/templates/list` 缺失不是同一故障；本次修复依据实际 HA 响应与 Runtime 回归测试。
