# Q02 下一轮 Gemini 技术定向复测

日期：2026-10-02。未调用 AGY。批次使用当前 CW2 运行端点的 8318 配置，目标模型 `gemini-3.8-flash-high`，默认 persona v4，PromptCompiler template v12，评测器 1.14.0，推荐预算为 32768 输入窗口、8192 输出预留、8192 请求输出上限、15% 估算余量和 scaled 分项。评测数据库与请求记录均为隔离目录。

## 范围和结果

`technical_help` 场景运行 3 重复、4 轮，共 12 条逻辑回复。所有请求 HTTP 成功、`finish_reason=stop`，没有评测器级失败或重试终态。

| 项目 | 结果 |
| --- | --- |
| 普通 `asyncio.gather` 异常传播与其他任务取消边界 | 6/6 条符合当前问题要求 |
| `return_exceptions=True` 配置和异常对象列表语义 | 3/3 条符合 |
| 完整 Python 示例 | 3/3 个代码块可执行，输出与声称一致 |
| 直接元素返回、没有 Result 包装 | 3/3 条符合 |
| 技术硬错误 | 0/12 |
| Provider usage | 12/12 条均有 prompt/completion/total/reasoning |

代码块在隔离子进程中执行。三份完整示例均正确打印两个成功结果和一个 `ValueError`；只包含一行调用的两个代码块是对上一轮问题的配置片段，不按完整程序计分。

## 用量和延迟

供应商报告合计：prompt 31307、completion 2255、total 38540、reasoning 4978。评测器参考估算合计为 prompt 31974、completion 2698；两者是不同计数口径，不自行相加或把差值推广为固定误差上界。总延迟 54445 ms，p50 4401 ms，p95 使用 nearest-rank 为 5632 ms。

实际捕获的最终请求 JSON、编译请求对象和原始回复保存在忽略目录：

`.local/research/q02/q02-next-execution-20261002/technical-gemini-3.8-flash-high-captured/`

其中 `captured-requests.json` 的 SHA-256 为 `8ca4fbc55f84be0d872755402ec9771726c136468e380249cf8273d932232d1d`，`results.jsonl` 的 SHA-256 为 `fa34024dbd48bb7ea1453ce9bac193ae08f46ac96616acdc73eb4d7ce3f6165f`。请求带有 `max_tokens=8192`、`stream_options.include_usage=true`，本批没有工具 schema；完整最终请求未观察到上游资料省略。

## 判断边界

本批在完整输入下没有复现先前 Flash-Lite 的 Python 错误，也没有验证 Raft 或特殊 `BaseException` 的全部边界。因此它只能说明本批 Gemini 3.8 在这些普通技术题上的表现，不能宣称模型长期技术可靠，也不能抹掉此前错误样本。由于本批未发现 Runtime 丢失条件、提示冲突或预算裁剪，不修改 persona、PromptCompiler 或生产模型配置。

Q02 的来源条件、法规清单保真、三种 presentation、完整十二场景和受控 Runtime 交互仍未通过。

