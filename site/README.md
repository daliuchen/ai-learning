# LangChain 教程站点

基于 [Nextra 3](https://nextra.site/) + Next.js 14 的静态文档站点，把 `../docs/` 与 `../demos/` 渲染成可在线浏览的教程。

## 目录

```
site/
├── pages/
│   ├── _meta.json          # 顶层导航
│   ├── index.mdx           # 站点首页
│   └── docs/               # ← 由 sync 脚本生成，勿手改
├── public/
│   ├── favicon.svg
│   └── demos/              # ← 由 sync 脚本生成
├── scripts/
│   └── sync-docs.mjs       # 同步上层 docs / demos
├── next.config.mjs
├── theme.config.tsx        # 顶部 / 侧边 / 主题配置
├── package.json
└── vercel.json
```

## 本地运行

```bash
cd site
npm install
npm run dev
```

浏览器打开 http://localhost:3000

`npm run dev` 会先自动跑 `scripts/sync-docs.mjs`：
- 读取 `../docs/**/*.md` → 复制为 `pages/docs/**/*.mdx`
- 自动生成每个目录的 `_meta.json`（按文件名排序，title 从 H1 读取）
- 复制 `../demos/` 整个目录到 `public/demos/`，供文档里的相对链接 `(/demos/xxx.py)` 直接下载

如果手动改了 `../docs/`，重新跑 `npm run sync` 或重启 dev。

## 部署到 Vercel

1. 把整个仓库 push 到 GitHub
2. 登录 https://vercel.com → New Project → Import 你的仓库
3. **Root Directory** 设为 `site`（关键，否则 Vercel 会去仓库根找 package.json）
4. Framework 选 `Next.js`（默认即可）
5. Build Command 留空 / 用默认 `next build`（package.json 里已配 `prebuild: sync-docs`）
6. Deploy

每次 `git push` 都会自动同步 docs 并重新部署。

### 自定义域名

Vercel 项目 → Settings → Domains → Add，按提示配 DNS 即可。

## 改主题 / 配置

- `theme.config.tsx`：logo、GitHub 链接、footer、search 文案、banner 等
- `next.config.mjs`：Nextra 选项（搜索、代码块、defaultShowCopyCode 等）
- `pages/_meta.json`：顶层菜单结构
- `pages/docs/**/_meta.json`：每个子目录的导航顺序与标题（自动生成，也可手动覆盖）

## 添加新内容

1. 在仓库根 `docs/` 下加 `.md` 文件
2. 文件首行用 `# 标题` 写好 H1（会自动成为侧边栏标题）
3. `npm run sync && npm run dev` 即可看到

## 已知坑

- Nextra 3 要求 Node 18+
- 第一次 build 较慢（生成全文搜索索引）
- 改完 `theme.config.tsx` 必须重启 dev server
- markdown 里相对链接已由 `sync-docs.mjs` 自动改写；如果你写新的链接风格，可能要扩展正则
