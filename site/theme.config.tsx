import React from 'react'
import { DocsThemeConfig, useConfig } from 'nextra-theme-docs'
import { useRouter } from 'next/router'

const config: DocsThemeConfig = {
  logo: (
    <span style={{ fontWeight: 700, fontSize: 18 }}>
      📚 AI Learning · 大模型应用开发学习手册集合
    </span>
  ),
  project: {
    link: 'https://github.com/daliuchen/ai-learning',
  },
  footer: {
    content: (
      <span>
        非官方教程 · 个人学习笔记 · 全部内容对照各官方文档独立编写
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
  feedback: {
    content: '有问题？提交反馈',
  },
  editLink: {
    content: '',
  },
  darkMode: true,
  nextThemes: {
    defaultTheme: 'system',
  },
  head: function useHead() {
    const { asPath } = useRouter()
    const { frontMatter, title: pageTitle } = useConfig()
    const base = 'AI Learning · 学习手册集合'
    const title =
      asPath === '/'
        ? base
        : `${frontMatter.title || pageTitle || ''} – ${base}`
    const desc =
      frontMatter.description ||
      'LangChain / Pydantic AI 等大模型应用开发系列中文深度教程，含可运行 demo'
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
