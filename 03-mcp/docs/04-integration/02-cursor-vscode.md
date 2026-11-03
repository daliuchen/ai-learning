# MCP Integration 02：在 Cursor、VS Code 等客户端中接入 MCP

> 除了 Claude Code，主流的 AI IDE 和编辑器都已经原生支持 MCP，只是配置方式各不相同。这篇整理 Cursor、VS Code Copilot Chat、Continue、Cline、Zed、JetBrains AI Assistant 和 ChatGPT Desktop 的配置方法，以及它们之间的差异。

---

## 1. Cursor

Cursor 的 MCP 配置和 Claude Code 很像，也用 `mcpServers` 键。

### 1.1 配置文件

用户级：`~/.cursor/mcp.json`　项目级：`<project>/.cursor/mcp.json`（也可以在设置的 Customize 页面里图形化安装）

```json
{
  "mcpServers": {
    "my-tool": {
      "command": "python",
      "args": ["/abs/path/to/server.py"],
      "env": {"API_KEY": "value"}
    },
    "remote": {
      "url": "https://mcp.example.com/mcp",
      "headers": {"API_KEY": "value"}
    },
    "oauth-server": {
      "url": "https://api.example.com/mcp",
      "auth": {"CLIENT_ID": "your-client-id", "CLIENT_SECRET": "optional", "scopes": ["read", "write"]}
    }
  }
}
```

- 本地 stdio 和远程 HTTP / SSE 都支持，远程 Server 支持 OAuth
- 静态 OAuth：给 `url` 类型的条目加一个 `auth` 对象（`CLIENT_ID` 必填，`CLIENT_SECRET`、`scopes` 可选）。适用于 provider 提供固定 Client ID、要求回调 URL 白名单（Figma、Linear 之类），或者不支持动态客户端注册的情况。Cursor 使用固定的回调地址：桌面端是 `http://localhost:8787/callback`，Web/云端是 `https://www.cursor.com/agents/mcp/oauth/callback`
- 官方声明支持的能力有 Tools、Prompts、Resources、Roots、Elicitation，以及 MCP Apps 扩展（工具可以返回交互式 UI，见[用 MCP Apps 在客户端中嵌入交互式 UI](../06-advanced/01-mcp-apps.md)）

### 1.2 UI 入口

在 Customize 页面可以从 Cursor Marketplace 一键安装（用 OAuth 登录），查看已配置的 Server，启用或停用，查看日志；团队管理员还可以通过团队 marketplace 分发 Server。

---

## 2. VS Code（GitHub Copilot Agent）

### 2.1 两种配置格式，写法不一样

| | VS Code 格式 | 可移植（Copilot）格式 |
|---|---|---|
| 位置 | 工作区 `.vscode/mcp.json` 或用户 profile | 工作区根目录 `.mcp.json`；或 `~/.copilot/mcp-config.json` |
| 顶层键 | `servers` | `mcpServers` |
| 环境变量引用 | `${env:NAME}` | `${NAME}` 或 `${NAME:-default}` |
| 传输 | stdio / HTTP / SSE | stdio / HTTP（用户级还支持 SSE） |
| 独有能力 | `inputs` 输入变量、`envFile`、`cwd`、`dev` 开发模式、沙盒 | 无 |

VS Code 格式示例（`.vscode/mcp.json`）：

```json
{
  "servers": {
    "my-tool": {
      "type": "stdio",
      "command": "python",
      "args": ["${workspaceFolder}/server.py"],
      "env": {"API_KEY": "${input:api-key}"}
    },
    "remote": {"type": "http", "url": "https://mcp.example.com/mcp"}
  },
  "inputs": [{"type": "promptString", "id": "api-key", "description": "API Key", "password": true}]
}
```

> 早先的设置项 `github.copilot.chat.mcp.servers` 现在已经不用了。

### 2.2 添加与管理

- 命令面板：运行 `MCP: Add Server`，选 HTTP 或 Command (stdio)，填 URL 或启动命令，再选作用域
- 在 Chat 里：Customizations → MCP Servers → Add Server
- 验证：配置写好了不代表 Agent 就能用。在会话里发一个需要用到这个工具的请求，看实际有没有发生工具调用
- 信任：本地 Server 能在你的机器上执行代码，启动前会有信任提示，先看清发布者和启动命令

### 2.3 沙盒（macOS / Linux）

stdio Server 可以在 `servers.<id>` 里设置 `"sandboxEnabled": true`，再用 `sandbox` 限制文件系统和网络访问（`filesystem.allowWrite` / `denyRead` / `denyWrite`，`network.allowedDomains` / `deniedDomains`）。开了沙盒之后，这个 Server 的工具确认会自动通过，因为它跑在受控环境里。目前客户端里内置的、能给本地 MCP Server 加限制的手段不多，这是其中之一，建议用上。

---

## 3. Continue

Continue 是一个开源的 IDE 内嵌 AI 助手，支持 VS Code 和 JetBrains。MCP 只在 Agent 模式下可用，配置已经改成了 YAML：在工作区建一个 `.continue/mcpServers/` 目录，每个 Server 一个文件：

```yaml
# .continue/mcpServers/playwright-mcp.yaml
name: Playwright mcpServer
version: 0.0.1
schema: v1
mcpServers:
  - name: Browser search
    command: npx
    args:
      - "@playwright/mcp@latest"
```

（在 `config.json` 里写 `mcpServers` 是已经弃用的旧配置方式。）

## 4. Cline

Cline（原名 Claude Dev）是 VS Code 里的 Agent 插件，主打自主执行的工作流。

### 4.1 配置

在 Cline 面板的 MCP Servers 页添加 Server，配置保存在它自己的 `cline_mcp_settings.json` 里（键同样是 `mcpServers`）。具体路径和字段以 Cline 官方文档为准。

### 4.2 特点

Cline 比其他客户端更依赖 MCP，它的整个工具生态都建立在 MCP 上；它还内置了 Cline Marketplace，可以一键安装常用的 Server。

---

## 5. Zed

Zed 把 MCP Server 叫作 "Context Servers"。它支持 Tools 和 Prompts（Resources、Sampling、Elicitation 等还不支持），收到 `notifications/tools/list_changed` 时会自动重新加载工具列表。

### 5.1 配置

在 Settings → AI → MCP Servers → Add Server 里添加（本地、远程或从扩展安装），也可以直接编辑 settings 文件（`zed: open settings file`）：

```json
{
  "context_servers": {
    "local-mcp-server": {"command": "some-command", "args": ["arg-1", "arg-2"], "env": {}},
    "remote-mcp-server": {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer <token>"}},
    "remote-mcp-server-with-oauth": {"url": "https://mcp.example.com/mcp"}
  }
}
```

（早先 `"command": {"path": ..., "args": ...}` 这种嵌套写法已经过时；远程 Server 缺少凭据时，Zed 会走 OAuth。）

## 6. JetBrains AI Assistant

IntelliJ、PyCharm、WebStorm 等 JetBrains 系 IDE 在 Settings 的 AI 相关页面里提供 MCP Servers 管理（菜单位置随版本变化，以官方文档为准），通过 UI 添加 Server，不直接编辑 JSON。

---

## 7. ChatGPT / OpenAI API

OpenAI 在 2025 年加入了 MCP 支持。ChatGPT 里叫 Connectors（自定义连接器），只能添加远程 HTTP MCP，不支持本地 stdio。具体入口和可用的套餐会随版本变化，以 OpenAI 官方文档为准。

OpenAI API（Responses API）通过 `tools` 参数传入 MCP 端点，由 OpenAI 的服务器作为 MCP Client 去连接你的远程 Server，所以你的 Server 必须能从公网访问：

```python
from openai import OpenAI

client = OpenAI()
resp = client.responses.create(
    model="gpt-6.1-sol",
    input="...",
    tools=[{"type": "mcp", "server_url": "https://mcp.example.com/mcp", "server_label": "my-server",
            "headers": {"Authorization": "Bearer ..."}}],
)
```

（运行这段需要 OpenAI 的付费 API key。）

---

## 8. 跨客户端兼容的几个习惯

写 MCP Server 时养成下面几个习惯，在各个客户端上都能少出问题：

1. Server 名保持稳定，不要随便改。配置里的 key 相当于用户找到它的地址
2. 本地工具优先用 stdio，远程场景才用 HTTP，stdio 的客户端兼容性最好
3. 工具描述可以中英双语，英文方便 LLM 理解，中文用户读起来也方便。工具很多的客户端会用 tool search 延迟加载工具定义，所以 Server 的 `instructions` 要写清楚什么时候该用它
4. 至少在 Claude Code 和 Cursor 两个客户端上各跑一遍
5. README 里给出四种客户端的配置示例，用户复制就能用

---

## 9. 客户端能力对比

各客户端的功能支持变化很快，不要照抄一张静态表格。下面三个来源更可靠：

- 官方 Clients 列表（modelcontextprotocol.io/clients）：列出每个 Host 支持哪些原语
- 官方 Extension Support Matrix（modelcontextprotocol.io/extensions/client-matrix）：由社区维护，列出哪些客户端支持 MCP Apps、OAuth Client Credentials、Enterprise-Managed Auth、Skills、Tasks 这几个官方扩展
- 各客户端自己的文档

下面是 2026 年 11 月各客户端文档里能查到的部分：

| 能力 | Claude Code | Cursor | VS Code Copilot | Zed |
|------|------------|--------|-----------------|-----|
| stdio / HTTP | 支持 / 支持 | 支持 / 支持 | 支持 / 支持 | 支持 / 支持 |
| Tools | 支持 | 支持 | 支持 | 支持 |
| Prompts | 支持（`/mcp__server__prompt`） | 支持 | 支持（`/mcp.server.prompt`） | 支持 |
| Resources | 支持（`@server:uri`） | 支持 | 支持（Add Context → MCP Resources） | 不支持 |
| Roots | 支持 | 支持 | — | 不支持 |
| Elicitation | 支持（表单 + URL） | 支持 | 支持 | 不支持 |
| MCP Apps | 见[用 MCP Apps 在客户端中嵌入交互式 UI](../06-advanced/01-mcp-apps.md) | 支持 | — | — |
| OAuth | 支持 | 支持（含静态 client） | 支持 | 支持 |
| 本地 Server 沙盒 | — | — | 支持（macOS/Linux） | — |

> 表中「—」表示没有查到明确说明，不代表不支持。测试 Server 时，Claude Code 的支持最完整，适合作为主要的验证对象；Sampling 支持的客户端很少（VS Code 是少数之一），而且已经弃用（见[Sampling 让 Server 借用 Host 的 LLM（已弃用）](../03-client/03-sampling.md)）。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| 同一个 Server 在 Claude Code 能用，在 Cursor 不行 | 查看该客户端的日志；可能是协议版本头或命名空间策略不同 |
| 配置文件路径或顶层键写错 | 各客户端的配置路径不同，顶层键也不同：`mcpServers`（Claude Code、Cursor、可移植格式）、`servers`（VS Code）、`context_servers`（Zed）；用 IDE 内置的 UI 添加最稳妥 |
| 环境变量没生效 | 语法因客户端而异：Claude Code 和可移植的 `.mcp.json` 用 `${VAR}`、`${VAR:-默认}`，VS Code 的 `.vscode/mcp.json` 用 `${env:VAR}`；最稳妥的办法是在外部启动脚本里 export |
| 本地 npm 包路径 | 用 `npx -y <package>`，不要写绝对路径，跨操作系统更稳 |
| OAuth 时浏览器没有打开 | 有些客户端不会自动打开浏览器，需要手动复制 URL |

---

## 11. 下一步

- LangChain 接 MCP（它是框架，不是 IDE 客户端）：[在 LangChain 中使用 MCP](./03-langchain-mcp.md)
- Pydantic AI 接 MCP：[在 Pydantic AI 中使用 MCP](./04-pydantic-ai-mcp.md)
- 与 Function Calling、OpenAPI 的对比：[MCP 与 Function Calling、OpenAPI、LangChain Tools 的对比](./05-comparison.md)

## 参考资料

- 官方 Clients 列表：https://modelcontextprotocol.io/clients
- 扩展支持矩阵：https://modelcontextprotocol.io/extensions/client-matrix
- Cursor MCP 文档：https://cursor.com/docs/context/mcp
- VS Code MCP：https://code.visualstudio.com/docs/agent-customization/mcp-servers 、配置参考 https://code.visualstudio.com/docs/agents/reference/mcp-configuration
- Continue MCP：https://docs.continue.dev/customize/deep-dives/mcp
- Zed MCP：https://zed.dev/docs/ai/mcp
