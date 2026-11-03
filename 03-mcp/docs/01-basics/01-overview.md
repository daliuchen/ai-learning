# MCP 01：MCP 是什么，解决什么问题

> MCP（Model Context Protocol，模型上下文协议）是 Anthropic 在 2024 年底牵头推出的开放协议，现在 OpenAI、Google、Microsoft、JetBrains、Cursor 等都参与共建。它规定了 AI 应用怎样以统一的方式连接外部数据和工具，官方的比喻是「AI 应用的 USB-C」。这篇先讲它要解决什么问题、和 Function Calling 等已有方案是什么关系，再用十行代码感受一下。

---

## 1. 从一个实际问题说起

假设你是 Anthropic 的工程师，要让 Claude Desktop 支持下面这些场景：

- 读用户本地文件夹
- 查 PostgreSQL / MySQL / SQLite
- 拉 GitHub / GitLab Issue
- 调 Slack 发消息
- 查 Linear / Jira 工单
- 控制浏览器抓页面
- 调 Figma 拿设计稿
- ……

按以前的做法，每接一个服务就要在 Claude Desktop 里内嵌一段代码：调 SDK、手写 JSON Schema、做权限弹窗、处理错误。应用和服务两两耦合，M 个 AI 应用对 N 个服务，要写 M×N 份集成。

而且这些集成别的 AI 应用用不了。Cursor 想用你写好的 PostgreSQL 集成，只能自己再写一遍。

MCP 的思路是把 M×N 拆成 M+N：

- 服务方（数据库厂商、文档系统、Figma、Slack 等）只写一个 MCP Server
- AI 应用方（Claude Code / Cursor / VS Code / ChatGPT）只实现一个 MCP Client
- 两边用标准协议通信

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

Server 写一次，所有支持 MCP 的应用都能接。

---

## 2. MCP 的定义

MCP 是一个基于 JSON-RPC 2.0 的开放协议，定义了「AI 应用」和「外部能力提供方」之间怎样交换上下文、怎样调用工具。

这个定义里有三点容易被忽略。

第一，它是开放协议，不是某家的产品。spec 在 GitHub 上开源，演进走 SEP 流程（类似 PEP 或 RFC）。

第二，它管的是上下文交换，不只是工具调用。除了调函数，还能传文件、传 prompt 模板、订阅资源变更，以及让 Server 向用户追问（Elicitation）。

第三，它连接的是 AI 应用和外部世界，而不是 LLM 和工具。MCP 是应用层协议，模型怎么使用这些上下文由 AI 应用决定，MCP 不管。

> 很多人在这里理解偏了，把 MCP 当成 Function Calling 的替代品。Function Calling 是模型和 AI 应用之间的协议（OpenAI / Anthropic API 那一层），MCP 是 AI 应用和外部服务之间的协议。两者是上下两层，并不竞争。

---

## 3. 为什么说它像 USB-C

这个比喻能和 MCP 的几个设计决策一一对上：

| USB-C 特性 | 对应 MCP 设计 |
|------------|--------------|
| 统一物理接口 | 统一 JSON-RPC 2.0 消息格式 |
| 双向数据传输 | Server 可以在处理请求的过程中向 Client 要东西（要用户输入、要模型补全）。2026-07-28 起改成 Server 在结果里返回问题、Client 带着答案重试，不再是独立的反向请求 |
| 能力协商（手机、显示器、充电器各不相同） | 每个请求在 `_meta` 里声明自己的 capabilities；`server/discover` 返回 Server 的能力（2025-11-25 及更早是 `initialize` 握手） |
| 热插拔、设备发现 | `tools/list`、`resources/list`、`*/list_changed` 通知 |
| 同一接口承载多种协议（USB / DP / PD） | 同一份 JSON-RPC 消息可以走 stdio 或 Streamable HTTP |

USB-C 让一根线能插所有设备，MCP 让一个 Server 能接所有 AI 应用。

---

## 4. MCP 和你已经熟悉的几样东西

### 和 Function Calling（OpenAI / Anthropic Tool Use）

```
[模型] ←── Function Calling ──→ [AI 应用]   ← 模型决定调哪个函数
[AI 应用] ←─────── MCP ────────→ [外部服务]  ← 外部服务由谁提供
```

Function Calling 是模型 API 提供的能力，让模型可以「申请调用某个函数」。OpenAI 叫 `tools`，Anthropic 叫 `tool_use`，Google 叫 `function_call`，格式互不兼容。

MCP 解决的是 AI 应用从哪里拿到这些工具。Claude Code 通过 MCP 从 GitHub Server 拿到 `create_issue` 工具，再按 Anthropic Tool Use 的格式交给模型；ChatGPT 连同一个 GitHub Server，则按 OpenAI 的 tools 格式交给模型。所以 MCP 位于 Function Calling 之上一层，负责工具的来源和分发。

### 和插件协议（ChatGPT Plugin / Claude Connector）

| 维度 | ChatGPT Plugin（已下线） | MCP |
|------|--------------------------|-----|
| 协议规范 | OpenAPI + 自定义 manifest | 开放标准 |
| 跨平台 | 仅 ChatGPT | 全行业 |
| 双向通信 | 单向 | Server 可以向 Client 要用户输入（2026-07-28 起改为结果里返回问题、Client 重试） |
| 本地能力 | 只能远程 HTTP | stdio + 远程 HTTP |
| Resource / Prompt 抽象 | 只有 action | 三大原语 |

ChatGPT Plugin 在 2024 年已下线。Claude 和 ChatGPT 里现在叫 Connector 的东西，底层接的就是远程 MCP Server。在「AI 应用接入外部能力」这件事上，MCP 已经是这一代的事实标准。

### 和 LangChain Tools / LlamaIndex Tools

LangChain、LlamaIndex 里也有 Tool 抽象，但那是框架内部的概念，只能在该框架的 Agent 里用。MCP 是框架之外的协议，任何语言、任何框架都能实现 Client 和 Server。

LangChain、Pydantic AI 等框架都内置了把 MCP Server 转成框架内 Tool 的适配器（见[在 LangChain 中使用 MCP](../04-integration/03-langchain-mcp.md)和[在 Pydantic AI 中使用 MCP](../04-integration/04-pydantic-ai-mcp.md)），所以同一个 MCP Server 可以同时给 LangChain Agent、Pydantic AI Agent 和 Claude Code 用。

### 和 OpenAPI / gRPC

OpenAPI 是 REST API 的描述格式，AI 应用要用它，还得有一层把 OpenAPI 转成 Tool。gRPC 是强类型 RPC，需要 .proto 文件，对 AI 不友好。MCP 从一开始就是为 AI 设计的：工具描述里直接带给 LLM 看的自然语言说明、JSON Schema 和用法提示，还有 Resource、Prompt 这些 AI 场景专用的概念。

---

## 5. 核心抽象一图看完

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

图里有三个角色：

- Host：AI 应用本身（Claude Code、Cursor 等），负责 UI、对话以及和模型交互
- Client：Host 里的一个组件，每个 Server 对应一个 Client 实例，负责协议层通信
- Server：提供能力的程序（本地进程或远程服务），向 Client 暴露 Tools / Resources / Prompts

Server 通过三种原语（primitives）对外暴露能力：

| 原语 | 谁用 | 类比 |
|------|------|------|
| Tools | 模型主动调（model-controlled） | 函数 / 写操作 |
| Resources | 应用决定何时加载（application-controlled） | 文件 / 只读数据 |
| Prompts | 用户显式调用（user-controlled） | 模板 / Slash 命令 |

反过来，Client 也可以给 Server 提供能力：

| 客户端能力 | 用途 |
|-----------|------|
| Elicitation | Server 在执行过程中向用户索取额外输入 |
| Sampling（已弃用） | Server 请求 Host 的 LLM 做一次 completion |
| Roots（已弃用） | Client 告诉 Server 它能操作哪些目录 |

> 2026-07-28 的变化：Sampling、Roots 和协议层 Logging 已标为 Deprecated（SEP-2577，功能仍可用，至少保留 12 个月）。官方建议分别改为 Server 直接调自己的 LLM API、用工具参数传目录、写 stderr 或 OpenTelemetry。Elicitation 保留，但传递方式变了（见[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)）。

原语、通知机制和生命周期构成了 MCP 协议的主体，后面的「三大原语」和「协议生命周期」两篇会逐个展开。

---

## 6. 用一段代码感受一下

下面用官方 Python SDK 写一个 hello world MCP Server，10 行代码暴露一个 `add` 工具：

```python
# server.py
from mcp.server import MCPServer

mcp = MCPServer("hello-mcp")

@mcp.tool()
def add(a: int, b: int) -> int:
    """两个整数相加"""
    return a + b

if __name__ == "__main__":
    mcp.run()  # 默认走 stdio
```

启动：

```bash
python server.py
```

用官方 Inspector 可视化调试：

```bash
npx @modelcontextprotocol/inspector python server.py
# 打开浏览器 http://localhost:6274
```

接到 Claude Code 里（详见[在 Claude Code 中接入 MCP](../04-integration/01-claude-code.md)）：

```json
// 项目根目录的 .mcp.json
{
  "mcpServers": {
    "hello-mcp": {
      "command": "python",
      "args": ["/abs/path/to/server.py"]
    }
  }
}
```

现在 Claude Code 的所有对话都能用 `add` 工具了。整个过程中你没写任何 Claude 专属代码，没碰 Anthropic SDK，也没处理 tool_use/tool_result。

---

## 7. 2025–2026 年的生态

### 主流 Client（消费端）
- Claude Code / Claude Desktop：MCP 发起方，原生支持
- VS Code（Copilot Chat）：内置 MCP Server 管理
- Cursor：原生支持
- Continue / Cline / Zed / JetBrains AI Assistant：均已支持
- ChatGPT Desktop / API：2025 年加入支持（Custom Connectors）

### 官方 Reference Server（生产端）
仓库 [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) 提供了一批参考实现：

- `filesystem`：本地文件系统
- `git`：读写本地 Git 仓库
- `fetch`：抓网页并转成 Markdown
- `memory`：知识图谱式记忆
- `time`、`sequentialthinking`、`everything`（测试用，覆盖全部协议特性）

早期放在这里的 `github`、`postgres`、`sqlite`、`slack`、`puppeteer` 等已移到归档仓库 `servers-archived`，GitHub、Playwright 这类改由厂商自己维护官方 Server（如 `github/github-mcp-server`、`microsoft/playwright-mcp`）。

### 厂商出品的 Server
Sentry、Linear、Notion、Figma、Stripe、Vercel、Supabase 等都已上线官方 MCP Server。MCP 项目维护的 [Registry](https://registry.modelcontextprotocol.io) 是官方发布中心（还在 preview 阶段，见[把 Server 发布到 MCP Registry](../06-advanced/03-registry.md)）。

### Python SDK 现状
仓库是 `modelcontextprotocol/python-sdk`，PyPI 包名 `mcp`。当前稳定线是 2.x（本手册基于 2026 年 11 月的 `mcp 2.3.0`），实现 2026-07-28 规范，同时兼容 2025 年的老客户端。

- 高层 API：`MCPServer`（v1 叫 `FastMCP`，v2 改名并挪到 `mcp.server.MCPServer`，装饰器风格和 FastAPI 类似）
- 客户端：新的一等公民 `mcp.Client`，一个对象完成连接、协商和调用
- 低层 API：`Server`（v2 重写，构造参数传 `on_*` 处理函数）
- v1.x 进入维护期，只修关键 bug；老代码可以 `pip install "mcp<2"` 继续跑

另外还有一个独立的第三方包 `fastmcp`（jlowin/Prefect 维护，当前 4.1.x）。它是官方 SDK 里 FastMCP 1.0 的后续演化，API 和官方 `MCPServer` 不同，本手册不涉及。

---

## 8. 什么时候不该用 MCP

标准化的代价是约束。下面这些情况可能用不着 MCP：

| 场景 | 更合适的方案 |
|------|--------------|
| 只在一个 Agent 框架内部用工具，不打算给别的 AI 应用用 | 直接用框架原生 Tool（LangChain `@tool`、Pydantic AI `@agent.tool`） |
| 工具只是一段固定的 prompt 逻辑，没有外部 IO | Prompt 模板加字符串拼接就够了 |
| 服务已有成熟的 OpenAPI，AI 客户端只是众多消费者之一 | 通过 OpenAPI-to-MCP 转换层接入，不必专门写 MCP Server |
| 对延迟要求到微秒级 | MCP 是 JSON-RPC，序列化开销不可忽略 |

反过来，只要你写的能力可能被多个 AI 应用使用，或者想让 Claude Code、Cursor 用上，MCP 基本就是默认选择。

---

## 9. 不同角色怎么看 MCP

| 你的角度 | MCP 对你意味着什么 |
|-------------------|-----------|
| 协议工程师 | JSON-RPC 2.0 + 能力协商 + 三大原语 + 双向请求 |
| AI 应用作者 | Agent 直接接入现成的几百个工具 |
| 后端工程师 | 把内部系统包成 Server，各家 AI 客户端都能用 |
| 产品经理 | AI 工具有了标准化的分发渠道 |
| CIO / 架构师 | 企业内部统一接入 AI 能力的事实标准协议 |

---

## 10. 常见坑

| 坑 | 描述 | 怎么避免 |
|----|------|---------|
| 混淆 MCP 与 Function Calling | 以为 MCP 是给模型用的 | MCP 是给 AI 应用用的，模型那边仍然是 OpenAI/Anthropic 的 tool 格式 |
| 所有能力都写成 Tool | Resource / Prompt 闲置 | 只读上下文用 Resource，模板用 Prompt（详见[三种原语 Tools、Resources 与 Prompts](03-primitives.md)） |
| 以为 Server 一定是远程服务 | 觉得 Server 必须部署成 HTTP | 官方参考 Server 大多走 stdio，作为本地子进程运行 |
| 忽略能力协商 | Server 发出了 Client 没声明支持的请求（如 elicitation） | 只使用 Client 在 `_meta` 里（2025 版是在 `initialize` 里）声明过的 capability |
| 把 SSE 流当 REST 读 | Streamable HTTP 不是普通 REST | 看[传输方式 stdio、Streamable HTTP 与进程内](../03-client/02-transports.md) |

---

## 11. 下一步

- 想弄清 Host/Client/Server 的关系和传输层：[Host、Client、Server 与两层架构](./02-architecture.md)
- 想知道 Tools/Resources/Prompts 各自什么时候用：[三种原语 Tools、Resources 与 Prompts](./03-primitives.md)
- 想直接跑通 Hello World：[安装 Python SDK 与 Inspector](./05-installation.md)，然后看 [写第一个 MCP Server 和 Client](./06-first-server.md)

## 参考资料

- 官方 What is MCP：https://modelcontextprotocol.io/docs/getting-started/intro
- 架构总览：https://modelcontextprotocol.io/docs/learn/architecture
- Spec 2026-07-28（当前）：https://modelcontextprotocol.io/specification/2026-07-28
- 变更清单：https://modelcontextprotocol.io/specification/2026-07-28/changelog
- Spec 2025-11-25（上一版）：https://modelcontextprotocol.io/specification/2025-11-25
- Python SDK：https://github.com/modelcontextprotocol/python-sdk（文档 https://py.sdk.modelcontextprotocol.io ）
- Reference Servers：https://github.com/modelcontextprotocol/servers
