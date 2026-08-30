import DefaultTheme from "vitepress/theme";
import type { Theme } from "vitepress";
import HomePreview from "./components/HomePreview.vue";
import "./custom.css";

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component("HomePreview", HomePreview);
  },
} satisfies Theme;
