# 设置中心

Web 的聊天抽屉保留构图、声音和麦克风快捷设置，完整配置集中到 `/settings`。原来的 `/settings/channels` 和 `/settings/agent` 继续有效，其余分类也可以直接打开或刷新。

| 导航分组   | 内容                                                                            |
| ---------- | ------------------------------------------------------------------------------- |
| 聊天       | QQ/微信连接、交流权限、自由交流、群路由、回复样式和发送节奏；桌面另有桌宠和陪伴 |
| 模型与声音 | 聊天、行为决策/Jev、记忆和向量模型；TTS 与 Realtime                             |
| 能力与任务 | 工具授权、后台任务、文件、候选开发审批、Skills/插件与 MCP                       |
| 记忆与资料 | 记忆建议、事实修正、来源与遗忘；照片/表情仍在对应渠道内管理                     |
| 日程       | 日历、提醒、计划任务和设备回执                                                  |
| 连接与设备 | Runtime、参与者和运行诊断；桌面另有本地数据清理和 Worker Pack                   |

`SettingsShell` 统一分类搜索、连接状态、桌面/窄屏导航、内容滚动与视觉层级。搜索匹配功能关键词，例如“打字”“自由交流”“Jev”。每个产品保留自己的分类注册表，Web 构建不导入桌宠和桌面专属设置模块。共用的模型、声音、MCP 和日历表单已移到产品无关目录，原导出路径保留兼容入口。Google 首次授权/权限升级、原生模型包和设备接入继续由桌面处理。

模型配置按用途切换，角色状态和输入预算放在高级折叠区；QQ 行为集中到“交流与权限”，短期群上下文预算仍在折叠区。表单继续使用各自的保存动作、完整策略校验、版本校验和明确的错误回读，不因导航而扩大权限。

设置分类第一次访问后才挂载；同一 Runtime/参与者内切换分类保留草稿和滚动位置。模型用途切换也保留每个角色的草稿。修改、保存中和已保存状态可见。Runtime 身份变化时页面重建，防止旧范围的草稿被带入新连接。设置页沿用 `useSettingsRuntime`，不建立消息事件流、麦克风或播放链路。关闭/刷新页面仍会丢弃未保存修改；渠道内切换 QQ/微信/交流权限维持原有面板生命周期。

能力分页发现与已保存的任务/文件并行读取，避免刷新后任务列表等待整个能力目录。两个读取路径都保留 Runtime 身份和请求版本校验，过期结果不能覆盖当前页面。

隔离浏览器验证：

```bash
PYTHONPATH=services/runtime/src:packages/protocol-python/src:packages/model-worker-sdk-python/src \
  .venv/bin/python tools/agent/browser_fixture_runtime.py --root /tmp/cw-settings-browser --port 8778
VITE_RUNTIME_URL=http://127.0.0.1:8778 VITE_RUNTIME_TOKEN=disposable-agent-browser-fixture \
  pnpm --filter @chatwaifu/web exec playwright test --config playwright.settings.config.ts
```

验证使用一次性 Runtime，覆盖 Web/桌面 1280px 与 390px 布局、功能搜索、模型用途、跨分类草稿保留、全部分类入口、页面无横向溢出和无额外媒体链路。构建产品隔离检查继续分别运行。双产品开发服务器使用独立 Vite 依赖缓存，避免互相失效。
