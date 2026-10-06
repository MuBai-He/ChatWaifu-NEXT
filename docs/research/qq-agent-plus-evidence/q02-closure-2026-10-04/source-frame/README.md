# 来源成文帧隔离控制

仅支持[统一台账](../../q02-closure-ledger-2026-10-04.md)中的原型结论；不是生产功能、
新的完整四轮或角色A/B验收。

- `capability/`：2次端点JSON Schema有效响应，未测质量。
- `preflight-failure/`：JSON正文比较方式有误，0HTTP终止。
- `frozen/`：同一正文及原T1/T2前缀，两组T3/T4，帧1.0，4HTTP。
- `layout/`：保留上述真实T3，帧1.1区分段落与列表项，2HTTP。
- `existing-revision/`：对实际布局草稿各执行现有一次修订，2HTTP。
- `accounting.json`：所有真实payload、schema、正文、历史、用量和时延校验。
- `primary-review.json`：实现作者直接审查全文；不称独立主审。
- `decision.json`：条件保真失败，原型不采用，完整Q02未通过。
- `historical-frame-v1.0.py.txt`：第一个控制的真实代码。当前1.1代码在仓库Agent域，
  `prototype-sha256.json`列代码/测试/ADR指纹；尚无生产调用者。

脚本中的 `published_reply` 指隔离渲染文本，未投递到客户端/微信、未TTS或播放。
模型密钥只在服务器进程中读取，先验证无泄露再复制；未知usage字段保持未知。
后续执行和依赖顺序只维护在统一台账。
