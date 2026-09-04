---
layout: home

hero:
  name: ChatWaifu NEXT
  text: 一个会听、会记事、能住在桌面上的角色。
  tagline: 2022 年的 ChatWaifu 是几个脚本拼起来的小玩具。NEXT 把它重做了一遍：说话能打断，记住的东西能查能删，浏览器和桌宠用同一个后端，角色想换就换。
  image:
    src: /brand/chatwaifu-mark.png
    alt: 丝带环绕月牙与星芒组成的 ChatWaifu NEXT 标志
  actions:
    - theme: brand
      text: 先跑起来（不需要模型）
      link: /guide/getting-started
    - theme: alt
      text: 装成 Windows 桌宠
      link: /guide/windows
    - theme: alt
      text: 换成我的角色
      link: /guide/customization
---

<HomePreview />

<section class="cw-chapters" aria-label="她能做什么" data-reveal>
  <article class="cw-chapter" style="--i: 0">
    <span class="cw-chapter__number">01</span>
    <div class="cw-chapter__body">
      <h2>说到一半可以打断她</h2>
      <p>按住麦克风说话，停下来她就接。她说到一半你想插嘴，直接开口就行，她会停下来听。声音、字幕、口型和动作跟着同一句台词走，不会出现字幕到了、声音还在上一句的情况。</p>
    </div>
    <div class="cw-demo cw-demo--wave" aria-hidden="true">
      <span class="cw-wave"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></span>
      <span class="cw-demo__label"><b class="cw-dot"></b>说话中，随时可以打断</span>
    </div>
  </article>
  <article class="cw-chapter" style="--i: 1">
    <span class="cw-chapter__number">02</span>
    <div class="cw-chapter__body">
      <h2>上次说的事她还记得</h2>
      <p>不是把聊天记录整包塞回去。你随口提的事会被挑出来、过一遍审核再存进本地数据库，每一条都能看到是哪天哪句话来的。记错了就改，不想留就删，她下次不会再提。</p>
    </div>
    <div class="cw-demo cw-demo--memory" aria-hidden="true">
      <div class="cw-memcard">
        <span class="cw-memcard__tag">记忆 · 近况</span>
        <p>最近在赶毕业论文，晚上经常熬夜</p>
        <div class="cw-memcard__meta">
          <span>来源：9 月 2 日 23:14 的对话</span>
          <span class="cw-memcard__actions"><b>改</b><b>删</b></span>
        </div>
      </div>
    </div>
  </article>
  <article class="cw-chapter" style="--i: 2">
    <span class="cw-chapter__number">03</span>
    <div class="cw-chapter__body">
      <h2>浏览器里聊，桌面上陪</h2>
      <p>想认真聊就开网页版，界面像 Galgame。不想被打扰就开桌宠，她安静地待在屏幕角落。两边不是同一个页面套两层皮，是各自编译的两个产物，但会话、记忆、关系和声音设置是同一份。</p>
    </div>
    <div class="cw-demo cw-demo--windows" aria-hidden="true">
      <div class="cw-win cw-win--web"><span>Web</span><i></i><i></i><i></i></div>
      <div class="cw-win cw-win--desk"><span>桌宠</span><i></i></div>
      <span class="cw-win__link"></span>
    </div>
  </article>
</section>

<section class="cw-history" data-reveal>
  <p class="cw-kicker" style="--i: 0">2022 → 现在</p>
  <h2 style="--i: 1">从 ChatWaifu 到 NEXT</h2>
  <p class="cw-history__lead" style="--i: 2">最早的 <a href="https://github.com/cjyaddone/ChatWaifu" target="_blank" rel="noreferrer">ChatWaifu</a> 是把 ChatGPT、VITS 声线和 Live2D 硬凑进一个窗口的实验。效果出乎意料地好，但代码确实只是几个脚本。NEXT 没有换方向，只是这次打算把它当成一个能维护好几年的项目来做。</p>
  <ul class="cw-then-now" style="--i: 3">
    <li><span class="cw-then-now__then">只能等她把话说完</span><span class="cw-then-now__arrow">→</span><span class="cw-then-now__now">想插嘴随时开口</span></li>
    <li><span class="cw-then-now__then">关掉窗口，聊过的全忘了</span><span class="cw-then-now__arrow">→</span><span class="cw-then-now__now">记在你自己的电脑里，能查、能改、能删</span></li>
    <li><span class="cw-then-now__then">换角色要翻代码改</span><span class="cw-then-now__arrow">→</span><span class="cw-then-now__now">人格、声音、模型、Avatar 各有各的文件</span></li>
    <li><span class="cw-then-now__then">一个窗口，开着就得盯着</span><span class="cw-then-now__arrow">→</span><span class="cw-then-now__now">网页里聊，桌宠留在桌面</span></li>
  </ul>
</section>

<section class="cw-status" data-reveal>
  <h2 style="--i: 0">老实说，现在做到哪了</h2>
  <p class="cw-status__lead" style="--i: 1">这是一个能用的 Demo，还不是正式发行版。哪些行、哪些还不行，先写在这里，省得你装完才发现。</p>
  <div class="cw-status__cols">
    <div class="cw-status__col cw-status__col--ok" style="--i: 2">
      <h3>能用</h3>
      <ul>
        <li>浏览器里语音对话，可以打断</li>
        <li>Live2D 表情、口型、动作</li>
        <li>本地记忆，可查可改可删</li>
        <li>Windows x64 安装候选，过了基础安装 smoke</li>
      </ul>
    </div>
    <div class="cw-status__col cw-status__col--todo" style="--i: 3">
      <h3>还差</h3>
      <ul>
        <li>安装包代码签名</li>
        <li>Worker Pack 在真机 x64 / CUDA 上的安装态推理</li>
        <li>公开发布前的许可与资产复核</li>
      </ul>
    </div>
    <div class="cw-status__col cw-status__col--none" style="--i: 4">
      <h3>仓库里没有</h3>
      <ul>
        <li>宁宁的 Live2D 模型</li>
        <li>训练好的声线和 checkpoint</li>
        <li>模型权重</li>
        <li>Cubism Core</li>
      </ul>
      <p>这些归各自的权利人，得自己准备。细节在 <a href="./guide/licensing">许可边界</a>。</p>
    </div>
  </div>
</section>

<section class="cw-start" data-reveal>
  <h2 style="--i: 0">你想从哪开始？</h2>
  <div class="cw-paths">
    <a class="cw-path" href="./guide/web-development" style="--i: 1">
      <span class="cw-path__goal">先在浏览器里看一眼界面</span>
      <span class="cw-path__to">macOS 与 Web 开发 <i>→</i></span>
    </a>
    <a class="cw-path" href="./guide/windows" style="--i: 2">
      <span class="cw-path__goal">构建 Windows x64 桌宠安装候选</span>
      <span class="cw-path__to">Windows x64 <i>→</i></span>
    </a>
    <a class="cw-path" href="./guide/worker-packs" style="--i: 3">
      <span class="cw-path__goal">装本地 Qwen3-TTS 或 faster-whisper</span>
      <span class="cw-path__to">本地 AI Worker Packs <i>→</i></span>
    </a>
    <a class="cw-path" href="./guide/configuration" style="--i: 4">
      <span class="cw-path__goal">接上聊天模型、Embedding 或百炼声音</span>
      <span class="cw-path__to">模型与 TTS 配置 <i>→</i></span>
    </a>
    <a class="cw-path" href="./guide/customization" style="--i: 5">
      <span class="cw-path__goal">换角色、声音、Avatar、记忆规则或 Skill</span>
      <span class="cw-path__to">二次开发指南 <i>→</i></span>
    </a>
    <a class="cw-path" href="./guide/troubleshooting" style="--i: 6">
      <span class="cw-path__goal">启动失败、白屏、端口冲突或没声音</span>
      <span class="cw-path__to">故障排查 <i>→</i></span>
    </a>
  </div>
</section>

<HomeReveal />
