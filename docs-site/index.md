---
layout: home

hero:
  name: ChatWaifu NEXT
  text: 把喜欢的角色留在桌面
  tagline: 她会听你说话，也会记得。想沉浸地聊一会儿，就打开 Galgame 界面；想有人陪在桌边，就让桌宠一直待在那里。
  image:
    src: /brand/chatwaifu-mark.png
    alt: 丝带环绕月牙与星芒组成的 ChatWaifu NEXT 标志
  actions:
    - theme: brand
      text: 先跑起来
      link: /guide/getting-started
    - theme: alt
      text: 安装桌宠
      link: /guide/windows
    - theme: alt
      text: 换成我的角色
      link: /guide/customization
---

<HomePreview />

<section class="cw-chapters" aria-label="ChatWaifu NEXT 的三种体验">
  <article class="cw-chapter">
    <span class="cw-chapter__number">01</span>
    <div>
      <h2>开口就能聊</h2>
      <p>按下麦克风开始说话；说完会自动收尾，想插话时也能随时打断。声音、字幕、口型和动作按同一段对白往前走。</p>
    </div>
  </article>
  <article class="cw-chapter">
    <span class="cw-chapter__number">02</span>
    <div>
      <h2>下次见面，她还记得</h2>
      <p>你提过的小事、最近在忙什么、彼此熟悉到了哪一步，都可以慢慢留下来。记错了，也能查看来源、改掉或删掉。</p>
    </div>
  </article>
  <article class="cw-chapter">
    <span class="cw-chapter__number">03</span>
    <div>
      <h2>在网页里，也在桌面上</h2>
      <p>想专心聊天就打开 Galgame 界面，想安静陪伴就启动桌宠。两边共用同一套会话、记忆、关系和声音设置。</p>
    </div>
  </article>
</section>

<p class="cw-kicker">从 ChatWaifu 到 NEXT</p>

## 把当年的想法，认真做完

[早期 ChatWaifu](https://github.com/cjyaddone/ChatWaifu) 让聊天模型、角色语音、语音识别和 Live2D
第一次在同一个窗口里见面。NEXT 仍然想做同一件事：让角色真正陪你聊下去。只是这一次，语音、记忆、
桌宠和二次开发都被整理成了一套可以长期维护的项目。

<div class="cw-notes">
  <article class="cw-note">
    <span>对话</span>
    <strong>先把相处的感觉做好</strong>
    <p>当前 Demo 从宁宁主题出发，先看对白节奏、声音、表情和动作能不能自然地接在一起。</p>
  </article>
  <article class="cw-note">
    <span>记忆</span>
    <strong>她的记忆留在你的机器上</strong>
    <p>聊天记录、关系和长期记忆默认由本地 Runtime 保管；云模型负责回答，不接管角色本身。</p>
  </article>
  <article class="cw-note">
    <span>二开</span>
    <strong>换角色，不必推倒重来</strong>
    <p>人格、声音、模型和 Avatar 都有各自入口。想做自己的角色时，只换真正需要换的部分。</p>
  </article>
</div>

::: warning 当前发行状态
现在是基础可用 Demo，还不是完成签名与公开许可审查的正式发行版。源码仓库不包含宁宁 Live2D、
训练声线、模型权重或 Cubism Core。Windows 安装候选已通过基础 x64 安装 smoke；原生 x64/CUDA
笔记本上的 Worker Pack 构建与安装态真实推理仍待完成。
:::

## 你想从哪里开始？

| 目标                                   | 去这里                                      |
| -------------------------------------- | ------------------------------------------- |
| 先在浏览器里看一眼界面                 | [macOS 与 Web 开发](/guide/web-development) |
| 构建 Windows x64 桌宠安装候选          | [Windows x64](/guide/windows)               |
| 安装本地 Qwen3-TTS 或 faster-whisper   | [本地 AI Worker Packs](/guide/worker-packs) |
| 接上聊天模型、Embedding 或百炼声音     | [模型与 TTS 配置](/guide/configuration)     |
| 换角色、声音、Avatar、记忆规则或 Skill | [二次开发指南](/guide/customization)        |
| 排查启动失败、白屏、端口冲突或没有声音 | [故障排查](/guide/troubleshooting)          |
