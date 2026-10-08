# 设置中心 Liquid Glass 验收记录

> 历史发布记录：以下现场验证来自原发布过程；私人路径和关联标识已脱敏。
> 本次 PR 整理的独立验证见 PR 描述，本次未部署、重启或操作线上数据。

日期：2026-10-07；暗紫主题复查：2026-10-08。实施分支：`mubai/settings-liquid-glass`。

## 基线与实施边界

- 实施基线核验时，`<runtime-host>` 静态页面目录为 `<server-root>/releases/settings-center-20261007/web`，对应前端提交 `694c9d8e35ecbd1a8480dca56be4c8a41fd91e6f`。
- 本轮基于 `mubai/qq-account-permissions` 的 `0461c5fbf0ceedc97020e616165daca22e9a34cf`。两个提交间的 Web、桌面和公共包没有代码差异；0461 的新增部分为文档。
- 实际读取服务器发布清单并核对磁盘和 HTTP 返回内容，18 个前端文件哈希相符，`/settings` 返回的 index 也相符。
- `<local-worktree>` 的 main `7f9bab6e` 是更早版本；本轮代码只写入 `<local-worktree>`，未覆盖 main 或原来的设置分支。
- 修改限于共享 React/CSS、前端生命周期及验收代码。无协议、后端、数据库迁移、模型/语音配置或真实账号修改；未使用 agy。

## 已实现行为

玻璃材质用于悬浮侧栏、页头、分段导航和共享弹窗；正文卡片保持清晰。统一系统字体、连接页同款暗紫表面与梅紫操作、14px 正文和表单标签、40px 桌面/44px 触屏主要控件。页头和正文共用 1040px 上限，移除渠道页残留的独立宽度。小窗口、窄屏和较低窗口高度均可滚动到末项及保存按钮。

扩展管理改为 Skills/插件/MCP 页内标签。Skills 支持本地搜索、列表与详情、就近执行参数、按需说明和最近运行；插件及 MCP 原有安装、启停、卸载、编辑、探测和读取功能保留。共享弹窗显式接入主题，支持背景 inert、焦点循环、Esc 和入口焦点返回。

普通草稿在同一 Runtime 内保留。隐藏面板停止自身轮询（包括模型索引重建进度），Runtime 身份切换重建页面；MCP 首次加载避免覆盖刚输入的草稿，离开面板清理令牌、Prompt 参数及资源结果，迟到结果不能重新显示。设置页仍不建立事件/媒体链路。页内管理仅由当前可见分类持有权限确认入口；聊天弹窗在上层确认出现时让出键盘，拒绝后恢复焦点。

## 验证

以下为本轮最终本地结果；后续服务器发布另见本文的发布状态。

| 检查                     | 结果与范围                                                                                                                   |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| 格式、ESLint、TypeScript | 本轮改动格式通过；Web lint 与构建中的 TypeScript 检查通过                                                                    |
| Web 单元测试             | **474 passed / 66 files**，包括隐藏轮询、普通草稿、Runtime 会话重建和迟到结果回归                                            |
| Chromium/WebKit          | Web 与桌面 UI 设置矩阵 **80 个适用场景通过 / 4 skipped**；跳过的是桌面不适用的 Web 聊天弹窗场景                              |
| 渠道回归                 | **8 passed**：实际一次性 Runtime API 保存/CAS回读/刷新、原策略恢复、统一控件和参与者/场景弹窗                                |
| 布局尺寸                 | 全部分类、QQ/微信/权限和 Skills/插件/MCP 子页；1870×864、1548×864、1280×900、1024×768、390×844、1280×480                     |
| 缩放重排                 | 640×450 CSS 视口，相当于 1280×900 窗口的 200% 缩放布局；没有操作原生浏览器缩放菜单                                           |
| 状态与交互               | 长文本、空目录、加载、失败、离线恢复、末项和保存按钮、Tab/箭头/Esc、焦点返回、单一确认窗口；拒绝写入返回 `permission_denied` |
| 辅助偏好                 | 减少动效、增加对比度、触屏 44px、实际 CSS 不透明降级规则；深色场景的弹窗说明对比度不低于 4.5:1，最终两引擎复核 **2 passed**  |
| 构建和边界               | Web/桌面 UI 构建、两个产品模块隔离及架构依赖检查通过；验收 Runtime 的 Ruff/格式与 Pyright 通过                               |

验收 Runtime 仅补充两个 loopback CORS 端口以支持原有渠道测试配置；生产安全配置未修改。浏览器采用一次性 loopback Runtime `http://127.0.0.1:8778`，模型/语音为 Demo/Fake，STT 禁用。QQ 已绑定画面来自合成的浏览器响应；没有扫码、配对、发消息或生成真实语音。桌面 UI 在浏览器引擎中验证，没有更新已安装的原生客户端。构建保留已有大文件及 Tauri 混合导入警告，未引入新的渲染依赖。

### 发布后的样式补修

用户反馈 TTS 按钮仍为原生方形、上方留白不足且蓝色与应用紫色不一致。
核验 active Web 仍为 `aa0d8df7` 后，在同一隔离分支补修：

- TTS 完整样式原本只在桌面 CSS 中，Web 仅接到按钮高度规则。将字段网格和操作区移入组件直接加载的共享 CSS，补齐圆角、背景、间距和窄屏换行；公共材质也提供完整按钮基线。
- 增加页头外边距及正文上方间距；固定设置容器与独立滚动，滚动到保存按钮时不会把页头推离视口。
- 设置中心继承应用 `--accent`（`#7c3aed`），环境色、焦点、选中态、弹窗及降级表面统一为紫色；主要按钮悬停使用 `#6425c6` 和白字。

新增的两个布局/配色场景先在旧样式上各失败一次，再在 Web/桌面的 Chromium/WebKit 上通过。
完整矩阵首轮有 72 项通过，8 项仅因旧测试固定检查蓝白降级颜色失败；将明确颜色期望同步为紫白后，相关降级和新增按钮悬停共 12 项复核通过，最终 80 个适用场景均已通过。
474 个单元测试及 8 个渠道回归再次通过。补修没有改变请求、保存、路由或权限逻辑。
本机 `.local/settings-glass-polish/` 保存日志及隔离截图；补修已发布，见下方发布状态。

### 2026-10-08：按连接页参考校正为暗紫主题

上一版把用户要求的暗紫色误读为紫白配色，已纠正。共享 `plum-theme.css` 使用连接页的
`#231b29 → #151119` 背景、`#eee5ef` 正文及低饱和梅紫按钮；设置侧栏、内容、浮层、
输入框和降级表面均按暗色配色，玻璃仍限于导航和悬浮控制层。

完整检查发现并修复：

- 导航按钮继承居中布局，图标横坐标最大相差 30.56px。改为固定图标列/文字列，跨标签、选中状态和桌面宽度保持对齐。
- 旧渠道、日程、声音、桌面和诊断组件硬编码浅色。引入语义色，同时保留非主题消费者的原色回退；PDF 纸张和二维码保留各自必要背景。
- 独立弹窗未继承主题，以及公共控件 CSS 加载顺序覆盖深色按钮。明确主题优先级，覆盖参与者、权限确认、数据清理、引导、索引提示及预览弹窗；清理和引导补上焦点循环/返回，低窗口可滚动到操作区。
- MCP 有数据时列表按钮布局被公共按钮覆盖；长名称和路径改为左对齐纵向布局。
- 任务授权和候选开发表单的标签/输入框原本挤在一行，改为纵向整宽字段；语音名称与说明分行；空状态按钮留间距，手机窄屏短操作文字不拆行。

验证先在旧版重现侧栏偏移及错误配色。整轮 Web/桌面 Chromium/WebKit 矩阵
**100 passed / 4 skipped**；随后新增任务表单场景及全部展开页面在四产品/引擎组合复查
**8 passed**（其中 4 个新增场景），共覆盖 **104 个适用设置场景**。新检查还覆盖
MCP 长数据、模型用途、日程子页、权限弹窗、辅助弹窗低高度和键盘焦点。渠道回归 **8 passed**；参与者弹窗控件期望同步为 14px/12px 圆角后通过。
474 个单元测试通过，最后表单/焦点相关的 9 项再复核通过；格式、lint、类型、两种 UI 构建及产品/架构边界均通过。

对比度检查遍历可见普通文本，按 CSS 颜色与祖先表面透明度合成后检查 4.5:1（大字 3:1），
配合各分类宽/窄截图人工检查；不声称该计算覆盖图像、动态折射背景或系统原生弹出菜单。
玻璃弹窗说明额外按最亮背景检查。保留七种尺寸与 200% 等价视口重排、减少动态、
增加对比度、无模糊降级、无额外媒体请求以及草稿/Runtime 隔离回归。
当前检查日志与截图位于 `.local/settings-dark-audit/`，线上发布记录单独保留。

## 预览与截图

当前本地预览：Web `http://127.0.0.1:4186/settings/extensions`，桌面 UI `http://127.0.0.1:4187/desktop-settings`。预览使用一次性数据。

工作树 `.local/settings-liquid-glass/` 保存三处重点页面的前后截图、窄屏画面及浏览器输出。`comparison.html` 提供并排/仅新版切换，当前地址为 `http://127.0.0.1:4188/comparison.html`。该目录为本机验收产物，不进入产品包。原始用户截图保留为视觉参考；旧版隔离截图与新版隔离截图的账号/加载状态可能不同，截图不证明真实渠道接入成功。

### 发布状态

用户随后授权服务器部署。2026-10-07 22:28（Asia/Shanghai）已将前端提交
`aa0d8df7` 发布为 `settings-liquid-glass-20261007-aa0d8df7`，入口为
[服务器设置中心](http://<runtime-host>:18780/settings/extensions)。23 个新旧静态文件
和九个页面入口哈希通过；Chromium/WebKit 在桌面与手机宽度下的四个生产只读场景
通过。Runtime、凭据/模型配置、渠道权限及服务/容器身份保持，原生客户端没有更新。
发布、保留范围及回退见 [服务器发布记录](./operations/settings-liquid-glass-web-2026-10-07.md)。

2026-10-07 23:26，按钮/留白/紫色补修提交 `a51aec88` 已发布为
`settings-glass-polish-20261007-a51aec88`。28 个静态文件和九个页面入口的磁盘/HTTP
哈希通过；四个生产只读浏览器场景补查 TTS 样式、主按钮悬停、顶部留白和紫色
一致性，均通过。全部五个模型、语音/权限及服务身份保持。详见
[补修发布记录](./operations/settings-glass-polish-web-2026-10-07.md)。

### 可复现命令

```bash
pnpm --filter @chatwaifu/web test
pnpm --filter @chatwaifu/web lint
pnpm --filter @chatwaifu/web typecheck
pnpm --filter @chatwaifu/web build:web
pnpm --filter @chatwaifu/web build:desktop
python3 tools/verify_product_artifacts.py --product web
python3 tools/verify_product_artifacts.py --product desktop
python3 tools/check_architecture_boundaries.py
VITE_RUNTIME_URL=http://127.0.0.1:8778 VITE_RUNTIME_TOKEN=disposable-agent-browser-fixture \
  pnpm --filter @chatwaifu/web exec playwright test --config playwright.glass.config.ts
VITE_RUNTIME_URL=http://127.0.0.1:8778 VITE_RUNTIME_TOKEN=disposable-agent-browser-fixture CHATWAIFU_E2E_ISOLATED_CHANNELS=1 \
  pnpm --filter @chatwaifu/web exec playwright test --config playwright.channels.config.ts
```

浏览器命令依赖先启动一次性 Runtime（见 `docs/settings-center.md`）；配置自行管理 UI 开发服务器。已启动的已知一次性 4186/4187 预览可通过 `CHATWAIFU_REUSE_PREVIEW=1` 复用。构建与浏览器验收分开运行，避免工具缓存失效影响验收。
