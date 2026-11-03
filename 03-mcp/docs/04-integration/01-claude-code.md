# MCP Integration 01：在 Claude Code 中接入 MCP

> Claude Code 出自 MCP 的发起方 Anthropic，对 MCP 的支持也最完整：本地 stdio Server、远程 Streamable HTTP Server、OAuth 鉴权、跨 Server 的工具命名空间、把 Prompts 当斜杠命令、用 @ 引用 Resource，都不需要额外配置。这篇讲三种添加 Server 的方式和排错思路。

---

## 1. Claude Code 里的 MCP 概念

Claude Code 从传输和作用域两个维度划分 MCP Server。传输有 `http`（远程，推荐；JSON 里的 `type` 也接受别名 `streamable-http`）、`stdio`（本地子进程）、`sse`（已弃用）和 `ws`。作用域有三种：

| 作用域 | 加载范围 | 团队共享 | 存放位置 |
|--------|---------|---------|---------|
| local（默认） | 当前项目 | 否 | `~/.claude.json`（记在该项目路径下） |
| project | 当前项目 | 是，可提交到 git | 项目根目录 `.mcp.json` |
| user | 所有项目 | 否 | `~/.claude.json` |

同名 Server 在多处定义时，按 local > project > user > 插件 > claude.ai connectors 的优先级取最高的那一整条定义，字段不会合并。

> 常见的一个坑：JSON 条目里只写了 `url` 没写 `type`，Claude Code 会把它当成 stdio 服务器并报错。远程 Server 一定要写 `"type": "http"`。

### MCP 客户端运行时：v1 与 v2

Claude Code 内部有两套 MCP 客户端运行时：v1 基于 MCP TypeScript SDK 1.x；v2 基于 TypeScript SDK 2.0，支持 2026-07-28 协议。v2 运行时会探测 HTTP、stdio 和 claude.ai connector 类的 Server 是否支持新版协议，支持就用新版，不支持就按 2025 版协议连接；它通过一条常开的流接收 `list_changed` 通知（对应协议里的 `subscriptions/listen`）。可以用环境变量 `MCP_SDK_GENERATION=v1|v2` 手动选择运行时，用 `MCP_PROTOCOL_NEGOTIATION=auto|legacy` 控制是否探测。所以用 Python SDK 2.x 写的 Server，在 Claude Code 新旧两种运行时下都能工作（`MCPServer` 能同时应答两代协议）。

---

## 2. 方式一：用 `claude mcp` 命令行

Claude Code 自带 `claude mcp` 子命令，这是最快的方式。

```bash
# 加一个本地 stdio Server
claude mcp add hello-mcp -- python /abs/path/to/server.py

# 加一个远程 HTTP Server
claude mcp add --transport http my-saas https://mcp.example.com/mcp

# 加一个带环境变量的 stdio Server（--env 和服务器名之间要隔一个别的选项，如 --transport stdio）
claude mcp add --env GITHUB_PERSONAL_ACCESS_TOKEN=ghp_xxx --transport stdio github -- docker run -i --rm -e GITHUB_PERSONAL_ACCESS_TOKEN ghcr.io/github/github-mcp-server

# 带 Bearer token 的远程 Server
claude mcp add --transport http secure-api https://api.example.com/mcp --header "Authorization: Bearer your-token"

# 列出 / 查看 / 删除；会话里用 /mcp 看状态
claude mcp list
claude mcp get hello-mcp
claude mcp remove hello-mcp
```

- stdio 方式里的 `--`（双横线）把 Claude 自己的选项（`--transport`、`--env`、`--scope`）和启动 Server 的命令分开，`--` 之后的内容原样传给 Server
- 默认是 local 作用域；`--scope project` 会写进 `.mcp.json`（可以提交到 git，团队共享），`--scope user` 则在所有项目里通用：

```bash
claude mcp add --scope project hello-mcp -- python ./server.py
```

- `claude mcp list` 会在每个 Server 旁边显示健康状态：`✔ Connected`、`! Needs authentication`、`✘ Failed to connect`，还有不用连接就能判断的配置状态（`Pending approval`、`Disabled for this project` 等）
- 关闭 stdio Server 时，macOS/Linux 上先发 `SIGINT`，100ms 后发 `SIGTERM`，再过 400ms 发 `SIGKILL`。需要保存状态的 Server 要在这个时间窗口内处理信号
- Claude Code 会在 Server 进程的环境里设置 `CLAUDE_PROJECT_DIR`（项目根目录），Server 要解析项目内的相对路径时可以直接读它

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
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "ghcr.io/github/github-mcp-server"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"
      }
    },
    "remote-sentry": {
      "type": "http",
      "url": "https://mcp.sentry.dev/mcp",
      "headers": {
        "Authorization": "Bearer ${SENTRY_TOKEN}"
      }
    }
  }
}
```

### 3.2 项目级 `.mcp.json`（项目根目录）

格式和上面一样，项目里的所有人共享，建议提交到 git：

```json
{
  "mcpServers": {
    "project-db": {
      "command": "uv",
      "args": ["run", "scripts/db_mcp.py"],
      "env": {
        "DATABASE_URL": "${DATABASE_URL}"
      }
    }
  }
}
```

`.mcp.json` 里的 `command`、`args`、`env`、`url`、`headers` 都支持环境变量展开，语法是 `${VAR}` 或 `${VAR:-默认值}`（不是 `${env:VAR}`）。变量没设置、也没有默认值时，配置照样会加载，但 `claude mcp list` 会给出警告，并保留 `${VAR}` 原文。敏感信息放在环境变量里，不要把 token 直接写进提交到 git 的 `.mcp.json`。

出于安全考虑，Claude Code 发现项目根目录有 `.mcp.json` 时，会在交互会话里先请你批准再启用这些 Server（用 `claude mcp reset-project-choices` 可以重置之前的选择）。团队共享 MCP 配置因此很简单：一个改 `.mcp.json` 的 PR，就能让所有队友拿到同样的工具集，第 7 节有一份推荐写法。

---

## 4. 方式三：远程 MCP + OAuth

接入有 OAuth 保护的远程 MCP（Sentry、Linear 等）：

```bash
claude mcp add --transport http sentry https://mcp.sentry.dev/mcp
```

在会话里运行 `/mcp`，选中这个 Server，在浏览器里完成 OAuth 授权（也可以在 shell 里执行 `claude mcp login <name>`；`claude -p` 非交互模式没有 `/mcp` 面板，要先在交互会话里登录）。token 会被安全存储并自动刷新，选「Clear authentication」可以撤销；Server 返回 401/403 时会被标记为需要认证。v2 运行时会校验授权响应里的 `iss`，并且只把凭据发给 HTTPS（或 localhost）的 token 端点。固定回调端口、预注册 client 等进阶设置见官方文档（对应本手册 [远程 MCP 的 OAuth 2.1 鉴权](../05-production/02-auth-oauth.md)）。

---

## 5. 在对话里怎么用

### 5.1 工具调用（自动）

接好之后正常对话就行：

```
查一下 anthropic/anthropic-sdk-python 仓库最近的 PR
```

Claude 会自动调用 `mcp__github__search_pulls` 之类的工具。界面上会弹出工具调用气泡，显示输入和输出，敏感操作会要求确认。

### 5.2 跨 Server 命名

Claude Code 会自动给工具加上命名空间 `mcp__<server_name>__<tool_name>`，比如 GitHub Server 的 `search` 变成 `mcp__github__search`，DB Server 的 `search` 变成 `mcp__db__search`。名字不同，LLM 就不会搞混。

### 5.3 斜杠命令（Prompts）

如果 Server 暴露了 Prompts，输入 `/` 就能看到 `/servername:promptname (MCP)`；也可以直接输入 `/mcp__servername__promptname` 这种形式（服务器名里不属于 `A-Za-z0-9_-` 的字符会被替换成 `_`）：

```
/mcp__hello-mcp__code-review src/auth.py
```

Claude Code 会展开这个 Prompt 并开始对话，参数按声明顺序用空格分隔。

### 5.4 用 @ 引用 Resource

```
@hello-mcp:greeting/Claude
```

对应 Resource 的内容会被嵌入对话上下文（格式是 `@server:协议://路径`，输入 `@` 后，自动补全里能看到所有 Server 的资源）。

---

## 6. 排错思路

### 6.1 确认 Server 有没有连上

在会话里输入 `/mcp`，会列出 Server 状态、认证情况、开关和重连选项；也可以在 shell 里执行 `claude mcp list` / `claude mcp get <name>`。在 `/mcp` 里还能临时禁用某个 Server，配置不会丢。

### 6.2 看 Server 日志

stdio Server 的 stderr 是排查问题的主要依据。Python 里用标准库 `logging`（默认写 stderr，SDK 已经配好了）。想看 Claude Code 自己的 MCP 连接日志，用 `claude --debug` 启动，或者查看会话的 debug 日志；官方的「错误」页面按报错原文列出了各种连接失败的原因。

### 6.3 用 Inspector 单独调试

怀疑 Claude Code 连不上是 Server 的问题时，直接用 Inspector 跑一遍：

```bash
npx @modelcontextprotocol/inspector python /abs/path/to/server.py
```

如果 Inspector 也连不上，那就是 Server 本身的问题。

### 6.4 重启 Server

改了 Server 代码后，Claude Code 不会自动重新加载。可以在 `/mcp` 面板里选中这个 Server 重新连接（失败的 Server 会自动重连有限次，之后要手动点），也可以先禁用再启用。

### 6.5 常见症状

| 现象 | 原因 |
|------|------|
| "0 tools available" | 多半是 Server 里的 `print()` 污染了 stdout，看日志 |
| `spawn python ENOENT`，或 Server 起来就报 import 错误 | `python` 不在 PATH 里，或指向了没装 `mcp` 的 base 环境；command 写成虚拟环境里 `.venv/bin/python` 的绝对路径（需要装好 `mcp>=2.3`） |
| 远程 401 | OAuth token 过期且自动刷新失败，在 `/mcp` 里点 Re-authenticate |
| 远程一直 Connecting，或报 `has a "url" but no "type"` | 条目要写 `"type": "http"`；检查 Server 是否真的实现了 Streamable HTTP |
| `ModuleNotFoundError: mcp.server.fastmcp` | Server 代码是 v1 写法，而环境里装的是 mcp 2.x；改成 `from mcp.server import MCPServer` |
| `.mcp.json` 没生效 | 重启 Claude Code，并批准项目级 Server（状态为 `Pending approval`） |

---

## 7. 给团队配置 MCP

推荐的 `.mcp.json` 写法：

```json
{
  "mcpServers": {
    "project-db": {
      "command": "python",
      "args": ["scripts/db_mcp.py"],
      "env": {
        "DB_URL": "${DB_URL}",
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

再配一个 `.env.example`，告诉团队需要配置哪些环境变量。新成员 clone 项目、配好 `.env`、启动 Claude Code，就能拿到同一套 MCP 工具。

---

## 8. 给 Claude Code 写 MCP 工具的几点建议

- 工具描述是写给 LLM 看的。不要只写 "creates an issue"，要写成「在指定 repo 创建 GitHub issue。需要 repo 的 owner/name 和 title。这是写操作」。工具很多时，Claude Code 默认启用 MCP tool search：启动时只加载工具名字和 Server 的 `instructions`，用到时才取完整定义。所以 `MCPServer(..., instructions="...")` 里要写清楚什么情况下应该来搜你的工具。
- 用 annotations 标出工具的行为。标了 `ToolAnnotations(read_only_hint=True)` 的工具，Claude Code 会和其它只读调用并行执行；`idempotent_hint=True`（或只读）表示调用两次是安全的，连接在调用中途断开时，Claude Code 会在新连接上重试一次。不标的话，就可能直接报 `connection closed` 而不重试。
- 强制每次都审批：在工具的 `_meta` 里设置 `"anthropic/requiresUserInteraction": true`（Python 写法是 `@mcp.tool(meta={"anthropic/requiresUserInteraction": True})`），Claude Code 每次调用都会弹出权限提示，即使在 `bypassPermissions` 模式下也一样，适合授权、同意类的工具。
- 大结果：MCP 输出超过 10,000 token 时 Claude Code 默认会警告；超过 25,000 token（环境变量 `MAX_MCP_OUTPUT_TOKENS`）或 50,000 字符时，结果会被存成文件，对话里只留路径。个别工具需要更大的阈值，可以设置 `_meta["anthropic/maxResultSizeChars"]`；更推荐的做法是用 `ResourceLink`，让 Claude 按需读取。
- 工具名长度：完整名称 `mcp__<server>__<tool>` 超过 128 个字符的工具不会暴露给模型，Server 名和工具名都别起太长。
- 长任务：主对话里超过 2 分钟的 MCP 调用会自动转成后台任务（在 `/tasks` 里查看或停止）。每次调用还有墙钟超时（Server 配置里以毫秒为单位的 `timeout`，或 `MCP_TOOL_TIMEOUT`）和空闲超时（5 分钟内既没响应也没进度通知就中止，可以用 `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` 修改），所以长时间运行的工具要用 `ctx.report_progress(...)` 保持活跃。如果 Server 实现了 MCP Tasks 扩展（`io.modelcontextprotocol/tasks`），Claude Code（公开 beta，需要 v2.1.304+）还能把它作为服务器端长任务跟踪，退出会话后也能 resume 继续查看。目前官方 Python SDK 没有这个扩展，见[用 Tasks 扩展异步执行长请求](../02-server/07-tasks.md)。
- Elicitation：Server 中途需要信息时，Claude Code 会弹出对话框（表单或 URL 模式），不需要额外配置；想自动应答可以用 `Elicitation` hook。它在 2026 连接和大多数 2025 连接上都声明了 `elicitation: {form: {}, url: {}}`，所以 Python 里的 `Resolve(Elicit(...))` 可以直接用（见[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)）。
- 本地 Server 不要用 print，用标准库 `logging` 写 stderr。
- 大资源不要直接嵌入，用 `ResourceLink` 让 Claude Code 自己决定要不要拉取。
- Prompt 名用 kebab-case：写 `@mcp.prompt(name="code-review")`，而不是默认生成的 `code_review`。

---

## 9. 完整示例：接入本手册的 hello-mcp

假设你已经按 01-basics 写好了 `demos/basics/06_first_server.py`。

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

启动 Claude Code，在对话里输入：

```
帮我用 hello-mcp 算一下 17 加 25
```

预期结果：Claude 调用 `mcp__hello-mcp__add`（第一次调用会请你确认），返回 42。

```
/mcp__hello-mcp__code-review README.md
```

预期结果：触发 Prompt 模板，Claude 进入 code review 模式。

---

## 10. 常见坑

连接失败、401、改代码不生效这几类问题见第 6 节，这里只列配置上的坑。

| 坑 | 做法 |
|----|------|
| 路径必须是绝对路径 | `~` 在 `.mcp.json` 里不会展开，要写 `/Users/...`（也可以用 `${CLAUDE_PROJECT_DIR:-.}` 这类带默认值的展开） |
| 多个项目的 Server 同名 | 用 user 作用域时，各项目的同名 Server 会互相覆盖；要么改名，要么用 project 作用域 |

---

## 11. 下一步

- 其他 Host 的接入：[在 Cursor、VS Code 等客户端中接入 MCP](./02-cursor-vscode.md)
- LangChain 接 MCP：[在 LangChain 中使用 MCP](./03-langchain-mcp.md)
- Pydantic AI 接 MCP：[在 Pydantic AI 中使用 MCP](./04-pydantic-ai-mcp.md)
- 实战，给 Claude Code 写一个自定义 MCP：[为 Claude Code 编写 PR 评审 MCP Server](../07-practice/03-project-claude-code-tool.md)

## 参考资料

- Claude Code MCP 文档：https://code.claude.com/docs/en/mcp
- Claude Code CLI 参考（`claude mcp ...`）：https://code.claude.com/docs/en/cli-reference
