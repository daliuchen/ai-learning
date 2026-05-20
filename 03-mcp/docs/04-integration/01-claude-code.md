# MCP Integration 01：Claude Code 接 MCP

> **一句话**：Claude Code 是 MCP 协议的发起方，对 MCP 的支持最完整——本地 stdio Server、远程 Streamable HTTP Server、OAuth 鉴权、跨 Server 工具命名空间、斜杠命令（Prompts）、Resource @-提及 都开箱即用。本篇讲清四种添加方式 + 排错套路。

---

## 1. Claude Code MCP 概念地图

Claude Code 把 MCP Server 分成两个维度：

| 维度 | 选项 |
|------|------|
| **传输** | stdio（本地子进程）/ Streamable HTTP（远程） |
| **作用域** | user（用户全局，所有项目都用）/ project（当前项目独占，可提交到 git） |

四种排列组合：

| 作用域 | 配置文件 |
|--------|---------|
| user + stdio | `~/.claude.json` |
| user + http | `~/.claude.json` |
| project + stdio | `.mcp.json`（在项目根目录） |
| project + http | `.mcp.json` |

---

## 2. 方式一：用 `claude mcp` CLI（最快）

Claude Code 自带 `claude mcp` 子命令。

```bash
# 加一个本地 stdio Server
claude mcp add hello-mcp -- python /abs/path/to/server.py

# 加一个远程 HTTP Server
claude mcp add --transport http my-saas https://mcp.example.com/mcp

# 加一个带环境变量的 Server
claude mcp add github -- env GITHUB_TOKEN=ghp_xxx npx -y @modelcontextprotocol/server-github

# 列出已配置的 Servers
claude mcp list

# 删除
claude mcp remove hello-mcp
```

加上 `--scope project` 把配置写到 `.mcp.json`（可提交到 git，团队共享）：

```bash
claude mcp add --scope project hello-mcp -- python ./server.py
```

---

## 3. 方式二：手写 JSON 配置

### 3.1 用户级 `~/.claude.json`

```json
{
  "mcpServers": {
    "hello-mcp": {
      "command": "python",
      "args": ["/Users/me/projects/03-mcp/demos/basics/06_first_server.py"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_TOKEN": "ghp_xxx"
      }
    },
    "remote-sentry": {
      "type": "http",
      "url": "https://mcp.sentry.dev/mcp",
      "headers": {
        "Authorization": "Bearer ${env:SENTRY_TOKEN}"
      }
    }
  }
}
```

### 3.2 项目级 `.mcp.json`（项目根目录）

格式同上。被项目内所有用户共享，**强烈推荐提交到 git**：

```json
{
  "mcpServers": {
    "project-db": {
      "command": "uv",
      "args": ["run", "scripts/db_mcp.py"],
      "env": {
        "DATABASE_URL": "${env:DATABASE_URL}"
      }
    }
  }
}
```

`${env:XXX}` 语法让 Server 启动时读宿主进程环境变量——把敏感信息留在 `.env`，**不要**直接把 token 写到 git 仓库里的 .mcp.json。

---

## 4. 方式三：从 `.mcp.json` 模板复制

Claude Code 启动时如果发现项目根有 `.mcp.json` 会询问是否启用。这让"开发环境共享 MCP 配置"非常简单——一份 PR 改 `.mcp.json` 即可让队友拿到同样的工具集。

---

## 5. 方式四：远程 MCP + OAuth

接入 OAuth 受保护的远程 MCP（Sentry / Linear 等）：

```bash
claude mcp add --transport http sentry https://mcp.sentry.dev/mcp
```

第一次用时 Claude Code 会跳浏览器走 OAuth 授权流程，token 缓存到 `~/.claude/`（不进 git）。

---

## 6. 在对话里怎么用

### 6.1 工具调用（自动）

接好后随便对话：

```
查一下 anthropic/anthropic-sdk-python 仓库最近的 PR
```

Claude 自动调 `github__search_pulls` 或类似工具。UI 会弹出工具调用气泡显示输入输出，敏感操作会要确认。

### 6.2 跨 Server 命名

Claude Code 自动给工具加命名空间 `<server_name>__<tool_name>`：

- GitHub Server 的 `search` → `github__search`
- DB Server 的 `search` → `db__search`

LLM 看到不一样的名字不会混淆。

### 6.3 Slash Command（Prompts）

如果 Server 暴露了 Prompts：

```
/hello-mcp:code-review file=src/auth.py
```

Claude Code 把这个 Prompt 展开并启动对话。

### 6.4 Resource @-引用

```
@hello-mcp:greeting/Claude
```

会把对应 Resource 的内容嵌进对话上下文。

---

## 7. 排错套路

### 7.1 看 Server 是不是连上了

```
/mcp list-servers
```

或在 Claude Code UI 设置里看 MCP 状态。

### 7.2 看具体连接日志

Claude Code 把 MCP 日志写到：

```bash
# macOS
~/Library/Logs/Claude/mcp-server-<name>.log

# Linux
~/.local/share/Claude/logs/mcp-server-<name>.log
```

tail -f 那个文件能看到 Server 的 stderr 输出。

### 7.3 用 Inspector 单独调

Claude Code 没起来怀疑是 Server 问题？直接用 Inspector 跑一遍：

```bash
npx @modelcontextprotocol/inspector python /abs/path/to/server.py
```

如果 Inspector 都连不上，肯定是 Server 自身的问题。

### 7.4 重启 Server

修改了 Server 代码后 Claude Code 不会自动 reload：

- 设置 → MCP → 找到 Server → 点 "Restart"
- 或 `/mcp restart <name>`

### 7.5 常见症状

| 现象 | 原因 |
|------|------|
| **"0 tools available"** | 多半 Server `print()` 污染 stdout，看日志 |
| **`spawn python ENOENT`** | Python 不在 PATH，用绝对路径如 `/usr/bin/python` |
| **远程 401** | OAuth token 过期，重新授权 |
| **远程一直 Connecting** | 检查 `MCP-Protocol-Version` 头 / Server 是否真的实现 Streamable HTTP |
| **`.mcp.json` 没生效** | Claude Code 重启 + 在弹窗里点"启用" |

---

## 8. 工程实践：给团队配 MCP

推荐的 `.mcp.json` 模式：

```json
{
  "mcpServers": {
    "project-db": {
      "command": "python",
      "args": ["scripts/db_mcp.py"],
      "env": {
        "DB_URL": "${env:DB_URL}",
        "DB_READONLY": "true"
      }
    },
    "internal-docs": {
      "command": "python",
      "args": ["scripts/docs_mcp.py"]
    }
  }
}
```

配合 `.env.example` 告诉团队哪些环境变量要配。新成员 clone 项目 + 配 `.env` + 启动 Claude Code = 一份 MCP 工具集到位。

---

## 9. 给 Claude Code 写 MCP 工具的几个 tips

- **工具描述写给 LLM 看**：别只写 "creates an issue"，写 "在指定 repo 创建 GitHub issue。需要 repo 的 owner/name 和 title。⚠️ 写操作"。
- **危险操作用 annotations 标**：destructive=true / openWorldHint=true 让 Claude Code 默认弹审批。
- **本地 Server 不要 print**：用 `logging` 写到 stderr 或 `ctx.info`。
- **资源大别 embed**：用 `resource_link` 让 Claude Code 自己决定要不要拉。
- **Prompts 起 kebab-case 名**：`@mcp.prompt(name="code-review")` 而不是默认的 `code_review`，斜杠命令好看。

---

## 10. 完整步骤示例：把本手册的 hello-mcp 接入

假设你跟着 01-basics 写好了 `demos/basics/06_first_server.py`。

```bash
# 1. 用 CLI 一键加（最简）
claude mcp add hello-mcp -- python /Users/me/.../03-mcp/demos/basics/06_first_server.py

# 2. 或者写到 ~/.claude.json
```

```json
{
  "mcpServers": {
    "hello-mcp": {
      "command": "python",
      "args": ["/Users/me/.../03-mcp/demos/basics/06_first_server.py"]
    }
  }
}
```

启动 Claude Code，在对话里：

```
帮我用 hello-mcp 算一下 17 * 19
```

预期：Claude 用 `current_time` 之外不会找到乘法工具，会改用 Python REPL 或自己算。

```
/hello-mcp:code-review file=README.md
```

预期：触发 Prompt 模板，Claude 进入 code review 模式。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **路径必须绝对** | `~` 在 mcp.json 里不会展开，必须 `/Users/...` |
| **不同 Python 环境** | command 写 `python` 可能是 base，要写到具体 venv: `/path/.venv/bin/python` |
| **改了代码不生效** | restart Server |
| **多个项目 Server 同名冲突** | 用 user scope 时各项目同名 Server 互相覆盖，要么改名要么用 project scope |
| **Token 过期** | OAuth Server 每天/每周需要重新授权 |

---

## 12. 下一步

- 📖 其他 Host 接入 → [02-cursor-vscode.md](./02-cursor-vscode.md)
- 📖 LangChain 框架接 MCP → [03-langchain-mcp.md](./03-langchain-mcp.md)
- 📖 Pydantic AI 框架接 MCP → [04-pydantic-ai-mcp.md](./04-pydantic-ai-mcp.md)
- 🛠️ 实战：给 Claude Code 写自定义 MCP → 07-practice/03-project-claude-code-tool

## 参考资料

- Claude Code MCP 文档：https://docs.claude.com/en/docs/claude-code/mcp
- claude mcp CLI：https://docs.claude.com/en/docs/claude-code/cli-reference#claude-mcp
