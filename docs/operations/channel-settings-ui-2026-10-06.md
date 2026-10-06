# 设置控件样式统一与 Mac 模型加载核验

2026-10-06，在 `mubai/messaging-short-replies` 隔离工作区直接实现与审查，未使用 AGY。

## 改动

截图中的微信账号选择器使用 macOS WebKit 原生 menulist 外观，尺寸不随原有
padding 一致变化。QQ 配对、群设置、主动策略和桌面旧设置也分别定义了输入样式。

新增设置页专用共享控件样式：常规输入框、单选下拉框和文字按钮采用 40px
最小高度、10px 圆角、13px 字体及统一边框、占位和禁用状态。下拉框取消原生
menulist 外观并使用一致的箭头，仍保留原生选择、标签与键盘操作。
键盘焦点使用可见的 2px 紫色轮廓。textarea 保留纵向缩放；开关、普通复选框、
单选按钮、滑杆、文件与颜色选择器以及图标按钮不套用文字输入框尺寸。

共享样式仅在桌面设置页及 Web 渠道设置页启用，并在两个产品入口最后引入。
删除 QQ/微信分项重复控件规则，调整微信标题默认 margin、QQ 卡片内边距和
表单标签间距。连接、权限、预算保存、消息分段和角色提示行为未改。
各表单继续控制字段宽度，横排陪伴数字框保持 84px，语音选择框保持原有半宽限制。
不把所有输入强制拉满整行。

## 验证

- Web 单元测试：63 文件、450 项通过；TypeScript、ESLint、Prettier 和 diff 检查通过。
- Web、桌面 UI 构建及产品产物隔离检查通过，保留已有大包与 Tauri 动态导入警告。
- 隔离 Runtime 使用 demo LLM、fake TTS、disabled STT，不接入真实账号。
  Chromium Web/桌面真实 API 保存、读回及刷新检查各一项通过。
- 两个 Chromium 产品及桌面 WebKit 样式检查共三项通过，覆盖选择器原生外观取消、
  40px 高度、输入字体/圆角、Tab/Shift+Tab 焦点、开关 38×22px、390px 窄屏无横向溢出。
  两个桌面引擎同时验证陪伴横排数字框 84px、语音选择框半宽及 40px 高度；
  QQ 竖排地址框保持铺满表单。
  账号选择器来自两条明确的模拟绑定；样式检查仅建立空会话，不提交消息或渠道策略。
- 共 5 项浏览器检查通过；[记录与截图](../research/qq-agent-plus-evidence/channel-settings-ui-polish-2026-10-06/validation.json)
  分开标注模拟绑定、隔离 API 和真实原生客户端观察。

最初的浏览器检查使用精确 label 文本匹配，未定位包含 option 文本的标签，改为
角色与可访问名称定位；随后对初始化空会话的 POST 误作消息/策略写入，改为明确
仅允许一次 `/v1/sessions`。这两项是测试定位/断言问题，未降低控件或权限标准。

## Mac 桌宠

原生旧桌宠窗口已回退到占位头像，显示 `Load failed`。本次逐项读取并核对模型清单、
全部引用资源、Cubism Core 和 bridge：20 个本地资源 HTTP 200 且内容与文件匹配。
重新启动本工作区原生开发客户端后，设置预览显示 Live2D ready；关闭设置窗口后，
实际桌宠也显示宁宁且没有加载错误。两个窗口分别核验，没有以预览状态代替桌宠。

保留原远程 Runtime 连接和访问令牌。没有取得初次加载失败的详细网络错误，
不进一步归因为 Provider、DNS 或 Cubism 缺陷；没有修改 renderer 逻辑。
本地私人模型资产不提交 Git，也不随这次服务器 Web 样式发布分发。

## 发布记录

最终前端提交 `13f8254`（包含 `2dca6cc` 的样式统一）已发布到
`/home/mubai/chatwaifu-server/releases/channel-settings-ui-20261006-13f8254/web`。
另存 12 个本次已提交的前端源码路径，明确作为既有 `4898cb1` 发布的 UI 覆盖文件，
没有宣称该目录是完整 Runtime 源码。Web 链接原子切换，7 个实际 HTTP 返回文件与
本地产物哈希相同，`/settings/channels` 正确返回新入口。

Runtime 源码仍为原 `channel-settings-20261006-4898cb1/source`；Runtime、Web Nginx
和 HTTPS 进程均保持原 PID，没有重启服务或容器。渠道策略 revision 0 及内容哈希
保持；QQ 和当前微信仍为 ready。Mac 原生客户端继续使用现有远程连接，配置文件
权限 0600。[部署记录](../research/qq-agent-plus-evidence/channel-settings-ui-polish-2026-10-06/deployment.json)
与原 Web 链接保存在独立备份目录。首次 `2dca6cc` 发布也已保留；最终版补充
横排宽度保护。若要撤回整个样式更新，可恢复原 `4898cb1/web` 链接，无需切换
Runtime 或回退数据库。

此次未进行 QQ/微信手机收件或真实语音验收。
