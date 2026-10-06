# Q02 服务器 Yandex/SearXNG + Crawl4AI 探针

日期：2026-10-03。执行主机为 `192.168.1.103`（`mubai-6133`）。不调用 AGY，
不输出或保存服务令牌，不修改 Runtime、模型路由、数据库或生产密钥。

## 隔离测试

在服务器上从现有 Compose 目录复制临时配置，单独启动 SearXNG 容器并只启用
一个引擎；测试完成后销毁临时容器和目录。现有 `cw2-public-web-searxng-1`
容器在探针期间保持不变。

固定查询及结果：

| 查询 | 重复 | HTTP/引擎错误 | 官方域命中 |
| --- | ---: | --- | ---: |
| `site:caac.gov.cn 充电宝 3C 召回` | 5 | 5/5 为 200，无错误 | 5/5 |
| `site:caac.gov.cn 充电宝 100Wh 160Wh 托运` | 5 | 5/5 为 200，无错误 | 5/5 |
| `site:raft.github.io leader election timeout heartbeat` | 5 | 5/5 为 200，无错误 | 5/5 |

请求总延迟约为 0.54--1.44 秒；结果 URL 已是 HTTPS，未使用搜索页面或导航
重定向作为来源。Yandex 结果示例包含民航局官方公告、航空旅行常识页和
`raft.github.io` 页面。

同主机的对照结果：

- Sogou：SearXNG `resp.next_request` 兼容性异常，结果为空。
- Baidu：连续返回 `Suspended: CAPTCHA`。
- DuckDuckGo：中文查询返回 `CAPTCHA`。
- Google：返回 `Suspended: access denied`。
- Bing：中文查询为空，Raft 查询稳定返回游戏页面，相关性不合格。
- Qwant：返回 CAPTCHA；Startpage、Mojeek、Marginalia 返回空结果。

这些对照是供应商可用性证据，不把空结果解释为“无来源”。

## 正文读取

Crawl4AI `0.9.2` `/md` 在同一服务器正常工作。对 Yandex 命中的 HTTPS 页面读取：

- 民航局 2025-06-26 公告：正文保留 `3C`、`召回`。
- 民航局《航空旅行常识》：正文保留 `100Wh`、`160Wh`、`托运`。
- `https://raft.github.io/`：正文保留 `Leader Election`。

请求使用服务器私有环境中的 Crawl4AI bearer token；收据只保留 URL、时间、内容
指纹和截断状态，不保留 token。公网 URL 的 Runtime 读取仍需显式
`dns_resolver: cloudflare`，Fake-IP 解析不能作为失败或成功的隐式替代。

## 结论与边界

Yandex + SearXNG 通过了初始探针，但后续出现十次连续 `yandex timeout`，因此
不能作为稳定性通过证据。两批完整 `detailed_answer` 均完成 12 条回复，法规事实
审计均为 2/3；第一批有一次 `web_http_error`，另一批包含诚实的未核实回答。
原始批次保存在 `q02-gemini-yandex-detailed-2026-10-03/`，第一批供应商返回
prompt/completion/reasoning/total 为 `131938/8085/11637/151660`。

本轮续跑时服务器的三个固定查询恢复 HTTP 成功且没有引擎错误，延迟分别为
1188/943/1268 ms。但 Raft 查询前三条是 PDF/PPTX，不能由当前 HTML Reader
直接当成已读取正文。此结果说明服务恢复后仍需验证正文可读性和模型消费。

Crawl4AI 是可工作的正文读取服务。仓库的 DuckDuckGo Lite + 内置 Reader 默认
未改变；服务器运行进程已显式配置 `searxng` + `crawl4ai`，使用 18080/11235。
部署模板保留 Yandex 试验配置并明确其间歇超时，不把短期恢复写成稳定通过。
独立的微信文字投递和桌面用户听见确认已有各自证据，不能用本搜索探针替代。
Q02 整体仍未通过，完整模型质量和实际来源链路需继续验收。

后续已修正 SearXNG 容器 DNS、改用本地 `/healthz` 健康探针，并把 Yandex 超时
试设为 8 秒。修复查询覆盖及错误分类后的 24 条 Runtime 复测获得法规关键词
覆盖 3/3；但仍发现 Raft 关系式、实际来源引用和清单区间边界问题。完整变更、
失败批次及原始证据见[服务器链路修复与质量复核](q02-server-deployed-vs-jina-2026-10-03.md)。
该阶段仍未证明长期稳定或 Q02 质量通过。
