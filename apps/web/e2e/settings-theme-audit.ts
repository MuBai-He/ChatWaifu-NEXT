import type { Page } from "@playwright/test";

/** Inspect rendered text and surfaces, including legacy components inside the theme. */
export async function settingsThemeFindings(page: Page) {
  return page.locator(".settings-material").evaluateAll((roots) => {
    const rgb = (value: string) =>
      value.match(/[\d.]+/g)?.map(Number) ?? [0, 0, 0, 0];
    const over = (front: number[], back: number[]) =>
      front
        .slice(0, 3)
        .map(
          (value, i) =>
            value * (front[3] ?? 1) + back[i] * (1 - (front[3] ?? 1)),
        );
    const luminance = (color: number[]) =>
      color.slice(0, 3).reduce((sum, value, index) => {
        const c = value / 255;
        return (
          sum +
          (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4) *
            [0.2126, 0.7152, 0.0722][index]
        );
      }, 0);
    const findings: {
      element: string;
      text: string;
      issue: string;
      value: number;
    }[] = [];
    const elements = new Set(
      roots.flatMap((root) => [root, ...root.querySelectorAll("*")]),
    );
    for (const element of elements) {
      if (
        !(element instanceof HTMLElement) ||
        element.closest("svg, canvas, [hidden], [aria-hidden='true']")
      )
        continue;
      const box = element.getBoundingClientRect();
      if (
        !box.width ||
        !box.height ||
        box.bottom <= 0 ||
        box.top >= innerHeight ||
        box.right <= 0 ||
        box.left >= innerWidth
      )
        continue;
      const chain: HTMLElement[] = [];
      let visible = true;
      for (
        let parent: HTMLElement | null = element;
        parent;
        parent = parent.parentElement
      ) {
        const css = getComputedStyle(parent);
        if (
          css.visibility === "hidden" ||
          css.display === "none" ||
          Number(css.opacity) < 0.99 ||
          parent.matches(":disabled")
        )
          visible = false;
        if (
          parent !== element &&
          /auto|scroll|hidden|clip/.test(css.overflow + css.overflowY)
        ) {
          const clip = parent.getBoundingClientRect();
          if (
            box.bottom <= clip.top ||
            box.top >= clip.bottom ||
            box.right <= clip.left ||
            box.left >= clip.right
          )
            visible = false;
        }
        chain.unshift(parent);
      }
      if (!visible) continue;
      const style = getComputedStyle(element);
      const background = chain.reduce(
        (back, parent) =>
          over(rgb(getComputedStyle(parent).backgroundColor), back),
        [21, 17, 25],
      );
      const ownBackground = rgb(style.backgroundColor);
      const label =
        element.tagName.toLowerCase() +
        (element.className
          ? "." + element.className.trim().replace(/\s+/g, ".")
          : "");
      const text = [...element.childNodes]
        .filter((node) => node.nodeType === Node.TEXT_NODE)
        .map((node) => node.textContent?.trim())
        .filter(Boolean)
        .join(" ");
      // Small status dots and switch thumbs intentionally remain bright.
      if (
        box.width > 24 &&
        box.height > 24 &&
        (ownBackground[3] ?? 1) > 0.3 &&
        luminance(background) > 0.3 &&
        !element.closest(
          ".channels-settings-qr, .channels-settings-qrcode, .channels-settings-qrcode-frame",
        )
      ) {
        findings.push({
          element: label,
          text: text.slice(0, 70),
          issue: "light surface in dark settings",
          value: luminance(background),
        });
      }
      if (!text || element.matches("option, script, style")) continue;
      const foreground = over(rgb(style.color), background);
      const a = luminance(foreground),
        b = luminance(background);
      const contrast = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      const large =
        parseFloat(style.fontSize) >= 24 ||
        (parseFloat(style.fontSize) >= 18.66 &&
          parseInt(style.fontWeight) >= 700);
      if (contrast < (large ? 3 : 4.5))
        findings.push({
          element: label,
          text: text.slice(0, 70),
          issue: "text contrast",
          value: Math.round(contrast * 100) / 100,
        });
    }
    return findings;
  });
}
