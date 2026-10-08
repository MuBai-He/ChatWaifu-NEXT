# CI 验证策略

PR 提供快速反馈，main 验证产品整合，每周覆盖平台差异。减少重复的平台工作，而不是删掉
Runtime 的授权、作用域、取消、迟到输出、任务投递或数据库迁移测试。

| 检查             | PR                                                                             | main push                                                    | 定时 / 手动                                             |
| ---------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------ | ------------------------------------------------------- |
| Python 静态      | Python/Runtime/相关配置改动时执行 Ruff 与严格 Pyright                          | 同 PR                                                        | 每周一，全量                                            |
| Python 测试      | 完整 collection 分到四个独立 Linux runner                                      | 同 PR                                                        | 每周一及手动，Linux/macOS/Windows 各执行完整 collection |
| Neural worker    | worker、Python SDK、Python 协议或 CI 控制文件改动时，在 Linux/Python 3.10 验证 | 相关 Python 工作流触发时执行                                 | 每周一及手动，三平台/Python 3.10                        |
| Web 产品         | 相关前端改动时，在 Linux 验证共享包、Web 与 Desktop UI 构建及产物隔离          | 同 PR                                                        | 每周一及手动增加 Windows                                |
| Desktop 原生宿主 | 仅宿主/Rust 工具链改动时，在 Linux 执行 fmt、clippy、cargo test                | 原产品依赖范围触发三平台真实 Tauri 构建与 Runtime wheel 验证 | 每周一及手动，三平台完整构建                            |
| 协议             | 协议相关改动时，双语言 contract 与生成文件漂移检查                             | 同 PR                                                        | 手动                                                    |
| 浏览器与架构     | 相关改动时，确定性 Runtime 支撑的 Web/Desktop 浏览器测试与边界检查             | 同 PR                                                        | 手动                                                    |
| 密钥             | 始终执行 `secrets`                                                             | 始终执行                                                     | 每天及手动                                              |
| 依赖             | 始终产生 `dependencies`；清单/锁文件变化时审计对应生态                         | Python/JS 依赖全量审计                                       | 每天及手动，Python/JS 全量审计                          |
| 文档站点         | 构建和公开产物检查                                                             | 构建、公开产物检查和既有 Pages 发布                          | 手动                                                    |

## 完整测试分组

`tools/ci/pytest_shard.py` 是显式加载的 pytest 插件。正常本地 `uv run pytest` 不受影响。
CI 先收集完整用例，将排序后的 node ID 按轮转方式分成四组；各组保留原执行顺序。
同一 runner 内仍顺序执行；不同 runner 有独立文件系统与进程，不引入 xdist 共享环境竞态。

每组上传完整 collection 的数量、SHA-256 和本组选中的 node ID。`python-suite` 必须先确认
所有必要 job 成功，再验证四份清单数量一致、互不重叠、并集与完整 collection 的 SHA-256
完全一致。缺组、重复、不同 collection、空组、collection error 和实际测试失败均不能通过。
全量平台运行使用相同插件但只有一组，仍执行该平台的所有用例及原有平台 skip。

本地复现一组：

```sh
uv run pytest -p tools.ci.pytest_shard --ci-shard-count=4 --ci-shard-index=0 \
  --ci-shard-manifest=pytest-shard.json
```

改变组数时，必须同步更新矩阵和 `verify_pytest_shards.py --count`。增加测试无需维护领域映射；
新增用例自动进入完整 collection。不要用删除慢测试、弱化断言或允许空组的方式缩短 CI。

## 必需的安全状态

2026-10-08 核对的 main ruleset 要求 `secrets` 和 `dependencies`，且要求分支保持最新。
Security 工作流没有 PR paths 过滤，两个 job 名保持稳定。依赖未变时，只有安装与网络审计
步骤跳过；job 仍正常结束，并明确记录“清单未变”。这不表示本次执行了漏洞扫描。

`tools/ci/change_scope.py` 使用事件提供的完整 base/head SHA 和 Git merge-base diff；不调用有
路径数量限制的 PR 文件 API，不折叠重命名，删除清单仍触发审计。SHA 无效、Git diff 失败或
路径解码失败直接使 job 失败。Security 工作流或 scope helper 本身变化强制审计两种生态。
main、每天与手动不做路径省略，因此新公开的漏洞仍会被定期发现。

现有 NLTK 漏洞例外及 2026-10-31 复查日期保持原样；文档构建不再重复 `pnpm audit`。
当前 Python 审计覆盖已同步的 workspace 环境，JS 审计覆盖 pnpm workspace；独立模型 worker
环境和真实设备/渠道验收不能由这些检查代替。

不要给必需的 Security 工作流增加 paths 过滤，否则未触发的必需状态可能一直 pending。
job 级条件与工作流级省略的区别见
[GitHub 必需状态检查说明](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)。

## 耗时与使用边界

按实际 job 的开始/结束时间比较耗时，不把排队时间混入测试执行时间，也不把本地并行时间
当作托管 runner 的保证。四组运行的关键路径由最慢一组决定，runner 总时间则是各组之和。
冷缓存、仓库拉取、Playwright 系统依赖和 Rust 编译仍可能主导首次运行；3–5 分钟是常见 PR
反馈的优化目标，不是所有原生或依赖改动的固定 SLA。

PR 新提交会取消同一 PR 的旧检查；不同 PR、main、定时与手动运行不相互取消。工作流发布
到 main 后定时策略才生效。既有 PR 需要包含这次 CI 改动（通常更新 main 后 rebase）才能
使用新策略；仅创建此 PR 不会改变其他分支的工作流。

`release-web.yml` 和 `release-desktop.yml` 保留严格发布/候选安装包检查；这次策略修改本身
不会创建 release、合并 PR、部署 Runtime、修改持久数据或启动真实渠道。
