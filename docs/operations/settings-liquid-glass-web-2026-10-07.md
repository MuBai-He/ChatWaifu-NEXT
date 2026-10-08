# Liquid Glass 设置中心 Web 发布，2026-10-07

> 历史发布记录：以下现场验证来自原发布过程；私人路径和关联标识已脱敏。
> 本次 PR 整理的独立验证见 PR 描述，本次未部署、重启或操作线上数据。

按用户授权，将已验收的 Apple Liquid Glass 前端发布到 `<runtime-host>`。
前端提交为 `aa0d8df7e14b7bc61c06d8a573f3841d10e05dd5`，实施分支
`mubai/settings-liquid-glass`，工作树为
`<local-worktree>`。发布使用新版设置中心
基线，没有从更早的主目录覆盖产品，也没有使用 agy。

入口：[设置中心](http://<runtime-host>:18780/settings/extensions)。
首次切换时间为 2026-10-07 22:28:50（Asia/Shanghai）。

## 发布与保留

发布目录为
`<server-root>/releases/settings-liquid-glass-20261007-aa0d8df7`。
重新构建生产 Web，检查产品隔离、文件哈希及测试地址/令牌缺失后，上传
13 个新文件，并保留 10 个原不可变静态资源，以兼容已打开页面的旧懒加载资源。
原子替换 `<server-root>/web` 链接；不需要重启 Nginx 或 Runtime。

23 个文件的磁盘和实际 HTTP 内容均与发布清单相符，JavaScript、MJS 和 CSS
类型正确。`/settings` 和八个分类入口均返回新的 `index.html`。
原 `settings-center-20261007` 发布的 18 个文件保持原哈希。
构建文件没有嵌入生产访问令牌或一次性验收令牌。

发布前后快照一致：Runtime 源链接、配置/凭据文件哈希、兼容 API 可见的四个
模型配置、语音/实时配置、渠道全局策略、三个连接配置及 revision、两个群路由、
群行为策略和主动消息策略、九个运行服务的 PID/启动标识及 13 个容器身份。
另按显式包含行为模型的 API 核对全部五个模型配置，与上一版发布的完整快照相同。
QQ 保持 ready。Runtime PID `2399468` 及其
`releases/bubble-typing-20261007/source` 源码链接未修改。

## 线上浏览器复核

Chromium、WebKit 分别在 1870×864 和 390×844 检查，共四个场景通过。
使用既有 owner session，生产 API 请求只允许 GET/HEAD/OPTIONS；没有创建会话、
保存配置、安装插件、执行 Skill、发送渠道消息或建立设置页的额外媒体链路。

- 八个分类的页头/正文边界一致，无横向溢出，正文末端可达。
- 真实 Skills 目录为 20 项；搜索、清空搜索和列表末项可达。
- 插件入口和 MCP 编辑器可见，MCP 保存按钮可达；没有执行保存。
- QQ 显示真实连接正常，宽屏和窄屏布局通过。
- 两个引擎均实际计算出玻璃背景模糊，未出现 JavaScript 异常或非预期 API 错误。

声音页读取尚未配置的 `gpt_sovits` 表单时可能返回既有 404。发布前语音配置目录
只有两个阿里云配置，线上回读确认该 404 为 `TTS configuration not found`；
浏览器检查仅豁免这一已核对的未配置状态，其他 API 错误仍使检查失败。
本轮没有改变声音路由或补建配置。WebKit 初次使用五秒目录等待遇到超时，截图
随后已显示全部能力；改为等待生产网络响应最多二十秒后，四个场景通过。

源代码的 474 单元测试、80 个适用隔离浏览器场景、格式/lint/类型检查、Web/桌面
UI 构建及架构/产品边界结果见
[本地验收记录](../settings-liquid-glass-validation.md)。生产浏览器复核追加于这些
结果，没有重新运行不受本次静态发布影响的源码测试。

## 证据与回退

服务器发布目录权限为 0700，保存发布前后快照、构建包、文件清单、兼容资源清单、
旧 Web 链接及 provenance、完整模型核对和四个浏览器场景结果。
根目录 `WEB-PROVENANCE.json` 记录前端提交、实际发布和复核结果；
Runtime 的 `SOURCE-PROVENANCE.json` 不变。

本机 `.local/settings-liquid-glass-publication/` 保存浏览器结果和真实线上截图。
浏览器鉴权上下文仅存于本机私有临时文件，验收完成后删除；不进入源码或产品包。

可将 Web 链接原子恢复到 `releases/settings-center-20261007/web`，并恢复发布目录中
`previous-web-provenance.json`。回退只涉及静态目录与前端发布记录，无需重启 Runtime
或用旧数据库/配置覆盖持续产生的业务数据。初次发布内置失败回退，本次检查通过，
未执行回退。

本次只发布服务器 Web。已安装的原生客户端及安装包没有更新；源分支未合入 main
或推送。页面只读验证与真实手机收件、语音播放及原生设备验收分别记录。
