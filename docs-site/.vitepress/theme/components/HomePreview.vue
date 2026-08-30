<script setup lang="ts">
import { onBeforeUnmount, ref } from "vue";
import { withBase } from "vitepress";

const scene = ref<HTMLElement | null>(null);
const roomImage = withBase("/brand/moonlit-room.png");
let animationFrame = 0;

function canUseParallax(): boolean {
  return (
    window.matchMedia("(hover: hover) and (pointer: fine)").matches &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
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
    element.classList.add("is-following-pointer");
  });
}

function resetParallax(): void {
  const element = scene.value;
  if (!element) return;

  window.cancelAnimationFrame(animationFrame);
  element.style.setProperty("--scene-x", "0px");
  element.style.setProperty("--scene-y", "0px");
  element.classList.remove("is-following-pointer");
}

onBeforeUnmount(() => window.cancelAnimationFrame(animationFrame));
</script>

<template>
  <figure
    ref="scene"
    class="cw-scene"
    aria-labelledby="cw-scene-caption"
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
    <div class="cw-scene__shade" aria-hidden="true" />
    <i class="cw-scene__mote cw-scene__mote--one" aria-hidden="true" />
    <i class="cw-scene__mote cw-scene__mote--two" aria-hidden="true" />
    <i class="cw-scene__mote cw-scene__mote--three" aria-hidden="true" />

    <figcaption id="cw-scene-caption" class="cw-scene__dialogue">
      <span class="cw-scene__name">ChatWaifu NEXT</span>
      <p>欢迎回来。今天发生了什么，也说给我听听吧。</p>
      <span class="cw-scene__aside">嗯，我在听。你慢慢说就好。</span>
      <span class="cw-scene__advance" aria-hidden="true">⌄</span>
    </figcaption>
  </figure>
</template>
