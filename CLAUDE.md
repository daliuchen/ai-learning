# Claude Code 工作指南

> 给在本仓库工作的 Claude 看的"项目说明书"。读完直接开工。

---

## 项目是什么

AI 应用开发学习手册集合，6 本手册共 228 篇深度长文，部署在 Vercel 公开访问。

- **线上**：https://ai-learning-pied-zeta.vercel.app
- **仓库**：本地 `/Users/cliu/cliu/me_workspace/ai-learning`，远程 GitHub
- **主分支**：`main`，push 后 Vercel 自动部署

### 6 本手册

| # | 目录 | 主题 | 篇数 |
|---|------|------|------|
| 01 | `01-langchain/` | LangChain 全家桶 | 35 |
| 02 | `02-pydantic-ai/` | Pydantic AI | 32 |
| 03 | `03-mcp/` | Model Context Protocol | 35 |
| 04 | `04-prompt-engineering/` | Prompt Engineering | 44 |
| 05 | `05-openai-agents-sdk/` | OpenAI Agents SDK | 38 |
| 06 | `06-embedding/` | Embedding & 向量检索 | 44 |

---

## 仓库结构

```
ai-learning/
├── 0X-<手册名>/              # 6 本手册的源 markdown（编辑这里）
│   └── NN-<章节>/
│       └── MM-<小节>.md
│
├── site/                     # Nextra 静态站点
│   ├── pages/                # 自动生成（不要手改！）
│   │   ├── index.mdx         # 首页（手改这里）
│   │   └── 0X-*/             # 由 sync-docs.mjs 生成
│   ├── scripts/
│   │   └── sync-docs.mjs     # 关键：手册 → Nextra 同步脚本
│   ├── theme.config.tsx
│   └── package.json
│
├── xhs-drafts/               # ⚠️ gitignored 私有目录（详见下文）
├── README.md                 # 项目门面
├── CLAUDE.md                 # 本文件
└── .gitignore
```

---

## 关键工作流

### 改一篇手册内容

直接编辑 `0X-<手册>/NN-<章节>/MM-<小节>.md`。

**不要直接改 `site/pages/0X-*/` 下的文件** —— 那是 `sync-docs.mjs` 生成的，每次构建会覆盖。

push → Vercel 自动构建 → 上线。

### 新增一本手册

1. 跟用户对齐大纲（不要直接动手写所有内容）
2. 创建 `0N-<手册名>/` 目录结构
3. 编辑 `site/scripts/sync-docs.mjs`：
   - `MANUAL_TITLES` 加新手册条目
   - `SECTION_TITLES` 加各章节标题
   - 必要时扩展 `shortTitle` 正则
4. 编辑 `site/pages/index.mdx`：加 Card + 学习路径
5. 编辑根 `README.md`：手册表加一行 + 目录树更新
6. 本地验证：`cd site && npx next build`，确认无错
7. 让用户确认后 commit + push

### 本地预览

```bash
cd site
npm run dev      # 启动 dev server
# 或
npx next build && npx next start
```

---

## 重要约束

### 1. 不要主动 commit / push

用户对 git 操作很谨慎。所有 commit / push **必须等用户明确指示**。改完代码先报告改了啥，让用户决定提交时机。

### 2. xhs-drafts/ 是私有目录

根目录有个 `xhs-drafts/`，已被 `.gitignore` 排除。里面是用户的小红书发文草稿。

**规则**：
- ✅ 用户要求时可以读 / 写 / 改这个目录
- 🚫 **永远不要把 `xhs-drafts/` 内容 stage 或 push 到 git**
- 🚫 不要在公开输出（PR 描述、commit message）里暴露这个目录的存在或内容
- 进入这个目录工作前先确认 `git check-ignore xhs-drafts/<file>` 返回 ignored

详细的小红书工作背景在 `xhs-drafts/NEXT-SESSION.md`（也是 gitignored）。

### 3. 不要手改 `site/pages/0X-*/`

那是 `sync-docs.mjs` 自动生成的。改源 `.md`，不要改生成产物。

### 4. 手册结构稳定，不重构

`0X-xx/MM-yy.md` 编号 + `sync-docs.mjs` 自动同步的架构经过验证，不要好心改成"更好的"结构。

---

## sync-docs.mjs 速查

这是项目最关键的脚本。功能：扫描 `0X-*/` 源目录，生成 `site/pages/0X-*/` 的 Nextra 页面 + `_meta.ts`。

每次 `next build` 前自动跑（在 `site/package.json` 的 build 钩子里）。

修改场景：
- 新手册 → 加 `MANUAL_TITLES`
- 新章节命名 → 加 `SECTION_TITLES`
- 文件名解析失败 → 检查 `shortTitle` 正则

文件位置：`site/scripts/sync-docs.mjs`。

---

## 沟通风格

- 中文对话为主
- 用户喜欢简短、直接的回复，不要长篇 summary
- 「干就完了」式响应优先，先动手再问细节（但仅限可逆操作）
- 不可逆操作（git commit/push、删文件、改 .gitignore）必须先确认

---

## 用户全局信息

用户在 `~/.claude/CLAUDE.md` 里维护了其他 codebase 的位置（gd / nextfe / nextapp / next-miniapp / yoshi），那些跟本项目无关，但如果用户提到这些代号你能认出来即可。

---

**END** —— 这一份是项目级别的"how to work"，私有上下文（小红书发文进度、个人思路）在 `xhs-drafts/NEXT-SESSION.md`。
