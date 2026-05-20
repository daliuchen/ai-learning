# MCP Integration 02：Cursor / VS Code / 其他客户端

> **一句话**：除了 Claude Code，主流 AI IDE / 编辑器都已经原生支持 MCP。本篇梳理 Cursor、VS Code Copilot Chat、Continue、Cline、Zed、JetBrains AI Assistant、ChatGPT Desktop 的配置方式和差异。

---

## 1. Cursor

Cursor 的 MCP 配置和 Claude Code 风格几乎一致。

### 1.1 配置文件

**用户级**：`~/.cursor/mcp.json`
**项目级**：`<project>/.cursor/mcp.json`

格式：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/safe-dir"]
    },
    "my-tool": {
      "command": "python",
      "args": ["/abs/path/to/server.py"]
    },
    "remote": {
      "url": "https://mcp.example.com/mcp",
      "headers": {"Authorization": "Bearer ${env:TOKEN}"}
    }
  }
}
```

### 1.2 UI 入口

- Settings → Features → Model Context Protocol
- 可视化列出已配置 Server、enable/disable、查看日志

### 1.3 跟 Claude Code 的差异

- Cursor 没有"全 MCP 工具一致命名空间"约定（具体策略随版本变）
- Slash command 暴露 Prompts 的方式略不同
- Resource @-引用机制目前比 Claude Code 弱

---

## 2. VS Code Copilot Chat

VS Code 2024 中后期内置了 GitHub Copilot Chat 的 MCP 支持。

### 2.1 配置位置

VS Code Settings (JSON)：

```json
{
  "github.copilot.chat.mcp.servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "${workspaceFolder}"]
    }
  }
}
```

### 2.2 UI

- 命令面板：`MCP: Configure servers`
- Copilot Chat 侧栏点齿轮图标看 MCP 状态

### 2.3 工具调用

聊天里 Copilot 自动决定调用 MCP 工具。VS Code 在工具执行前弹审批气泡（写操作必弹）。

---

## 3. Continue

Continue 是开源的 IDE 内嵌 AI 助手，支持 VS Code / JetBrains。

### 3.1 配置

`~/.continue/config.json`：

```json
{
  "models": [...],
  "mcpServers": [
    {
      "name": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "env": {}
    }
  ]
}
```

### 3.2 特色

- 同时支持 MCP Tools + LangChain 工具
- 工具调用前展示给用户

---

## 4. Cline

Cline（前 Claude Dev）是 VS Code 里的 Agent 插件，主打"自主"工作流。

### 4.1 配置

VS Code 设置：

```json
{
  "cline.mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "${env:GITHUB_TOKEN}"}
    }
  }
}
```

### 4.2 特点

- Cline 对 MCP 的依赖比其他客户端深——它的整个工具生态都基于 MCP
- 内置 Cline Marketplace 一键安装常用 Server

---

## 5. Zed

Zed 是 Rust 写的快速编辑器，原生支持 MCP（叫 "Context Servers"）。

### 5.1 配置

`~/.config/zed/settings.json`：

```json
{
  "context_servers": {
    "github": {
      "command": {
        "path": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "..."}
      }
    }
  }
}
```

### 5.2 UI

在 Zed AI 面板里挑工具用，自动展示 MCP Server 提供的工具。

---

## 6. JetBrains AI Assistant

IntelliJ / PyCharm / WebStorm 等 JetBrains 系 IDE。

### 6.1 配置位置

Settings → Tools → AI Assistant → MCP Servers

### 6.2 特点

- 通过 UI 添加 Server，不直接编辑 JSON
- 支持自动从 Claude Code / Cursor 导入配置
- 把 MCP 工具集成到 AI Assistant 的 Chat 和 Inline 操作

---

## 7. ChatGPT Desktop / API

OpenAI 在 2025 年加入 MCP 支持（叫 "Custom Connectors"）。

### 7.1 ChatGPT Desktop

Settings → Beta features → Custom Connectors → Add new

只能加远程 HTTP MCP，**不支持本地 stdio**（截至 2025-Q4）。配置非常类似：

```json
{
  "name": "My Server",
  "url": "https://mcp.example.com/mcp",
  "authentication": "oauth"
}
```

### 7.2 OpenAI API

通过 `tools` 参数传 MCP 端点：

```python
from openai import OpenAI

client = OpenAI()
resp = client.responses.create(
    model="gpt-4o",
    input=[{"role": "user", "content": "..."}],
    tools=[
        {
            "type": "mcp",
            "server_url": "https://mcp.example.com/mcp",
            "server_label": "my-server",
            "headers": {"Authorization": "Bearer ..."}
        }
    ],
)
```

OpenAI Server 自己当 MCP Client 连远端，把工具透传给模型。

---

## 8. 跨客户端兼容性建议

写 MCP Server 时几条小习惯让你在所有客户端都顺：

1. **Server 名稳定**：不要轻易改名，配置里的 key 是用户的"地址"
2. **stdio 优先**：本地工具用 stdio，远程才用 HTTP——stdio 客户端兼容性最好
3. **工具描述用英文 + 中文双语**：英语 LLM 觉得更亲切，中文用户读起来也行
4. **测试矩阵**：至少在 Claude Code + Cursor 两个客户端跑一遍
5. **README 里写四种配置示例**：让用户复制即用

---

## 9. 客户端能力对比表

| 能力 | Claude Code | Cursor | VS Code | Continue | Cline | Zed | JetBrains | ChatGPT |
|------|------------|--------|---------|----------|-------|-----|-----------|---------|
| stdio | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| HTTP | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ | ✅ | ✅ |
| Tools | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Resources | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ |
| Prompts | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ |
| Sampling | ✅ | ⚠️ | ⚠️ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Roots | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ❌ |
| Elicitation | ✅ | ⚠️ | ⚠️ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 跨 Server namespace | ✅ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ |
| OAuth 2.1 | ✅ | ✅ | ✅ | ❌ | ❌ | ⚠️ | ✅ | ✅ |

> ⚠️ 表示部分支持或随版本变化。Claude Code 是 MCP 协议规范最完整的实现，写 Server 测试时以它为准。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| **同一份 Server 在 Claude Code 跑通，Cursor 跑不通** | 检查具体客户端日志；可能是协议版本头 / 命名空间策略不同 |
| **JSON 配置路径写错** | 各客户端配置文件路径不同；用 IDE 内置 UI 添加最稳 |
| **环境变量没生效** | 不同客户端处理 `${env:...}` 方式不同；最稳是直接写值或外部启动脚本 |
| **本地 npm 包路径** | 用 `npx -y <package>` 而不是绝对路径，跨 OS 更稳 |
| **OAuth 浏览器跳不开** | 部分客户端不会自动跳浏览器，要手动复制 URL |

---

## 11. 下一步

- 📖 LangChain 接 MCP（不是 IDE 客户端，是框架） → [03-langchain-mcp.md](./03-langchain-mcp.md)
- 📖 Pydantic AI 接 MCP → [04-pydantic-ai-mcp.md](./04-pydantic-ai-mcp.md)
- 📖 vs Function Calling / OpenAPI → [05-comparison.md](./05-comparison.md)

## 参考资料

- 官方 Clients 列表：https://modelcontextprotocol.io/clients
- Cursor MCP 文档：https://docs.cursor.com/context/mcp
- VS Code Copilot MCP：https://code.visualstudio.com/docs/copilot/chat/mcp-servers
- Zed Context Servers：https://zed.dev/docs/assistant/context-servers
