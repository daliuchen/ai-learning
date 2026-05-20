# MCP Advanced 02：Agent Skills —— 用可移植的 Skill 包指导 Agent 构建 MCP

> **一句话**：Agent Skills 是 Anthropic 推出的「可移植指令包」标准——你的 Coding Agent（Claude Code / Cursor 等）能装上特定领域的 Skill 后，**用知识带着你写代码**。官方维护了 `mcp-server-dev` 这套 Skill，专门指导 AI 怎么帮你 scaffold MCP Server / App / Bundle。

> ⚠️ Agent Skills 协议本身是开放标准，但当前最完整的实现是 Claude Code 的 `.claude/skills/` 体系。本篇基于 Claude Code 视角写。

---

## 1. 什么是 Skill

Skill 是给 Coding Agent 的"领域知识包"。一个 Skill 包含：

```
my-skill/
├── SKILL.md           # 入口：什么时候触发 + 怎么做
└── references/        # 详细参考资料（auth 流程 / 模板 / 错误码）
    ├── auth.md
    ├── patterns.md
    └── manifest.md
```

Agent 在对话中根据 `SKILL.md` 的 trigger 条件决定是否激活这个 Skill。激活后，Agent 按 SKILL.md 的指导工作，需要时按需读取 references。

---

## 2. 为什么和 MCP 有关

写好一个生产级 MCP Server 涉及很多决策：

- 用 stdio 还是 Streamable HTTP？
- 工具粒度细还是粗？
- 鉴权方案？
- 错误处理怎么写？
- ……

每个项目都让用户从零开始想这些，效率太低。**Agent Skill 把"如何设计 MCP"的知识打包成 Agent 能消费的格式**，让 Coding Agent 帮你一步步问需求 + scaffold 代码。

---

## 3. 官方 mcp-server-dev Skill

Anthropic 在 [claude-plugins-official](https://github.com/anthropics/claude-plugins-official) 维护了 `mcp-server-dev` 这套 Skill：

| Skill | 用途 |
|-------|------|
| `build-mcp-server` | 入口，问需求 + 决定 deployment 模式 + 路由到下面两个 |
| `build-mcp-app` | 加交互式 UI 组件（MCP Apps） |
| `build-mcpb` | 打包成 MCPB（含 runtime 的本地 server） |

### 3.1 安装

在 Claude Code 里：

```
/plugin marketplace add anthropics/claude-plugins-official
/plugin install mcp-server-dev
```

或者把 SKILL 目录 clone 到自己的 `.claude/skills/` 下。

### 3.2 使用

装好后在 Claude Code 里说：

```
帮我写一个能查询公司 PostgreSQL 数据库的 MCP Server
```

Skill 会自动激活，开始问：

- 这个 Server 连什么？（API / 本地进程 / 文件系统 / 硬件）
- 谁会用？（个人 / 团队 / 公开）
- 多少个操作？（少量 / 大量包装 API）
- 用户交互需求？（text / elicitation / MCP App UI）
- 上游鉴权？（API Key / OAuth / 无）

如果你开局说全了，Skill 跳过 discovery 直接 scaffold。

---

## 4. Skill 的四种 Deployment 推荐

`build-mcp-server` 根据问询结果给出四种推荐：

### 4.1 Remote Streamable HTTP（默认）
- 包云 API、零安装、统一鉴权
- Scaffold：Cloudflare Workers 或 Express/FastMCP

### 4.2 MCP Apps（交互式 UI）
- 用户需要表单 / 图表 / dashboard 等富交互
- 切到 `build-mcp-app` Skill

### 4.3 MCPB（MCP Bundle）
- 必须访问用户本地（文件 / 桌面 app / localhost）
- 打包成 `.mcpb` 含 runtime，用户一键安装无需配 Python / Node
- 切到 `build-mcpb` Skill

### 4.4 Local stdio
- 原型 / 学习
- 后续可升级到 MCPB

---

## 5. 自己写 Skill（指导别人写 MCP）

如果你公司内部有一套"MCP Server 标准模板"，可以把它写成 Skill 给团队共用。

`my-company-mcp/SKILL.md`：

```markdown
---
name: My Company MCP Server
description: 帮我按公司 MCP 规范创建一个内部 MCP Server。当用户说"建一个公司 MCP"时触发。
triggers:
  - "建一个公司 MCP"
  - "company MCP"
---

# 当用户要建一个公司内部 MCP Server 时，按以下步骤：

1. 用 Python FastMCP（公司标准）
2. 必须挂在 FastAPI 上，Streamable HTTP 模式
3. 加 OAuth 中间件，issuer = https://auth.example.com
4. 命名约定：`<team>__<tool_name>`
5. 必须接 Logfire 可观测（参考 references/observability.md）
6. 错误处理用 ToolError（参考 references/errors.md）
7. 提供 Dockerfile 和 k8s manifest 模板

参考：references/server-template.py
```

`references/server-template.py` 放一份可复用代码模板。

---

## 6. Skill 工作流和普通对话的区别

| 普通对话 | Skill 驱动 |
|---------|-----------|
| 你说"建 MCP Server" → LLM 凭训练知识写 | Skill 先问关键决策 → 用最佳实践 scaffold |
| 工具调用零散 | Skill 把工具组合成有逻辑的工作流 |
| 输出风格不稳定 | Skill 强制风格一致 |
| 没有公司私有知识 | Skill 里能塞你的私有规范 |

---

## 7. 与 MCP 协议本身的关系

Agent Skills 和 MCP 是**互补**关系：

- **MCP** 是 Server / Client 协议：让 Agent 能用外部能力
- **Agent Skills** 是 Agent 自身的"知识包"：让 Agent 知道何时用何种 MCP

举个例子：你的 Skill 说"用户要建 MCP Server 时，先问需求再 scaffold"。Skill 本身可能调用：

- 一个 MCP Tool 拉取公司模板
- 一个 MCP Tool 验证 namespace 是否合规
- 一个 MCP Resource 取最新的 SDK 版本

Skill 是大脑、MCP 是手脚。

---

## 8. 自动化场景

Skill 的真正杀手锏是**自动化重复工作**。例子：

### 8.1 添加新 MCP 工具
Skill：当用户说"给现有 Server 加个工具" → 自动：
1. 用 `Read` 看 server.py
2. 在合适位置插入 `@mcp.tool()` 函数
3. 加测试到 tests/
4. 跑 ruff / mypy

### 8.2 升级 SDK 版本
Skill：当用户说"升级 mcp" → 自动：
1. 看当前版本 + 最新版本
2. 看 changelog
3. 跑测试
4. 修要修的地方

### 8.3 排查问题
Skill：当 Server 无响应时 → 自动：
1. 起 Inspector 试连接
2. 看 stderr / log
3. 列举可能原因

---

## 9. 实战：用 mcp-server-dev 跟着做

假设你想从零写一个能查 GitHub 的 MCP Server。

```
[user]: 我想写一个 MCP Server 接 GitHub API，给我和团队用
```

mcp-server-dev 接管对话：

```
[Skill]: 好的，几个问题：
1. 主要操作？（搜 issue / 创建 PR / 看 CI 状态 / 其他）
2. 谁会用？看起来是团队，那部署模式选 remote 还是每人本地装？
3. GitHub auth 用 Personal Token 还是 GitHub App？
```

回答完，Skill scaffold 出：

- `server.py`（FastMCP + Streamable HTTP）
- `auth.py`（GitHub App auth middleware）
- `tools/`（按操作分组的工具）
- `Dockerfile` + `docker-compose.yml`
- `README.md`（含给团队的安装说明）
- `tests/`

然后 Skill 引导你跑 Inspector 验证、然后接到 Claude Code 试。

---

## 10. 跟其他 IDE 的兼容性

Agent Skills 是开放格式，但当前 Claude Code 的 `.claude/skills` 实现最完整。其他 IDE：

| IDE | Skill 支持 |
|-----|-----------|
| Claude Code | ✅ 完整 |
| Cursor | ⚠️ 部分（rules 系统类似但不完全兼容） |
| VS Code Copilot | ❌ 暂无对应 |
| Continue / Cline | ⚠️ 各有不同的 prompt rule 机制 |

格式标准还在演进，未来跨 IDE 兼容性应该会改善。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **Skill 没触发** | trigger 词描述要明确，看 SKILL.md frontmatter |
| **Skill 写得太死** | Skill 应该是 guidance，不是 rigid script |
| **references 太多 Agent 读不过来** | 只在需要时引用，按需读 |
| **Skill 和你想要的冲突** | 用户可以 override，Skill 不是法律 |

---

## 12. 下一步

- 📖 MCP Registry 发布 → [03-registry.md](./03-registry.md)
- 🛠️ 实战项目 → 07-practice/01-project-internal-kb（可以用 Skill 辅助生成）
- 🔍 用 Skill 在 Claude Code 里写本手册剩下的实战项目

## 参考资料

- Build with Agent Skills：https://modelcontextprotocol.io/docs/develop/build-with-agent-skills
- Agent Skills 仓库：https://github.com/anthropics/claude-plugins-official
- 通用 Agent Skills 标准：https://agentskills.io
- mcp-server-dev Skill 源码：https://github.com/anthropics/claude-plugins-official/tree/main/plugins/mcp-server-dev
