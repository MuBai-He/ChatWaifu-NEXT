# Q02 Gemini Jina/Sogou Runtime 复测 v4（服务器）

日期：2026-10-03

这次复测在 `192.168.1.103` 上执行，使用当前配置的认证 `chat` 密钥，仅在远程进程环境中读取；没有输出或保存密钥，也没有使用 AGY。模型请求仍走配置的 `gemini-3.8-flash-high`，来源工具使用 `jina_sogou` 搜索、内置 `web.read`，所有公网读取显式使用 `dns_resolver=cloudflare`。

## 执行边界

- 评测器：`1.16.0`，`runtime_source_tools`，`allow_once`
- 场景：`detailed_answer`，3 次重复、4 轮，共 12 条结果
- 展示 profile：`instant_message`
- 上下文窗口：32768；输出预留/上限：8192；估算余量：15%；`scaled` 分项策略
- 历史上限：32；记忆候选上限：24；工具结果上限：131072 字节
- 生产路由、默认 persona、生产数据库和服务器配置均未修改

干净目标收据位于 `eval-detailed-v4/`。服务器上第一次恢复时误省略了原批次的 `--scenario detailed_answer`，额外生成的 `greeting`、`teasing`、`correction` 收据保留在服务器临时目录的 expanded 副本中，没有纳入本报告。

## 结果

`results.jsonl` 共 12/12 条，全部以 `stop` 结束。供应商返回用量为：

- prompt：119846 tokens
- completion：7273 tokens
- reasoning：6871 tokens（8/12 条有该字段）
- total：135533 tokens
- 延迟：p50 7697 ms，p95 47629 ms，均值 18965 ms

本地估算 prompt 为 44874 tokens；该差异保留在收据中，不能用估算值替代供应商用量。

## 来源与事实

法规场景的第二轮三次均读取了 2025-06-28 民航局 3C/召回公告和额定能量公告，回复均覆盖：

- 没有 3C 标识或标识不清晰不得携带；
- 被召回型号或批次不得携带；
- 100Wh、160Wh 分级和数量/航司批准条件；
- 额定能量应按标称电压与标称容量换算，不能用 USB 输出 5V 换算。

其中 `r0:t2` 的额定能量搜索连续出现 `skill_timeout`，模型随后通过已发现的官方 URL 完成正文读取；该失败被保留在 `incomplete.jsonl` 和 Runtime trace 中，没有作为成功搜索隐藏。`r1:t2` 和 `r2:t2` 的搜索、正文读取均成功。

事实审计命令：

```text
uv run python tools/audit_character_facts.py --results eval-detailed-v4/results.jsonl --fixtures tests/fixtures/conversation/character_scenarios.json --output eval-detailed-v4/audit.json --check
```

审计结果：目标回合 3/3 完整覆盖，缺失 0，校验错误 0，`check_passed=true`。审计仍将 2 条 5V/Wh 相关内容列为人工复核线索；人工查看原始回复后确认它们明确禁止使用 USB 输出 5V 换算，没有把该线索判成事实错误。审计只证明概念出现和人工复核边界，不等于法律正确性或来源稳定性证明。

## 收口判断

这次复测证明，在来源最终读到正文时，Gemini 3.8 Flash High 能保留 Q02 所需的 3C、召回、Wh 分支和 5V 边界。它没有证明 Jina/Sogou 搜索稳定：同一目标仍出现 30 秒搜索超时，也没有覆盖 `single_text`、`default_voice`、完整场景质量或外部渠道/播放验收。因此 Q02 整体继续保持未通过；本批只作为来源修复后的法规回合证据。

