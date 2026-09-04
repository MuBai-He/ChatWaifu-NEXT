import DefaultTheme from "vitepress/theme";
import type { Theme } from "vitepress";
import HomePreview from "./components/HomePreview.vue";
import HomeReveal from "./components/HomeReveal.vue";
import "./custom.css";

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component("HomePreview", HomePreview);
    app.component("HomeReveal", HomeReveal);
  },
} satisfies Theme;
