# Windows 图片突发测试的准备阶段等待

2026-10-01，证据提交 `29431786cfc2e8a9f15183b707d75c6f525be359` 的[准确 Windows Python CI](https://github.com/MuBai-He/ChatWaifu-NEXT/actions/runs/36834030132/job/110277002916) 最终失败，其余 17 项成功、1 项部署按 PR 规则跳过。同一生产源码的前一提交 `3ef23da` 为 18 成功/1 跳过，不能用它覆盖本次失败。

失败为 `test_full_deferred_burst_and_many_more_images_do_not_block_stop[False]` 在首批四张图片之后的 `asyncio.wait_for(entered.wait(), 5)` 超时，尚未进入停止与溢出检查。远端 610 passed/7 skipped/1 failed。没有据此确认产品的停止行为错误，也没有凭前一 CI 通过将本次标为偶发。

## 控制与修复

测试把四张图片的串行持久化准备与 Provider 开始生成放在同一个五秒等待内。在仅进程内的故障注入中，每条首批消息的实际 `_remember_context` 前增加 1.5 秒 I/O 延迟，保留原方法调用；旧测试在同一等待点失败，1 failed/5.26 秒。该实验支持准备阶段共用截止时间的问题，**不证明 Windows 主机确切的 I/O 根因**。

只修改本测试：仍以一个实际模拟 poll response 提交四张图片；依次等待每条消息的 SQLite turn 和 burst membership 持久化，每步沿用现有五秒条件等待。随后保留原五秒 Provider entered 等待。不能用全 poll checkpoint 做单条准备同步，因为 checkpoint 在整批受理后才提交。

停止、溢出、游标、终态、发送及取消的原有等待和断言均保留；没有改生产派发、增加跳过或全局延长超时。故障注入延迟用于控制，不是测试的同步方式。

- 相同延迟控制修复后 1 passed/6.43 秒。
- 仅进程内禁用 `_spawn_dispatch_task` 的负例在修改后的原 Provider 等待处失败，1 failed/5.67 秒；持久化准备完成不会掩盖缺失派发。方法在 finally 中恢复，生产文件未被改写。
- 图片突发、外部渠道、渠道管理和实时回归 71 passed；Python 全集 1863 passed/46 平台跳过。Ruff lint、619 文件格式、全量 Pyright、架构边界以及 Web/桌面 UI/文档构建通过。

完整控制脚本、修复前后输出、负例和原 CI 状态见[证据](burst-readiness-evidence-2026-10-01.json)。`.github/workflows/ci-python.yml` 与 `test_tts_configuration.py` 的已有字节保持；新提交 Windows CI 必须单独核对，不能用本机检查清除原失败。此测试修复不批准 Q02，也没有执行真实渠道或设备操作。
