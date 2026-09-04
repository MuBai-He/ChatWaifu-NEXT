<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { withBase } from "vitepress";

type Mood = "thinking" | "speaking" | "listening";

interface Line {
  text: string;
  hold: number;
}

const LINES: readonly Line[] = [
  { text: "……啊，你回来了。今天过得怎么样？", hold: 3600 },
  { text: "嗯，我在听。你慢慢说就好。", hold: 3200 },
  { text: "论文的事我记下了，下次你不用再讲一遍。", hold: 3800 },
  { text: "要是我记错了什么，直接告诉我，我会改的。", hold: 3800 },
  { text: "那……我就在这儿，你先忙你的。", hold: 4200 },
];

const MOOD_LABEL: Record<Mood, string> = {
  thinking: "思考中",
  speaking: "说话中",
  listening: "正在听",
};

const scene = ref<HTMLElement | null>(null);
const roomImage = withBase("/brand/moonlit-room.png");

const lineIndex = ref(0);
const typed = ref("");
const mood = ref<Mood>("thinking");
const reducedMotion = ref(false);
const coarsePointer = ref(false);

const currentLine = computed(() => LINES[lineIndex.value]);
const isTyping = computed(
  () => !reducedMotion.value && typed.value.length < currentLine.value.text.length,
);
const hint = computed(() =>
  coarsePointer.value ? "点一下继续" : "点一下对话框继续，也可以按空格",
);

let timer = 0;
let animationFrame = 0;
let cursor = 0;
let running = false;
let inView = true;
let observer: IntersectionObserver | null = null;

function schedule(callback: () => void, delay: number): void {
  window.clearTimeout(timer);
  timer = window.setTimeout(callback, delay);
}

function beginLine(index: number): void {
  lineIndex.value = index;
  cursor = 0;
  typed.value = "";
  mood.value = "thinking";
  schedule(typeNext, reducedMotion.value ? 0 : 520 + Math.random() * 260);
}

function typeNext(): void {
  const { text, hold } = currentLine.value;

  if (reducedMotion.value) {
    typed.value = text;
    cursor = text.length;
    mood.value = "listening";
    schedule(() => beginLine((lineIndex.value + 1) % LINES.length), hold + 1600);
    return;
  }

  if (cursor < text.length) {
    mood.value = "speaking";
    cursor += 1;
    typed.value = text.slice(0, cursor);
    const char = text[cursor - 1];
    const pause = /[。？！…，、]/.test(char) ? 240 : 46 + Math.random() * 42;
    schedule(typeNext, pause);
    return;
  }

  mood.value = "listening";
  schedule(() => beginLine((lineIndex.value + 1) % LINES.length), hold);
}

function pause(): void {
  running = false;
  window.clearTimeout(timer);
}

function resume(): void {
  if (running || !inView || document.hidden) return;
  running = true;
  typeNext();
}

function advance(): void {
  if (!running) return;
  const next = (lineIndex.value + 1) % LINES.length;

  // Galgame convention: the first press finishes the sentence, the next one moves on.
  if (isTyping.value) {
    cursor = currentLine.value.text.length;
    typed.value = currentLine.value.text;
    mood.value = "listening";
    schedule(() => beginLine(next), currentLine.value.hold);
    return;
  }

  beginLine(next);
}

function handleVisibility(): void {
  if (document.hidden) pause();
  else resume();
}

function canUseParallax(): boolean {
  return !coarsePointer.value && !reducedMotion.value;
}

function updateParallax(event: PointerEvent): void {
  const element = scene.value;
  if (!element || !canUseParallax()) return;

  const bounds = element.getBoundingClientRect();
  const x = (event.clientX - bounds.left) / bounds.width - 0.5;
  const y = (event.clientY - bounds.top) / bounds.height - 0.5;

  window.cancelAnimationFrame(animationFrame);
  animationFrame = window.requestAnimationFrame(() => {
    element.style.setProperty("--scene-x", `${(-x * 12).toFixed(2)}px`);
    element.style.setProperty("--scene-y", `${(-y * 8).toFixed(2)}px`);
    element.style.setProperty("--glow-x", `${(x * 18).toFixed(2)}px`);
    element.style.setProperty("--glow-y", `${(y * 12).toFixed(2)}px`);
    element.classList.add("is-following-pointer");
  });
}

function resetParallax(): void {
  const element = scene.value;
  if (!element) return;

  window.cancelAnimationFrame(animationFrame);
  element.style.setProperty("--scene-x", "0px");
  element.style.setProperty("--scene-y", "0px");
  element.style.setProperty("--glow-x", "0px");
  element.style.setProperty("--glow-y", "0px");
  element.classList.remove("is-following-pointer");
}

onMounted(() => {
  reducedMotion.value = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  coarsePointer.value = !window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  document.addEventListener("visibilitychange", handleVisibility);

  const element = scene.value;
  if (element && "IntersectionObserver" in window) {
    observer = new IntersectionObserver(
      (entries) => {
        inView = entries.some((entry) => entry.isIntersecting);
        if (inView) resume();
        else pause();
      },
      { threshold: 0.15 },
    );
    observer.observe(element);
  } else {
    resume();
  }
});

onBeforeUnmount(() => {
  pause();
  window.cancelAnimationFrame(animationFrame);
  document.removeEventListener("visibilitychange", handleVisibility);
  observer?.disconnect();
  observer = null;
});
</script>

<template>
  <figure
    ref="scene"
    class="cw-scene"
    :class="[`is-${mood}`, { 'is-typing': isTyping }]"
    tabindex="0"
    role="group"
    aria-label="宁宁的对话演示。点击或按空格查看下一句。"
    @click="advance"
    @keydown.space.prevent="advance"
    @keydown.enter.prevent="advance"
    @pointermove="updateParallax"
    @pointerleave="resetParallax"
  >
    <div class="cw-scene__art" aria-hidden="true">
      <img
        :src="roomImage"
        alt=""
        width="1672"
        height="941"
        loading="eager"
        decoding="async"
      />
    </div>
    <div class="cw-scene__moon" aria-hidden="true" />
    <div class="cw-scene__lamp" aria-hidden="true" />
    <div class="cw-scene__shade" aria-hidden="true" />
    <i class="cw-scene__mote cw-scene__mote--one" aria-hidden="true" />
    <i class="cw-scene__mote cw-scene__mote--two" aria-hidden="true" />
    <i class="cw-scene__mote cw-scene__mote--three" aria-hidden="true" />
    <i class="cw-scene__mote cw-scene__mote--four" aria-hidden="true" />
    <i class="cw-scene__mote cw-scene__mote--five" aria-hidden="true" />

    <span class="cw-scene__motto" aria-hidden="true">在屏幕的一隅，写下属于你们的日常物语</span>

    <figcaption class="cw-scene__dialogue">
      <span class="cw-scene__name">宁宁</span>
      <span class="cw-scene__mood" aria-hidden="true">
        <i class="cw-scene__bars"><b /><b /><b /><b /></i>
        <span class="cw-scene__mood-text">{{ MOOD_LABEL[mood] }}</span>
      </span>
      <p class="cw-scene__line" aria-hidden="true">
        <span>{{ typed }}</span><i class="cw-scene__caret" />
      </p>
      <p class="cw-sr-only" aria-live="polite">{{ currentLine.text }}</p>
      <span class="cw-scene__aside">{{ hint }}</span>
      <span class="cw-scene__advance" aria-hidden="true">⌄</span>
    </figcaption>
  </figure>
</template>
