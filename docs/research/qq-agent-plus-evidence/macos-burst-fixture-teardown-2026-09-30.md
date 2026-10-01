# macOS 图片突发测试的服务生命周期修复

2026-09-30，提交 `b47f64b` 的 [macOS Python CI](https://github.com/MuBai-He/ChatWaifu-NEXT/actions/runs/36656538245/job/109701991341) 在 `test_crash_restart_interrupted_intake_recovery` 显示 PASSED 后，最终因 25 分钟上限取消。Faulthandler 显示 pytest 的 asyncio Runner 退出停在 `_cancel_all_tasks`。其他 CI 检查通过，文档部署按 PR 规则跳过。

## 已复现的问题与修复

`_setup_burst_environment` 先启动 RuntimeContainer，再创建第二个 ChannelManagementService 替换已启动服务。容器退出只停止当前字段中的新服务，原来的 `weixin-terminal-events-consumer` 留在事件循环中。AGY 调查和主代理独立探针均观察到：第一个和恢复后的容器都停止后，原消费者仍未结束。探针证明任务泄漏，未复现远端完整的 25 分钟卡住。

原恢复容器还使用默认钥匙串和网络客户端；AGY 的修复前探针记录了四次钥匙串读取调用。钥匙串/线程争用是否导致 CI 卡住仍是推测，没有 securityd 死锁证据，也没有本机耗时保证。

修复仅在 `test_inbound_multi_image_burst.py` 的夹具与断言中：

- 构造容器前，在该测试作用域注入内存凭据与模拟微信适配器，保留容器创建的同一个管理服务到正常退出。原有事件、照片、表情与调度器接线保留。
- 恢复容器使用保留的测试凭据和新的模拟传输实例，隔离系统钥匙串与真实渠道。
- 崩溃收集场景捕获运行中的实际 Task 引用，分别检查两个容器停止后任务已结束；停止有 10 秒限时，CI 的 25 分钟上限保持原值。
- 钥匙串守卫同时记录调用，退出后明确断言零调用，避免后台异常被转换为正常失败状态后漏检。
- 主代理补齐恢复容器的模型记录器和恢复传输计数；正常恢复检查没有额外回复/调用/下载，崩溃恢复仍检查 leader/follower 失败、恢复提示、零下载与零模型调用。

未修改生产领域、协议、数据库结构或运行配置。

## 检查与证据边界

AGY 修复前探针因一个未结束消费者失败，并显式清理残留任务，避免探针自身挂住。AGY 修复后 31 项图片突发/外部渠道测试通过。委派最终 SUCCESS、进程退出 0；stderr 含后台搜索等待通知，没有权限拒绝。主代理审查实际差异后补强上述恢复断言，独立运行：

```sh
uv run pytest -q services/runtime/tests/test_inbound_multi_image_burst.py services/runtime/tests/test_external_channels.py services/runtime/tests/test_channel_management.py services/runtime/tests/test_realtime.py
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run pyright
```

主代理定向 71 项通过；全集 1439 通过、46 个 Windows 专属跳过（63.72 秒），Ruff 全仓格式 561 文件通过、lint/Pyright 通过。最终远端 CI 结果以 [PR #50 当前提交的检查](https://github.com/MuBai-He/ChatWaifu-NEXT/pull/50/checks)为准。保留已有取消、迟到输出与交付检查，未新增跳过、缩减断言或延长 CI 超时。三模型 v4 盲表只作展示排版，精确模型输出仍以未改写的 JSONL 和原始评审输入为准。

本机测试通过不等于远端问题已确认消失；最终提交必须重新运行 macOS Python CI。上述确定性工程结果也不批准 Q02 的概率性角色质量。
