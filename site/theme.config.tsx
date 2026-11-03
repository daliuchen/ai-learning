import React from 'react'
import { DocsThemeConfig, useConfig } from 'nextra-theme-docs'
import { useRouter } from 'next/router'

const config: DocsThemeConfig = {
  logo: (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 10 }}>
      <span style={{ fontWeight: 800, fontSize: 18, letterSpacing: '-0.02em' }}>AI Learning</span>
      <span className="al-logo-sub" style={{ fontSize: 13, opacity: 0.6 }}>大模型应用开发中文手册</span>
    </span>
  ),
  color: {
    hue: { light: 174, dark: 170 },
    saturation: { light: 78, dark: 70 },
  },
  footer: {
    content: (
      <span style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <span>AI Learning · 对照各框架官方文档编写，非官方教程</span>
        <span>公众号「Ethan 的 LLM 工程手册」同步更新</span>
      </span>
    ),
  },
  i18n: [],
  search: {
    placeholder: '搜索教程…',
  },
  sidebar: {
    defaultMenuCollapseLevel: 1,
    toggleButton: true,
  },
  toc: {
    title: '目录',
    backToTop: '回到顶部',
  },
  feedback: { content: null },
  darkMode: true,
  themeSwitch: {
    useOptions() {
      return { light: '浅色', dark: '深色', system: '跟随系统' }
    },
  },
  gitTimestamp: function GitTimestamp({ timestamp }) {
    return <>最后更新于 {timestamp.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })}</>
  },
  navigation: { prev: true, next: true },
  editLink: { component: null },
  nextThemes: {
    defaultTheme: 'system',
  },
  head: function useHead() {
    const { asPath } = useRouter()
    const { frontMatter, title: pageTitle } = useConfig()
    const base = 'AI Learning'
    const title =
      asPath === '/'
        ? base
        : `${frontMatter.title || pageTitle || ''} – ${base}`
    const desc =
      frontMatter.description ||
      'LangChain、Pydantic AI、MCP、RAG 与上下文工程的中文手册，对照官方文档编写，代码可运行'
    return (
      <>
        <title>{title}</title>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <meta name="description" content={desc} />
        <meta property="og:title" content={title} />
        <meta property="og:description" content={desc} />
        <meta property="og:type" content="website" />
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
      </>
    )
  },
}

export default config
