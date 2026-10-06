# PR #50 依赖安全 Gate 修复：undici 7.29.0 -> 7.29.1

2026-09-30，复现 Security CI `dependencies` 任务时，本地 `pnpm audit --audit-level high` 退出 1，检出 `undici` 7.29.0 的 2 个高危已知漏洞。提交 `44bfaf7` 的远端任务先在 pip-audit 失败，尚未执行 pnpm audit。

- [GHSA-rfgv-xxqx-mfg5](https://github.com/nodejs/undici/security/advisories/GHSA-rfgv-xxqx-mfg5)：未请求的 WebSocket 子协议导致进程异常退出。
- [GHSA-w293-vg96-wgc3](https://github.com/nodejs/undici/security/advisories/GHSA-w293-vg96-wgc3)：BalancedPool 丢失函数式 TLS 校验选项。

全量 `pnpm audit` 同时检出另外 8 项中低危已知漏洞（`GHSA-r53p-7pc4-xj5r`、`GHSA-2gqq-gqf2-x968`、`GHSA-8436-99hf-9mmv`、`GHSA-3wwx-pv8p-q78v`、`GHSA-pmjh-fq2x-6v4x`、`GHSA-3xpg-4rpp-hhhm`、`GHSA-2jfj-6hjv-fm6j`、`GHSA-rx4f-c7p8-82vq`）。本次更新后的审计不再报告这 10 项。

AGY Gemini 3.8 Flash High 完成了锁文件与证据文档写入，但结果回收时云端 EOF 导致终态 `ERROR`，不能记为成功委派。主代理确认进程退出、检查实际 diff，并独立重跑验收；下面的 AGY 检查记录与主代理复验范围分别列明。

## 依赖路径与父版本范围核验

通过 `pnpm why undici` 与元数据核查锁定链路：

- `undici@7.29.0` 仅作为间接传递依赖存在：
  - `apps/web` -> `jsdom@29.1.1` (devDependencies) -> `undici`
  - `packages/avatar-sdk`、`packages/protocol-typescript`、`apps/web` -> `vitest@4.1.11` -> `jsdom@29.1.1` -> `undici`
- 注册表父级依赖声明：`jsdom@29.1.1` 在 `dependencies` 中声明 `"undici": "^7.25.0"`。
- 版本兼容性：`^7.25.0` 语义范围完全兼容 `7.29.1`（`>=7.25.0 <8.0.0`），无须升级 `jsdom` 或改动直接依赖。
- 本项目各工作区 `package.json` 均无对 `undici` 的直接依赖，生产 Web 前端产物运行于浏览器原生 Fetch 环境，`jsdom` 与 `undici` 仅在 Node 测试/模拟环境中运行。

## 修复方案与锁文件变更

遵循 Astra 决策与极小改动原则，执行定向传递依赖锁定：

- 不修改任何工作区 `package.json`（避免引入不必要的 `pnpm.overrides` 或伪直接依赖）。
- 不引入 audit ignore，不削弱 CI 审计安全门禁。
- 注册表确认 `undici@7.29.1` 元数据：
  - `dist.integrity`: `sha512-RYONW2MeafgYlkVOKYKkA/Ag7BmXqgIWCa8t1m0JcxrQg9pI9lEqRhAOruOBCbAohOa/gkCF+iPi9hrgvTzu6Q==`
  - `engines`: `{"node": ">=20.18.1"}`
  - 无附加运行时子依赖。
- 定向更新 `pnpm-lock.yaml` 中的 `undici` 解析条目、`jsdom@29.1.1` 依赖引用及 snapshot，锁文件 diff 仅包含这 3 处变动，无任何无关依赖变更。

```sh
# 验证锁文件一致性与完整安装
pnpm install --frozen-lockfile

# 安全门禁验证
pnpm audit --audit-level high
pnpm audit

# 全量 JS 回归验证
pnpm test
pnpm lint
pnpm typecheck
pnpm build:web
pnpm build:desktop-ui
```

## 验证结果

1. **`pnpm install --frozen-lockfile`**：
   - 退出码 0，444 项依赖通过供应链策略校验（`Lockfile is up to date, resolution step is skipped`，`Packages: +2 -1`）。
2. **`pnpm audit --audit-level high`**：
   - 退出码 0，输出 `No known vulnerabilities found`，高危漏洞清零。
3. **`pnpm audit` (全量)**：
   - 退出码 0，元数据确认 `vulnerabilities`: `{ info: 0, low: 0, moderate: 0, high: 0, critical: 0 }`，此前 10 项漏洞全部闭环修复，无遗留中低危漏洞。
4. **`pnpm test`**：
   - 退出码 0。共 63 个测试套件、370 项测试全部通过（`@chatwaifu/protocol`: 3 files / 36 tests; `@chatwaifu/avatar-sdk`: 8 files / 22 tests; `apps/web`: 52 files / 312 tests）。
5. **`pnpm lint`**：
   - 退出码 0，所有包 0 错误 0 告警通过。
6. **`pnpm typecheck`**：
   - 退出码 0，TypeScript 类型检查全部通过。
7. **`pnpm build:web`**：
   - 退出码 0，成功生成 Web 生产产物。
8. **`pnpm build:desktop-ui`**：
   - 退出码 0，成功生成 Desktop UI 生产产物。
9. **格式检查**：
   - `pnpm exec prettier --check docs/research/qq-agent-plus-evidence/undici-ci-fix-2026-09-30.md` 校验通过。

## 限制与遗留风险

- 主代理独立运行 frozen-lockfile 安装、audit、全量 JS 测试、lint、typecheck、Web/桌面构建与格式检查，全部退出 0；370 项测试通过。构建仍有现有大 chunk 和 Tauri 静态/动态导入告警。Ruff/Pyright 与 Python 全集的 PyJWT 修复验收另见 [PyJWT 修复](pyjwt-ci-fix-2026-09-30.md)。最终 Node 22/Windows CI 状态另行记录；本机为 Node 26。
- 权限与边界限制：未触碰预存变更（`uv.lock` PyJWT 更新、`implementation-status.md` 及 `pyjwt-ci-fix-2026-09-30.md`）；未执行 git commit/push/deploy；未调用模型端点。
- 遗留风险：`undici` 在项目中仅通过 `jsdom` 用于单测模拟环境，不会打包至最终浏览器生产静态资源中。本次更新完全在 `jsdom@29.1.1` 的兼容声明范围 `^7.25.0` 内，回归测试证实单测与构建行为无异常。
