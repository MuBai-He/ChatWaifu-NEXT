# Home Assistant 接入准备

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

1. 从常驻 Runtime 所在机器确认能访问 HA。Mac 能访问不代表服务器也能访问；当前部署仍等待开发板的 VPN 接入方案，不修改家庭路由。
2. 在 HA 的设备与服务中添加官方 Model Context Protocol Server 集成，选择对 AI 暴露的设备。
3. 在 ChatWaifu 现有 MCP 管理页添加 Streamable HTTP 连接：URL `http://192.168.10.216:8123/api/mcp`，开启远程访问，network policy 为 `allow`、sandbox 为 `disabled`。HA 访问令牌只填秘密输入框，由 Runtime 的秘密存储保管。
4. 测试连接和发现工具，先读状态。保持现有 Skill 权限、调用确认与主人私聊限制。
5. 用用户选定的一盏灯验证控制与状态回读，再扩大实体范围。不要以工具调用成功代替真实灯具确认。

撤销接入时禁用/删除 MCP 连接，并清除服务器允许项。已建立连接的配置策略以 Runtime 启动时为准，移除允许项后重启使现有连接关闭。HA 侧也可撤销令牌或取消暴露实体。

## 验证记录

2026-09-22：已有 MCP 网络检查共 20 项通过，包括真实回环 SSE/Streamable HTTP 通信、DNS 固定、TLS 和新增的精确私网来源/拒绝越界场景；修改代码的 Ruff 与 Pyright 通过。真实 HA 工具发现和设备控制待网络与授权完成。

官方文档：[MCP Server](https://www.home-assistant.io/integrations/mcp_server/)。
