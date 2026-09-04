<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";

// Renders nothing. Marks `[data-reveal]` blocks on the home page as `is-in`
// once they scroll into view so CSS can stagger their children with `--i`.
// Falls back to showing everything immediately when observation is unavailable.

let observer: IntersectionObserver | null = null;

function revealAll(targets: Iterable<Element>): void {
  for (const target of targets) target.classList.add("is-in");
}

onMounted(() => {
  const targets = document.querySelectorAll<HTMLElement>(".VPHome [data-reveal]");
  if (targets.length === 0) return;

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion || !("IntersectionObserver" in window)) {
    revealAll(targets);
    return;
  }

  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("is-in");
        observer?.unobserve(entry.target);
      }
    },
    { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
  );

  for (const target of targets) {
    target.classList.add("is-armed");
    observer.observe(target);
  }
});

onBeforeUnmount(() => {
  observer?.disconnect();
  observer = null;
});
</script>

<template>
  <span class="cw-reveal-anchor" aria-hidden="true" />
</template>
