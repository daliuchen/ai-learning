import nextra from 'nextra'

const withNextra = nextra({
  theme: 'nextra-theme-docs',
  themeConfig: './theme.config.tsx',
  defaultShowCopyCode: true,
  search: {
    codeblocks: false,
  },
})

export default withNextra({
  reactStrictMode: true,
  output: 'standalone',
  // GitHub Pages 部署时把下面打开（替换为你的仓库名）
  // basePath: '/lang-chain-demo',
  // assetPrefix: '/lang-chain-demo',
})
