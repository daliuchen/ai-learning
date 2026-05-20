# MCP 01：MCP 是什么 / 为什么需要它

> **一句话**：MCP（Model Context Protocol，模型上下文协议）是 Anthropic 在 2024 年底牵头、由 OpenAI / Google / Microsoft / JetBrains / Cursor 等全行业共建的**开放协议**，用一句官方比喻就是「**AI 应用的 USB-C**」——一个统一的、标准化的"AI 应用 ↔ 外部数据/工具"连接方式。

---

## 1. 从一个真实痛点说起

假设你是 Anthropic 工程师，在 Claude Desktop 里要支持下面这堆场景：

- 读用户本地文件夹
- 查 PostgreSQL / MySQL / SQLite
- 拉 GitHub / GitLab Issue
- 调 Slack 发消息
- 查 Linear / Jira 工单
- 控制浏览器抓页面
- 调 Figma 拿设计稿
- ……

按以前的做法，每接一个就是一段 Claude Desktop 内嵌代码：写 SDK 调用、捏 JSON Schema、做权限弹窗、处理错误。这种"应用 × 服务"的耦合是 **M×N** 复杂度——M 个 AI 应用对 N 个服务，要写 M×N 个集成。

更糟的是：**别的 AI 应用做不了这套**。Cursor 想接你写好的 PostgreSQL 集成？没门，得自己再写一遍。

MCP 要解决的就是这个：**把 M×N 拆成 M+N**。

- 服务方（数据库厂商、文档系统、Figma、Slack 等）只写一个 MCP **Server**
- AI 应用方（Claude Code / Cursor / VS Code / ChatGPT）只实现一个 MCP **Client**
- 两边通过标准协议讲话

```
没 MCP 之前：M × N 个集成

  Claude ─┬─ Postgres
          ├─ GitHub
          ├─ Slack
          └─ ...

  Cursor ─┬─ Postgres   ← 重新写
          ├─ GitHub     ← 重新写
          └─ ...

有了 MCP：M + N

  Claude ──┐
  Cursor ──┼── [MCP] ──┬── Postgres MCP Server
  VS Code ─┤           ├── GitHub MCP Server
  ChatGPT ─┘           └── Slack MCP Server
```

这就是 MCP 的核心价值：**一次写，到处接**。

---

## 2. MCP 是什么（一句话定义 + 三个关键词）

**MCP 是一个基于 JSON-RPC 2.0、定义了「AI 应用」和「外部能力提供方」之间如何交换上下文与调用工具的开放协议。**

三个关键词：

1. **开放协议**（不是某家产品）：spec 在 GitHub 上开源，治理走 SEP（类似 PEP / RFC）流程
2. **上下文交换**（不只是工具调用）：除了"调函数"，还能传文件、传 prompt 模板、订阅资源变更、反向请求 LLM 采样
3. **AI 应用 ↔ 外部世界**（不是 LLM ↔ 工具）：MCP 定义的是 **AI 应用层**的协议，不是模型层。模型怎么用上下文是 AI 应用的事，MCP 不管

> ⚠️ 这一条是很多人理解 MCP 时的最大误区：MCP **不是** Function Calling 的替代品。Function Calling 是「模型 ↔ AI 应用」之间的协议（OpenAI / Anthropic API 层面），MCP 是「AI 应用 ↔ 外部服务」之间的协议。两者是层级关系，不是竞争关系。

---

## 3. MCP 的「USB-C 比喻」到底好在哪

USB-C 这个比喻不是 marketing 话术，它精确对应了 MCP 的几个设计决策：

| USB-C 特性 | 对应 MCP 设计 |
|------------|--------------|
| 统一物理接口 | 统一 JSON-RPC 2.0 消息格式 |
| 双向数据传输 | Client / Server 都能主动发请求（不是单纯 RPC） |
| 能力协商（手机 vs 显示器 vs 充电器） | `initialize` 握手时双方声明 capabilities |
| 热插拔 / 设备发现 | `tools/list`、`resources/list`、`*/list_changed` 通知 |
| 多协议复用同一接口（USB / DP / PD） | 同一份 JSON-RPC 消息可走 stdio / Streamable HTTP |

USB-C 让一根线插所有设备；MCP 让一个 Server 接所有 AI 应用。

---

## 4. MCP vs 你已经知道的几个东西

### vs Function Calling（OpenAI / Anthropic Tool Use）

```
[模型] ←── Function Calling ──→ [AI 应用]   ← 模型决定调哪个函数
[AI 应用] ←─────── MCP ────────→ [外部服务]  ← 外部服务由谁提供
```

- **Function Calling**：模型 API 的能力，让模型能"申请调用某个函数"。OpenAI 叫 `tools`，Anthropic 叫 `tool_use`，Google 叫 `function_call`，互相不兼容。
- **MCP**：AI 应用拿到这些工具的标准化来源。Claude Code 通过 MCP 拉到 GitHub Server 提供的 `create_issue` 工具，再把这个工具用 Anthropic Tool Use 的格式塞给模型；ChatGPT 拉到同一个 GitHub Server，则用 OpenAI 的 tools 格式塞给模型。

**关系**：MCP 在 Function Calling 之上一层，是工具的**来源**与**分发协议**。

### vs 插件协议（ChatGPT Plugin / Claude Connector）

| 维度 | ChatGPT Plugin（已弃用） | Claude Connector | MCP |
|------|--------------------------|------------------|-----|
| 协议规范 | OpenAPI + 自定义 manifest | Anthropic 自家 | 开放标准 |
| 跨平台 | 仅 ChatGPT | 仅 Anthropic 产品 | 全行业 |
| 双向通信 | ❌ 单向 | ⚠️ 有限 | ✅ Client/Server 互发请求 |
| 本地能力 | ❌ 只能远程 HTTP | ❌ 只能远程 HTTP | ✅ stdio + 远程 HTTP |
| Resource / Prompt 抽象 | ❌ 只有 action | ❌ 只有 tool | ✅ 三大原语 |

ChatGPT Plugin 2024 年已下线；Claude Connector 也已被 MCP 取代。**MCP 是这一代「AI 应用接外部能力」事实标准**。

### vs LangChain Tools / LlamaIndex Tools

LangChain / LlamaIndex 里也有 Tool 抽象，但那是**框架内**的概念——只能在该框架的 Agent 里用。MCP 是**框架外**的协议——任何语言、任何框架都能实现 Client 和 Server。

**关系**：LangChain / Pydantic AI 等框架内置了"把 MCP Server 转成框架内 Tool"的适配器（参见 04-integration/03-langchain-mcp 和 04-pydantic-ai-mcp），让你写一个 MCP Server 同时被 LangChain Agent、Pydantic AI Agent、Claude Code 都消费。

### vs OpenAPI / gRPC

- **OpenAPI**：REST API 的描述格式。AI 应用要消费它需要中间层做"OpenAPI → Tool"转换。
- **gRPC**：RPC 协议，强类型但需要 .proto 文件，对 AI 不友好。
- **MCP**：天然为 AI 设计——工具描述里直接带 LLM 友好的自然语言描述、JSON Schema、用例提示，且支持 Resource / Prompt 等 AI 专用概念。

---

## 5. MCP 的核心抽象一图流

```
                ┌─────────────────────────────────────┐
                │       MCP Host (AI 应用)            │
                │   Claude Code / Cursor / VS Code    │
                │                                     │
                │  ┌──────────┐  ┌──────────┐         │
                │  │ Client 1 │  │ Client 2 │   ...   │
                │  └─────┬────┘  └─────┬────┘         │
                └────────┼─────────────┼──────────────┘
                         │             │
              JSON-RPC 2.0 over stdio / Streamable HTTP
                         │             │
                ┌────────▼────┐  ┌────▼─────────┐
                │  Server A   │  │   Server B   │
                │ (Postgres)  │  │   (GitHub)   │
                │             │  │              │
                │ Tools       │  │ Tools        │
                │ Resources   │  │ Resources    │
                │ Prompts     │  │ Prompts      │
                └─────────────┘  └──────────────┘
```

三个核心角色：

- **Host**：AI 应用本身（Claude Code、Cursor 等），负责 UI、对话、和模型交互
- **Client**：Host 内的一个组件，**一个 Server 对应一个 Client 实例**，负责协议层通信
- **Server**：提供能力的程序（本地进程 or 远程服务），向 Client 暴露 Tools / Resources / Prompts

Server 通过三个**原语（primitives）**对外暴露能力：

| 原语 | 谁用 | 类比 |
|------|------|------|
| **Tools** | 模型主动调（model-controlled） | 函数 / 写操作 |
| **Resources** | 应用决定何时加载（application-controlled） | 文件 / 只读数据 |
| **Prompts** | 用户显式调用（user-controlled） | 模板 / Slash 命令 |

反过来，Client 也可以给 Server 提供能力：

| 客户端能力 | 用途 |
|-----------|------|
| **Sampling** | Server 反向请求 Host 的 LLM 做一次 completion |
| **Roots** | Client 告诉 Server "你能操作哪些目录" |
| **Elicitation** | Server 在执行过程中向用户索取额外输入 |

这六个原语 + 通知机制 + 生命周期 = MCP 的全部协议表面积。03-primitives.md 和 04-protocol-lifecycle.md 会一个个展开。

---

## 6. 一段代码体验一下「MCP 风味」

下面用官方 Python SDK 写一个 hello world MCP Server，10 行代码暴露一个 `add` 工具：

```python
# server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hello-mcp")

@mcp.tool()
def add(a: int, b: int) -> int:
    """两个整数相加"""
    return a + b

if __name__ == "__main__":
    mcp.run()  # 默认走 stdio
```

启动它：

```bash
python server.py
```

用 MCP 官方 Inspector 可视化调试：

```bash
npx @modelcontextprotocol/inspector python server.py
# 打开浏览器 http://localhost:6274
```

在 Claude Code 里把它接进来（详细见 04-integration/01-claude-code）：

```json
// ~/.claude/mcp.json
{
  "mcpServers": {
    "hello-mcp": {
      "command": "python",
      "args": ["/abs/path/to/server.py"]
    }
  }
}
```

现在 Claude Code 里所有对话都可以用这个 `add` 工具——但你**没有**写任何 Claude 专属代码、没有动 Anthropic SDK、没有处理 tool_use/tool_result。这就是 MCP 的价值。

---

## 7. MCP 在 2025-2026 的生态现状

### 主流 Client（消费端）
- **Claude Code / Claude Desktop**：MCP 发起方，原生支持
- **VS Code**（Copilot Chat）：内置 MCP Server 管理
- **Cursor**：原生支持
- **Continue / Cline / Zed / JetBrains AI Assistant**：均已支持
- **ChatGPT Desktop / API**：2025 年加入支持（Custom Connectors）

### 官方 Reference Server（生产端）
仓库 [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) 提供了一批参考实现：

- `filesystem` — 本地文件系统
- `git` / `github` / `gitlab` — 代码托管
- `postgres` / `sqlite` — 数据库
- `slack` / `google-drive` / `gmail` — SaaS
- `puppeteer` / `playwright` — 浏览器自动化
- `memory` — 知识图谱式记忆
- ……

### 商业 Server（厂商出品）
- Sentry / Linear / Notion / Figma / Stripe / Vercel / Supabase 等已上线官方 MCP Server
- Anthropic 维护的 [Registry](https://registry.modelcontextprotocol.io) 是官方发布中心

### Python SDK 现状
- 仓库：`modelcontextprotocol/python-sdk`（PyPI 包名 `mcp`）
- 高层 API：`FastMCP`（类似 FastAPI 的装饰器风格）
- 低层 API：`Server` / `ClientSession`（直接操作协议消息）
- 1.0 后 API 稳定，本手册基于 `mcp>=1.10`

---

## 8. 什么时候**不该**用 MCP

MCP 是个标准化协议，标准化的成本是"约束"。下面情况你可能不需要 MCP：

| 场景 | 更合适的方案 |
|------|--------------|
| 只在一个 Agent 框架内部用工具，不打算分发给别的 AI 应用 | 直接用框架原生 Tool（LangChain `@tool`、Pydantic AI `@agent.tool`） |
| 工具是纯 LLM 内部逻辑（一段确定的 prompt），没有外部 IO | Prompt 模板 + 字符串拼接就够了 |
| 服务已有成熟的 OpenAPI，AI 客户端只是众多消费者之一 | 让 AI 应用通过 OpenAPI-to-MCP 转换层接，不必专门写 MCP Server |
| 极致性能场景（微秒级延迟） | MCP 是 JSON-RPC，序列化开销不可忽略 |

但**只要你写的能力可能被多个 AI 应用消费，或者你想让 Claude Code / Cursor 用上**，MCP 几乎是默认答案。

---

## 9. 一句话总结

| 你看待 MCP 的角度 | 一句话理解 |
|-------------------|-----------|
| **协议工程师** | JSON-RPC 2.0 + 能力协商 + 三大原语 + 双向请求 |
| **AI 应用作者** | 让我的 Agent 自动获得几百个工具生态 |
| **后端工程师** | 把内部系统包成 Server，让 AI 时代的客户端都能用 |
| **产品经理** | "USB-C 时刻"——AI 工具市场的标准化分发渠道 |
| **CIO / 架构师** | 企业内部"AI 能力中台"的事实标准协议 |

---

## 10. 常见坑

| 坑 | 描述 | 怎么避免 |
|----|------|---------|
| **混淆 MCP 与 Function Calling** | 以为 MCP 是给模型用的 | MCP 是给 AI 应用用的，模型还是用 OpenAI/Anthropic tool 格式 |
| **把所有能力都写成 Tool** | Resource / Prompt 闲置 | 只读上下文用 Resource，模板用 Prompt（详见 03-primitives） |
| **以为 Server 就是远程服务** | Server 必须部署到 HTTP | 90% 的官方 Server 走 stdio，本地子进程 |
| **没注意能力协商** | Server 单方面发某个通知，Client 没启用 | 必须在 `initialize` 阶段双方都声明对应 capability |
| **直接读取 SSE 流当 REST 用** | Streamable HTTP 不是普通 REST | 看 03-client/02-transports |

---

## 11. 下一步

- 📖 想搞清楚 Host/Client/Server 三角与传输层 → [02-architecture.md](./02-architecture.md)
- 📖 想知道 Tools/Resources/Prompts 各自什么时候用 → [03-primitives.md](./03-primitives.md)
- 🛠️ 想直接跑通 Hello World → [05-installation.md](./05-installation.md) + [06-first-server.md](./06-first-server.md)

## 参考资料

- 官方 What is MCP：https://modelcontextprotocol.io/docs/getting-started/intro
- 架构总览：https://modelcontextprotocol.io/docs/learn/architecture
- Spec 2025-11-25：https://modelcontextprotocol.io/specification/2025-11-25
- Python SDK：https://github.com/modelcontextprotocol/python-sdk
- Reference Servers：https://github.com/modelcontextprotocol/servers
