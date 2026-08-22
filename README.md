# ChatWaifu NEXT

ChatWaifu NEXT（ChatWaifuV2）是一次从空仓库开始的、local-first 的 AI
角色运行时重建。当前仓库完成了 **Phase 0（工程地基）**、**Phase 1（领域协议和
代码生成）**，并交付 **Phase 2 Avatar Lab 的 Fake/CI 完整路径**。专有 Cubism Core、
有授权的 Live2D 模型和真实渲染桥仍由开发者在本地提供，因此真实 Live2D 路径尚未验收。

## 当前交付

- Python、TypeScript、Rust 三套可执行质量门和 lockfile
- Pydantic 领域协议及版本感知解析器
- 可重复生成的 JSON Schema 和 TypeScript 类型
- Zod 运行时输入校验
- Python ↔ TypeScript golden contract fixtures/tests
- 独立 `/avatar-lab` 路由、语义 Cue 控制台和 FakeAvatarRenderer
- 分层 Cue 调度、generation invalidation、口型源、命中事件和性能遥测
- 官方 Cubism Web Framework 的固定版本 vendor 脚本与专有 Core 缺失诊断
- Vitest 单元测试与 Chromium Playwright 验收路径
- 10 份基础 ADR、CI workflows、项目级 Codex Development Skills

明确不在本轮范围：Pipecat、WebRTC、仓库内分发 Live2D Core/角色模型、Tauri
sidecar、AI 模型 SDK/权重、Fake Cascade、Runtime server、数据库业务表，以及
Postgres/Redis/Kafka/Kubernetes。

## 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 22+
- pnpm 11.19+
- Rust 1.98（`rust-toolchain.toml` 会固定版本）
- GNU Make
- `just`（可选；`justfile` 将命令委托给同一套 Make targets）

## 初始化

```bash
cp .env.example .env
make bootstrap
```

`make bootstrap` 会安装 Python/Node/Rust 依赖并重新生成协议产物。仓库不需要
Docker、数据库、模型或专有 SDK 就能运行 Fake Avatar Lab 和默认质量门。

## 常用命令

```bash
make format             # 格式化 Python、TypeScript、Rust
make lint               # Ruff、ESLint、Clippy
make typecheck          # Pyright、tsc、cargo check
make generate-protocol  # Pydantic -> JSON Schema -> TypeScript
make check-generated    # 生成后检查受控产物是否有 diff
make test               # Python、TypeScript、Rust 测试
make test-contract      # Python/TypeScript 跨语言协议测试
make test-avatar        # Avatar SDK 与 Web 单元测试
make test-e2e           # Chromium Avatar Lab 交互 smoke test
make dev-web            # 启动 Web 应用
make dev-avatar-lab     # 直接打开独立 Avatar Lab
make setup-live2d-framework # 拉取固定版本的官方公开 Framework
make check-live2d-vendor    # 检查本地 Core、bridge 和授权模型
make dev-runtime        # 提示 Runtime 尚未进入实现阶段
make dev-desktop        # 提示 Desktop 尚未进入实现阶段
make clean              # 清理可重建的本地产物
```

Web 首页默认位于 <http://127.0.0.1:5173>，Avatar Lab 位于
<http://127.0.0.1:5173/avatar-lab>。首次运行浏览器测试前执行：

```bash
pnpm --filter @chatwaifu/web exec playwright install chromium
```

真实 Live2D 本地接入步骤和许可证边界见 `vendor/live2d/README.md`。缺少专有文件时，
选择 Live2D renderer 会显示可操作错误，Fake/CI renderer 仍可完整运行。

## 协议工作流

1. 在 `packages/protocol-python/src/chatwaifu_protocol/` 修改协议源。
2. 运行 `make generate-protocol`。
3. 不要手工编辑 `schemas/domain/v1/` 或
   `packages/protocol-typescript/src/generated/domain.ts`。
4. 运行 `make test-contract` 和 `make check-generated`。

同一 major version 会忽略新增可选字段；未知 major、未知消息类型和非法 payload
会被明确拒绝。高频音视频正文不会伪装成持久化领域事件，协议只定义有界媒体头。

## 开发入口

在改代码前依次阅读：

1. `CODEX_HANDOFF.md`
2. `CHATWAIFU_NEXT_ARCHITECTURE.md`
3. `CHATWAIFU_NEXT_IMPLEMENTATION_PLAN.md`
4. `docs/implementation-status.yaml`
5. 相关 `docs/adr/` 与 `.agents/skills/`

贡献规则见 `CONTRIBUTING.md`，安全边界见 `SECURITY.md`。
