---
layout: home

hero:
  name: ChatWaifu NEXT
  text: 为一个角色，构建完整的生命感
  tagline: Local-first 实时 AI 角色 Runtime。把对话、语音、记忆、Live2D、桌宠与 Runtime Skills 放进同一条可取消、可观测、可扩展的链路。
  image:
    src: /hero-orbit.svg
    alt: ChatWaifu NEXT 月牙与星芒标识
  actions:
    - theme: brand
      text: 开始使用
      link: /guide/getting-started
    - theme: alt
      text: 二次开发
      link: /guide/customization
    - theme: alt
      text: 配置指南
      link: /guide/configuration

features:
  - icon: ◌
    title: 一条真实的语音回路
    details: 浏览器麦克风、VAD、faster-whisper、LLM、TTS、WebRTC 与抢话打断共享 generation 身份。
  - icon: ◇
    title: 会成长的 Character Kernel
    details: 人格、情绪、关系、场景与 Prompt 预算被显式管理，换模型也不把角色交给一次 system prompt 碰运气。
  - icon: ✦
    title: 有来源的长期记忆
    details: 提取、策略、去重、冲突修正、来源追踪、混合召回与隐私确认构成可审计的记忆闭环。
  - icon: ⌁
    title: Skills 与完整 MCP
    details: Tools、Resources、Prompts、权限、确认、超时、取消、审计与平台沙箱都留在 Runtime 安全边界内。
  - icon: ☾
    title: Web Galgame 与原生桌宠
    details: 两套编译期产品入口，共享 Runtime 和领域模块；浏览器沉浸对话与 Tauri 桌宠不再互相拖累。
  - icon: ⇌
    title: 可替换的模型与声音
    details: 聊天、提取、总结、Embedding 分角色配置；本地 Worker 与云端 Provider 通过统一契约切换。
---

<HomePreview />

<p class="cw-kicker">FROM CHATWAIFU TO NEXT</p>

## 不是旧项目的补丁，而是一次完整重构

[早期 ChatWaifu](https://github.com/cjyaddone/ChatWaifu) 把 ChatGPT、VITS、语音识别与 Live2D
第一次放在了同一份“赛博女友”体验里。NEXT 保留这份产品愿景，也保留中文、English、日本語的
开放气质；工程上则从单脚本工作流重建为 Runtime、协议、Worker、Web 与 Desktop 可独立演进的系统。

<div class="cw-grid">
  <article class="cw-card">
    <span class="cw-card__tag">PRODUCT</span>
    <strong>角色体验先行</strong>
    <p>当前 Demo 围绕绫地宁宁主题的 Galgame 对话节奏展开，但角色包、声线与 Avatar renderer 都是可替换边界。</p>
  </article>
  <article class="cw-card">
    <span class="cw-card__tag">RUNTIME</span>
    <strong>本地是真相源</strong>
    <p>会话、关系、记忆、权限与事件保存在本地 Runtime；云模型只是一种可替换 Provider，不接管产品状态。</p>
  </article>
  <article class="cw-card">
    <span class="cw-card__tag">EXTENSIBLE</span>
    <strong>二开不必拆掉地基</strong>
    <p>稳定 contract、注册表、repository port 与 Worker 协议，让新角色、新模型、新技能都能从明确入口接入。</p>
  </article>
</div>

::: warning 当前发行状态
这是基础可用 Demo，不是已完成签名与公开许可审查的正式发行版。源码仓库不包含宁宁 Live2D、训练
声线、模型权重或 Cubism Core。Windows 安装候选已经过基础 x64 安装 smoke，但原生 x64/CUDA
笔记本上的 Worker Pack 构建与安装态真实推理仍待完成。
:::

## 选一个入口

| 你想做什么                                        | 推荐入口                                    |
| ------------------------------------------------- | ------------------------------------------- |
| 从全新 checkout 看界面或开发 Web                  | [macOS 与 Web 开发](/guide/web-development) |
| 构建 Windows x64 安装候选                         | [Windows x64](/guide/windows)               |
| 给 Windows 安装本地 Qwen3-TTS / faster-whisper    | [本地 AI Worker Packs](/guide/worker-packs) |
| 配置 OpenAI-compatible 模型、Embedding 或百炼声音 | [模型与 TTS 配置](/guide/configuration)     |
| 换角色、Provider、Avatar、记忆或 Skill            | [二次开发指南](/guide/customization)        |
| 启动失败、白屏、端口冲突或没声音                  | [故障排查](/guide/troubleshooting)          |
