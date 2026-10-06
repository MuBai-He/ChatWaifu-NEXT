# PR #50 依赖安全 gate 修复

2026-09-30，提交 `44bfaf7` 的 [Security CI dependencies job](https://github.com/MuBai-He/ChatWaifu-NEXT/actions/runs/36653828359/job/109693694391)发现 PyJWT 2.13.0 的 10 个已知漏洞：`CVE-2026-102274`、`CVE-2026-101917`、`CVE-2026-102273`、`CVE-2026-102269`、`CVE-2026-102267`、`CVE-2026-102268`、`CVE-2026-102272`、`CVE-2026-102271`、`CVE-2026-102265`、`CVE-2026-102266`，均列出最低修复版本 2.14.0。本轮角色评估没有改动依赖，新的审计结果触发了修复。

AGY Gemini 3.8 Flash High 只读追查到 `runtime → mcp 2.1.1 → pyjwt[crypto]>=2.10.1`，无版本上限；本项目没有直接 JWT 调用，MCP 服务的鉴权仍是 `hmac.compare_digest` 比较静态 Bearer token。项目未使用 LiveKit SDK，也未激活 MCP SDK 的 JWT OAuth 扩展。主代理对照锁文件与实际 middleware 核验了该调用边界，未采用“所有场景完全兼容”或“风险为零”的推断。

主代理独立确认 [PyPI 2.15.1](https://pypi.org/project/PyJWT/2.15.1/)及[官方发行记录](https://github.com/jpadilla/pyjwt/releases/tag/2.15.1)已发布，选择定向锁定 2.15.1。`uv lock --dry-run --upgrade-package pyjwt==2.15.1` 仅报告这一包变化；正式更新后解析比较全部 121 个锁定包，其他 120 项完整元数据均保持相同。没有改 pyproject、鉴权行为或 CI 的漏洞忽略策略。

```sh
uv lock --upgrade-package pyjwt==2.15.1
uv sync --all-packages --all-groups --locked
uv run --with pip-audit pip-audit --ignore-vuln PYSEC-2026-3740
uv run pytest services/runtime/tests/test_mcp_server.py \
  services/runtime/tests/test_runtime_skills.py \
  services/runtime/tests/test_runtime_skill_hardening.py -q
```

主代理独立验证：37 项 MCP/Runtime Skill 测试通过；pip-audit 退出 0，输出 `No known vulnerabilities found, 1 ignored`。忽略项是此前已记录的 NLTK `PYSEC-2026-3740`，此次未新增忽略；三个本地源码包无法由 PyPI 数据库审计。全量回归与最终提交 CI 状态另见[实施状态](implementation-status.md)。本依赖修复不改变 v3 persona 或模型评审结论，Q02 发布质量仍未通过。
