# @chatwaifu/web

React/Vite Web 应用包含全屏视觉小说式角色舞台和独立的 `/avatar-lab`。主舞台固定
Live2D 与当前一句对白，把滚动历史、语音/模型设置、Skills 和结构化记忆放进独立面板。
Avatar Lab 默认使用 `FakeAvatarRenderer`，不连接 Runtime、Pipecat、Tauri 或 AI
模型；React 只提交语义 `AvatarCue`，渲染帧循环由 `AvatarController` 独立管理。

```bash
make dev-avatar-lab
make test-avatar
make test-e2e
```

真实 Cubism Core、bridge 和角色资源均不入库；本地接入见
`vendor/live2d/README.md`。
