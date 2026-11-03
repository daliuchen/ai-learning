# MCP Advanced 02：Agent Skills 与 Skills over MCP

> Agent Skills 是 Anthropic 推出的可移植指令包标准。Claude Code、Cursor 这类编码 Agent 装上某个领域的 Skill 后，就能按照其中的知识带着你写代码。官方维护了一套 `mcp-server-dev` Skill，专门指导 AI 帮你搭建 MCP Server、App 和 Bundle。

Agent Skills 本身是一种开放格式（见 agentskills.io），Claude Code、VS Code / GitHub Copilot、Cursor、Gemini CLI、Cline、Goose、Codex 等都支持从各自的 skills 目录加载。这篇分两条线：一是用 Skill 指导 AI 编程助手写 MCP Server（`mcp-server-dev`），第 1 到 10 节讲这部分；二是 Skills over MCP，即 2026 年新增的官方扩展 `io.modelcontextprotocol/skills`，让 MCP Server 自己把 Skill 作为资源分发出去，第 11 节讲这部分。

---

## 1. 什么是 Skill

Skill 是给编码 Agent 的领域知识包。一个 Skill 包含：

```
my-skill/
├── SKILL.md           # 入口：什么时候触发 + 怎么做
└── references/        # 详细参考资料（auth 流程 / 模板 / 错误码）
    ├── auth.md
    ├── patterns.md
    └── manifest.md
```

Agent 在对话中根据 `SKILL.md` frontmatter 里的 `description`（写明这个 Skill 做什么、什么时候用）决定要不要激活它。激活后，Agent 按 SKILL.md 的指引工作，需要时再去读取 references。

---

## 2. 为什么和 MCP 有关

写一个生产级的 MCP Server 要做很多决定：用 stdio 还是 Streamable HTTP？工具粒度细一点还是粗一点？用什么鉴权方案？错误怎么处理？

每个项目都让用户从零开始想这些，效率太低。Agent Skill 把「怎么设计 MCP」的知识打包成 Agent 能直接使用的格式，让编码 Agent 一步步问清需求，再生成代码骨架。

---

## 3. 官方的 mcp-server-dev Skill

Anthropic 在 [claude-plugins-official](https://github.com/anthropics/claude-plugins-official) 里维护了 `mcp-server-dev` 这套 Skill：

| Skill | 用途 |
|-------|------|
| `build-mcp-server` | 入口：问清需求，决定部署方式，再转到下面两个 |
| `build-mcp-app` | 添加交互式 UI 组件（MCP Apps） |
| `build-mcpb` | 打包成 MCPB（自带运行时的本地 server） |

### 3.1 安装

在 Claude Code 里：

```
/plugin marketplace add anthropics/claude-plugins-official
/plugin install mcp-server-dev
```

也可以把 SKILL 目录 clone 到自己的 skills 目录下（Claude Code：`~/.claude/skills/` 或项目的 `.claude/skills/`；VS Code / Copilot：`~/.copilot/skills/`；Cursor：`~/.cursor/skills/`；Gemini CLI：`~/.gemini/skills/`；Codex：`~/.codex/skills/`；Cline：`~/.cline/skills/`；Goose：`~/.config/goose/skills/`）。用 Vercel 的 Skills CLI 可以跨 Agent 安装：`npx skills add <仓库>`。

### 3.2 使用

装好之后，在 Claude Code 里说：

```
帮我写一个能查询公司 PostgreSQL 数据库的 MCP Server
```

Skill 会自动激活，然后开始问你：这个 Server 要连接什么（API、本地进程、文件系统、硬件）？谁会用它（个人、团队、公开）？有多少个操作（少量，还是要包装大量 API）？需要什么样的用户交互（纯文本、elicitation、MCP App UI）？上游怎么鉴权（API Key、OAuth、不需要）？

如果你一开始就把这些说清楚了，Skill 会跳过询问，直接生成代码。

---

## 4. Skill 推荐的四种部署方式

`build-mcp-server` 会根据问询的结果，从四种方式里给出推荐。

Remote Streamable HTTP 是默认选项，适合包装云端 API，用户不用安装，鉴权也统一。生成的代码基于 Cloudflare Workers 或 Express，Python 则用 `MCPServer`（`from mcp.server import MCPServer`）。

MCP Apps 适合用户需要表单、图表、dashboard 等富交互的情况，会转到 `build-mcp-app` 这个 Skill。

MCPB（MCP Bundle）适合必须访问用户本地资源的情况（文件、桌面应用、localhost）。它会打包成一个自带运行时的 `.mcpb`，用户一键安装，不用自己配 Python 或 Node，对应的 Skill 是 `build-mcpb`。

Local stdio 适合做原型或者学习，之后可以升级成 MCPB。

---

## 5. 自己写 Skill（指导别人写 MCP）

如果公司内部有一套 MCP Server 的标准模板，可以把它写成 Skill 给团队共用。

`my-company-mcp/SKILL.md`：

```markdown
---
name: my-company-mcp
description: 按公司 MCP 规范创建内部 MCP Server。用户提到「建一个公司 MCP」「company MCP」或要新建内部 MCP Server 时使用。
---

# 当用户要建一个公司内部 MCP Server 时，按以下步骤：

1. 用 Python `mcp>=2.3` 的 `MCPServer`（`from mcp.server import MCPServer`，公司标准）
2. 必须挂在 Starlette / FastAPI 上，Streamable HTTP 模式，配 `transport_security` 白名单
3. 用 `TokenVerifier` + `AuthSettings`（issuer = https://auth.example.com），身份只读 `get_access_token()`
4. 命名约定：`<team>__<tool_name>`
5. 可观测走 OpenTelemetry（SDK 默认已发 span，参考 references/observability.md）
6. 错误处理用 ToolError（参考 references/errors.md）
7. 提供 Dockerfile 和 k8s manifest 模板

参考：references/server-template.py
```

`references/server-template.py` 里放一份可以复用的代码模板。

---

## 6. Skill 驱动的工作流和普通对话有什么不同

| 普通对话 | Skill 驱动 |
|---------|-----------|
| 你说「建一个 MCP Server」，LLM 凭训练时学到的知识写 | Skill 先问清关键决策，再按最佳实践生成代码 |
| 工具调用比较零散 | Skill 把工具组织成有条理的工作流 |
| 输出风格不稳定 | Skill 保证风格一致 |
| 不了解公司内部的规范 | Skill 里可以写进你们的内部规范 |

---

## 7. 和 MCP 协议本身的关系

Agent Skills 和 MCP 是互补的。MCP 是 Server 和 Client 之间的协议，让 Agent 能使用外部能力；Agent Skills 是 Agent 自己的知识包，让 Agent 知道什么时候该用哪种 MCP。

举个例子，你的 Skill 里写着「用户要建 MCP Server 时，先问需求再生成代码」。执行这个 Skill 时，Agent 可能会调用一个 MCP Tool 去拉取公司模板，调用另一个 MCP Tool 检查命名空间是否合规，再读一个 MCP Resource 拿到最新的 SDK 版本。

---

## 8. 自动化场景

Skill 最有价值的地方是把重复性的工作自动化。几个例子：

### 8.1 添加新的 MCP 工具
用户说「给现有的 Server 加一个工具」时，Skill 会：
1. 用 `Read` 查看 server.py
2. 在合适的位置插入 `@mcp.tool()` 函数
3. 在 tests/ 里加上测试
4. 运行 ruff 和 mypy

### 8.2 升级 SDK 版本
用户说「升级 mcp」时，Skill 会：
1. 查看当前版本和最新版本
2. 阅读 changelog
3. 运行测试
4. 修复需要改的地方

### 8.3 排查问题
Server 没有响应时，Skill 会：
1. 启动 Inspector 尝试连接
2. 查看 stderr 和日志
3. 列出可能的原因

---

## 9. 实战：跟着 mcp-server-dev 做一遍

假设你想从零写一个能查询 GitHub 的 MCP Server。

```
[user]: 我想写一个 MCP Server 接 GitHub API，给我和团队用
```

mcp-server-dev 会接管对话：

```
[Skill]: 好的，几个问题：
1. 主要操作？（搜 issue / 创建 PR / 看 CI 状态 / 其他）
2. 谁会用？看起来是团队，那部署模式选 remote 还是每人本地装？
3. GitHub auth 用 Personal Token 还是 GitHub App？
```

回答完这些问题，Skill 会生成：

- `server.py`（`MCPServer` + Streamable HTTP）
- `auth.py`（GitHub App 鉴权中间件）
- `tools/`（按操作分组的工具）
- `Dockerfile` 和 `docker-compose.yml`
- `README.md`（包含给团队的安装说明）
- `tests/`

接着 Skill 会引导你用 Inspector 验证，再接到 Claude Code 里试用。

---

## 10. 其他 IDE 的支持情况

Agent Skills 是开放格式，但目前 Claude Code 的 `.claude/skills` 实现得最完整。其他 IDE：

| IDE | Skill 支持 |
|-----|-----------|
| Claude Code | 完整支持 |
| Cursor | 支持（`~/.cursor/skills/`） |
| VS Code / GitHub Copilot | 支持（`~/.copilot/skills/`） |
| Gemini CLI / Codex / Cline / Goose | 支持（各自的 skills 目录，见第 3.1 节） |

官方 MCP 文档里的 skills 安装表格列出了上面这些 Agent。这份列表并不完整，其他 Agent 请查各自的文档。

---

## 11. Skills over MCP：由 Server 分发 Skill（2026 新扩展）

前面讲的 Skill 都装在 Agent 的本地目录里。Skills over MCP（扩展标识符 `io.modelcontextprotocol/skills`，SEP-2640，已进入 Final 状态）换了一种思路：让 MCP Server 把 Skill 一起提供出来。Skill 讲的是怎样组合使用这个服务的多个工具，把它和服务放在一起，版本自然就同步了，Client 也可以按需发现、按需加载。

一个 Skill 就是一个目录，至少包含一个带 YAML frontmatter 的 `SKILL.md`（遵循 Agent Skills 规范），再加上可选的支撑文件。这个扩展只定义了怎么发现和取回 Skill：

| 方法 | 作用 |
|------|------|
| `skills/list` | 列出 Server 提供的 Skill（元数据：URI、frontmatter、文件清单） |
| `skills/get` | 按 URI 获取单个 Skill 的条目（即使 `skills/list` 没有列出，只要 Server 提供了这个 Skill，就必须能回答） |
| `resources/directory/read`（可选，需要 `directoryRead` 能力） | 列出某个目录下的直接子项（Skill 里常会写「从 `templates/` 里挑一个」，而 `resources/list` 没法回答「某棵子树下有什么」） |
| 文件内容 | 用普通的 `resources/read`，约定 URI 在 `skill://...` 之下 |

Server 必须同时声明 `resources` 能力和 `io.modelcontextprotocol/skills` 扩展（在 `server/discover` 里）。每个 Skill 条目都带有完整的文件清单：每个文件（包括 `SKILL.md`）都有 `sha256:...` 摘要和字节数，或者整个清单是字符串 `"dynamic"`（表示内容按需生成，没有摘要可以校验）。2026-07-28 版的结果里还带有缓存字段 `ttlMs` / `cacheScope`。

读取 `SKILL.md` 并不会激活 Skill。Host 要走自己的 Skill 加载流程：校验内容，必要时征得用户批准，然后才放进模型上下文。

### 安全：Skill 内容是不可信的模型输入

- Host 在加载文件之前必须先拿到 Skill 条目，检查文件 URI 在清单里，并且字节数和 SHA-256 摘要都对得上；`"dynamic"` 的 Skill 没有摘要可以检查
- 摘要只能证明字节没变，不能证明 Server 在 `skills/get` 里给出的 `frontmatter` 和取回的 `SKILL.md` 里的 frontmatter 一致。如果你要依据 `allowed-tools` 这类字段做决定，要自己解析取回的文件再对比
- 规范要求 Host 在 Skill 内容交给模型之前标注它来自哪个 Server，并且在用户逐个 Skill 明确批准之前，绝不授予 frontmatter 里的 `allowed-tools`（或任何放宽权限的字段）

### Python SDK 的现状

官方 Python SDK 的文档站（py.sdk.modelcontextprotocol.io/advanced/skills/）介绍了内置的 `Skills` 扩展：Server 端把 `mcp.server.skills.Skills(list_skills=..., get_skill=..., read_directory=...)` 传给 `MCPServer(extensions=[...])`；Client 端用 `Client(extensions=[Skills()])`，再通过 `client.extension(Skills)` 拿到 `list_skills()` / `get_skill()` / `read_directory()`，并对服务端的响应做 SEP-2640 一致性校验。SDK 只提供协议原语，不负责扫描目录、读取文件、计算摘要，这些由你提供的 handler 完成，文件内容用 `mcp.add_resource(...)` 作为普通资源提供。

> 不过 `mcp 2.3.0`（2026 年 11 月 PyPI 上的最新版）里还没有 `mcp.server.skills`、`mcp.client.extensions.skills` 这两个模块。文档站跟随的是 main 分支，这些 API 要等后续版本才会发布，所以上面的内容来自规范和官方文档，这一节没有可运行的示例。同一个版本里，MCP Apps 的 `Apps` 扩展是存在并且可以用的（见[用 MCP Apps 在客户端中嵌入交互式 UI](01-mcp-apps.md)）。想在当前版本里提供 Skill，可以自己用普通资源（`skill://...` 的 `TextResource`）提供文件内容，再通过通用的 `Extension` 机制，或者低层的 `add_request_handler("skills/list", ...)` 自己实现发现方法（官方没有这种做法的示例，需要自己测试和 Client 的兼容性）。
>
> 其他 Client 是否支持，可以查官方 Extension Support Matrix 里的 "Skills" 一列。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| Skill 没有触发 | frontmatter 的 `description` 要写清楚做什么、什么时候用（激活靠它，规范里没有单独的 triggers 字段） |
| Skill 写得太死 | Skill 应该是指引，而不是一成不变的脚本 |
| references 太多，Agent 读不过来 | 只在需要时引用，按需读取 |
| Skill 和你的意图冲突 | 用户可以覆盖 Skill 的建议，它只是参考 |
| 照着 py.sdk 文档写 `from mcp.server.skills import Skills`，报 ModuleNotFoundError | 文档站跟随的是 main 分支，`mcp 2.3.0` 里还没有，留意后续版本 |
| 盲目信任 Server 提供的 Skill | Skill 内容是不可信的输入：校验摘要，标注来源，`allowed-tools` 要逐个批准 |

---

## 13. 下一步

- 发布到 MCP Registry：[把 Server 发布到 MCP Registry](./03-registry.md)
- 实战项目：[内部知识库 MCP Server](../07-practice/01-project-internal-kb.md)（也可以用 Skill 辅助搭建）

## 参考资料

- Build with Agent Skills：https://modelcontextprotocol.io/docs/2026-07-28/develop/build-with-agent-skills
- Skills over MCP 扩展：https://modelcontextprotocol.io/extensions/skills/overview （SEP-2640；ext-skills 仓库 https://github.com/modelcontextprotocol/ext-skills）
- Python SDK Skills（文档站，API 在 main 分支，未随 2.3.0 发布）：https://py.sdk.modelcontextprotocol.io/advanced/skills/
- Agent Skills 仓库：https://github.com/anthropics/claude-plugins-official
- 通用 Agent Skills 标准：https://agentskills.io
- mcp-server-dev Skill 源码：https://github.com/anthropics/claude-plugins-official/tree/main/plugins/mcp-server-dev
