import { defineConfig } from "vitepress";

export default defineConfig({
  lang: "zh-CN",
  title: "ChatWaifu NEXT",
  description: "把喜欢的角色留在桌面上。她会听你说话，也会记得。",
  base: "/ChatWaifu-NEXT-docs/",
  cleanUrls: true,
  lastUpdated: true,
  head: [
    ["meta", { name: "theme-color", content: "#17111c" }],
    ["meta", { name: "color-scheme", content: "light dark" }],
    [
      "link",
      {
        rel: "icon",
        type: "image/png",
        href: "/ChatWaifu-NEXT-docs/brand/chatwaifu-mark-small.png",
      },
    ],
  ],
  sitemap: {
    hostname: "https://mubai-he.github.io/ChatWaifu-NEXT-docs/",
  },
  themeConfig: {
    logo: "/brand/chatwaifu-mark-small.png",
    siteTitle: "ChatWaifu NEXT",
    nav: [
      { text: "首页", link: "/" },
      { text: "安装", link: "/guide/getting-started" },
      { text: "配置", link: "/guide/configuration" },
      { text: "二次开发", link: "/guide/customization" },
      {
        text: "发行说明",
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
      message:
        "为那些想把角色留在身边的人而做 · 角色与模型权利归各自权利人所有",
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
