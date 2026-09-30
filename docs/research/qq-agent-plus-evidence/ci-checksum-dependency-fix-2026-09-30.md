# 评测证据校验和与依赖 CI 修复

日期：2026-09-30。主代理直接修改、审查和验证，未调用 AGY。对应失败提交为 `0f71bfc6b4b99cfcf5674c5be0c1acfb58cd7948`。

## 校验和误报

远端 gitleaks 在剩余场景证据清单的 38、77、116 行，将 `blinded_key.json` 文件名后的 64 位十六进制文件校验和识别为 `generic-api-key`。主代理重新计算三个模型目录的全部 21 个文件 SHA256，均与清单一致。这些文件使用合成输入，配对映射不是账号凭据。

清单版本改为 1.1：`files` 使用 `{"path": "...", "sha256": "..."}` 对象数组，将文件路径与校验和分开。原始模型回复、评分、配对映射和其他证据文件的字节均未改变。`.gitleaksignore` 只增加上述三个完整历史指纹，保留全 PR 历史扫描；没有忽略整个文件或关闭任何检测规则。

使用官方 gitleaks 8.24.3 Darwin arm64 发布包，并校验官方校验和。按远端 CI 的范围执行 `--no-merges --first-parent cc578c9cb375e80961aa6aeafbb2a8a294e192eb^..HEAD`，29 个历史提交扫描通过；本次改动文件的当前内容另行扫描通过。此处没有把目录全量扫描、历史 PR 扫描和当前改动扫描混为一个检查范围。

## 依赖补丁

远端文档和安全 CI 均被 `brace-expansion@5.0.9` 的审计结果阻断。已将兼容范围内的间接依赖更新至 5.0.12；锁文件仅版本、包校验和及依赖引用三处改变，没有修改直接依赖、其他包版本或 audit 例外。[嵌套展开公告](https://github.com/advisories/GHSA-qhr7-859c-m2p7)列出的 5.x 修复版本为 5.0.11，[逗号解析公告](https://github.com/advisories/GHSA-6j4f-fj2g-mc7p)列出的修复版本为 5.0.10。

主代理独立验证：

- `pnpm install --frozen-lockfile` 成功；`pnpm audit --json` 退出 0，各等级漏洞数均为 0。
- JS 测试 370 项通过：协议 36、Avatar SDK 22、Web 312。
- 全工作区 lint、类型检查通过。
- Web、桌面 UI、VitePress 文档构建通过；保留现有 bundle 大小和 Tauri 导入提示。
- 锁文件与清单 Prettier 检查、`git diff --check` 通过。

这些检查验证依赖补丁和证据清单，不作为角色回复质量、真实渠道投递或真实音频验收。最终提交的远端 CI 单独核对。

## Windows 中文结果读取

同一提交的 Windows Python CI 在新增快照校验用例读取 UTF-8 中文 JSONL 时，使用了系统默认 cp1252 编码，导致 UnicodeDecodeError。主代理为该用例的读取与损坏记录写入补齐显式 UTF-8，生产运行器原本已显式使用 UTF-8。独立将默认文件编码模拟为 cp1252：原测试复现解码失败，修复后的同一测试通过。实际 Windows 全集以修复提交的远端 CI 为准。
