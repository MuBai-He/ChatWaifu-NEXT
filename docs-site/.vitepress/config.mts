import { defineConfig } from "vitepress";

export default defineConfig({
  lang: "zh-CN",
  title: "ChatWaifu NEXT",
  description:
    "Local-first 实时 AI 角色 Runtime：语音、记忆、Live2D、Skills 与桌宠。",
  base: "/ChatWaifu-NEXT-docs/",
  cleanUrls: true,
  lastUpdated: true,
  head: [
    ["meta", { name: "theme-color", content: "#2a172e" }],
    ["meta", { name: "color-scheme", content: "light dark" }],
    [
      "link",
      {
        rel: "icon",
        type: "image/svg+xml",
        href: "/ChatWaifu-NEXT-docs/logo.svg",
      },
    ],
  ],
  sitemap: {
    hostname: "https://mubai-he.github.io/ChatWaifu-NEXT-docs/",
  },
  themeConfig: {
    logo: "/logo.svg",
    siteTitle: "ChatWaifu NEXT",
    nav: [
      { text: "首页", link: "/" },
      { text: "安装", link: "/guide/getting-started" },
      { text: "配置", link: "/guide/configuration" },
      { text: "二次开发", link: "/guide/customization" },
      {
        text: "v0.2",
        items: [
          { text: "Windows 发行状态", link: "/guide/windows" },
          { text: "本地模型包", link: "/guide/worker-packs" },
          { text: "许可边界", link: "/guide/licensing" },
        ],
      },
    ],
    sidebar: {
      "/guide/": [
        {
          text: "开始使用",
          items: [
            { text: "安装总览", link: "/guide/getting-started" },
            { text: "macOS 与 Web 开发", link: "/guide/web-development" },
            { text: "Windows x64", link: "/guide/windows" },
          ],
        },
        {
          text: "模型与声音",
          items: [
            { text: "本地 AI Worker Packs", link: "/guide/worker-packs" },
            { text: "模型与 TTS 配置", link: "/guide/configuration" },
          ],
        },
        {
          text: "开发者",
          items: [
            { text: "二次开发指南", link: "/guide/customization" },
            { text: "故障排查", link: "/guide/troubleshooting" },
            { text: "许可与私有资产", link: "/guide/licensing" },
          ],
        },
      ],
    },
    search: { provider: "local" },
    lastUpdated: {
      text: "最后更新",
      formatOptions: {
        dateStyle: "medium",
        timeStyle: "short",
      },
    },
    outline: {
      level: [2, 3],
      label: "本页目录",
    },
    docFooter: {
      prev: "上一页",
      next: "下一页",
    },
    returnToTopLabel: "回到顶部",
    sidebarMenuLabel: "目录",
    darkModeSwitchLabel: "外观",
    lightModeSwitchTitle: "切换到浅色模式",
    darkModeSwitchTitle: "切换到深色模式",
    footer: {
      message: "Local-first by design · 角色资产与模型权利归各自权利人所有",
      copyright: "ChatWaifu NEXT documentation",
    },
  },
  markdown: {
    theme: {
      light: "github-light",
      dark: "github-dark",
    },
    lineNumbers: true,
  },
});
