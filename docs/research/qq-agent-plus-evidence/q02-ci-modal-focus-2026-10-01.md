# CW2 Web 弹窗焦点测试同步

2026-10-01。来源切片提交 1541224 的 CI 已终态：17 成功、1 Ubuntu Web 失败、1 PR 文档部署跳过。失败为 ModelSettingsPanel 的 embedding 模型变更警告测试，311 项通过、1 项失败：弹窗 DOM 已出现，但 React effect 尚未将焦点转到“稍后”，测试立即读到 body。日志见 [失败 job](https://github.com/MuBai-He/ChatWaifu-NEXT/actions/runs/36810237509/job/110203463802)。保留该失败，不将父提交或本地通过当作新提交 CI 成功。

生产代码在 useEffect 中同时转移焦点、注册 Escape/Tab 处理。目标测试现在复用既有 waitFor，等待 document.activeElement 确实为“稍后”后再测试 Tab 到“重建索引”。原警告全文、dialog、按钮、精确焦点目标、Tab 目标及未触发重建的断言全部保留；不增加固定 sleep，不改全局超时或生产焦点代码。

受控负例暂时关闭实际 laterRef.focus()，目标测试在同一精确焦点断言失败；恢复后生产文件字节 hash 为 b719dca7ea31d2f0a0c320a6736c665b00a86ef6b5d1a25429b8424d4477c5c9，与控制前一致且无 Git diff。该负例确认异步等待没有掩盖真正的焦点缺陷。

本地目标文件 9 项、完整 Web 312 项通过，lint/typecheck 与 Web/桌面 UI 构建通过。本提交只改变测试同步和记录；前一来源切片的完整 Python 1759/46 平台跳过仍属于相同 Runtime 实现，没有重新把它记为新的 Python运行。新提交 CI 另验；Q02 模型质量、默认 persona v4 和草稿 PR 状态保持，未新增付费模型或 AGY 调用。
