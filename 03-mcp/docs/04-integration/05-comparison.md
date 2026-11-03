# MCP Integration 05：MCP 与 Function Calling、OpenAPI、LangChain Tools 的对比

> MCP 管的是工具从哪里来。它和模型层的 Function Calling、描述传统 REST 接口的 OpenAPI、框架内部的 LangChain Tools 各在不同的层次。先把这几者的关系理清楚，再做选型就有依据了。

---

## 1. 先看层级

```
┌────────────────────────────────────────────────┐
│  Model APIs（OpenAI / Anthropic / Gemini）      │ ← Function Calling 在这层
│  - 让模型在回复里申请调用某个函数               │
└────────────────────────────────────────────────┘
                       ▲
                       │ 把工具描述塞进 API
┌────────────────────────────────────────────────┐
│  Agent 框架（LangChain / Pydantic AI / Crew）   │ ← LangChain Tools / Pydantic AI Toolsets
│  - 在框架内表示工具                            │
│  - 接管 tool_use → 调函数 → tool_result 循环    │
└────────────────────────────────────────────────┘
                       ▲
                       │ 从协议层加载工具
┌────────────────────────────────────────────────┐
│  MCP 协议                                       │ ← MCP 在这层
│  - 标准化"AI 应用 ↔ 外部能力"的接入方式         │
└────────────────────────────────────────────────┘
                       ▲
                       │ 包装、转换、分发
┌────────────────────────────────────────────────┐
│  外部能力来源                                   │
│  - OpenAPI REST、gRPC、Python 函数、Database    │ ← OpenAPI / gRPC 在这层
│  - 数据库、SaaS、本地工具                      │
└────────────────────────────────────────────────┘
```

从上到下一共四层：模型 API、Agent 框架、MCP、实际的后端服务。

---

## 2. MCP 与 Function Calling

这两个最容易被混为一谈。

### 2.1 Function Calling 是什么
Function Calling 是模型 API 提供的能力：模型在 response 里表示「我要调用 `xxx`，参数是 yyy」。OpenAI 叫 `tools`，Anthropic 叫 `tool_use`，Google 叫 `function_call`，各家格式不同，互不兼容。

### 2.2 MCP 是什么
MCP 是协议层，规定 AI 应用怎样接入工具来源。它和具体的模型无关，MCP 工具最后还是要转成 OpenAI tools 或 Anthropic tool_use 的格式交给模型。

### 2.3 两者的关系
```
                          ┌─[OpenAI tools 格式]─→ GPT / 千问（OpenAI 兼容）
[MCP Server 暴露 tools]──┤
                          └─[Anthropic 格式]─────→ Claude
```

同一份 MCP 工具，由 Host 转换成不同模型 API 的 Function Calling 格式。所以 MCP 不会取代 Function Calling，它处在 Function Calling 的上游。

> 模型 API 也开始直接对接 MCP 了。比如 OpenAI 的 Responses API 支持 `tools=[{"type": "mcp", "server_url": ...}]`，Pydantic AI 的 `MCP(url, native=True)` 也能让模型厂商替你连接远程 MCP Server。这时「Host 把 MCP 工具转成 Function Calling 格式」这一步由厂商代劳了，但层级关系没有变，而且 Server 必须能从公网访问，凭据也要交给厂商。想把凭据、hook 和追踪留在自己手里，就让 Host 在本地连接（`MCP(url)` 的默认行为）。

| 维度 | Function Calling | MCP |
|------|------------------|-----|
| 协议层 | 模型 API（各家不同） | 通用协议（统一） |
| 谁来实现 | OpenAI / Anthropic 等模型厂商 | 任何人 |
| 何时使用 | AI 应用调用模型时 | AI 应用启动或加载工具时 |
| 替代关系 | 不存在替代，它在 MCP 的下游 | 不适用 |

---

## 3. MCP 与 OpenAPI / gRPC

REST 服务想接入 AI，有两条路。

### 3.1 直接用 OpenAPI
在 Agent 框架里装一个把 OpenAPI 转成 Tool 的转换器，工具描述直接来自 OpenAPI spec。适合服务已经有 OpenAPI、AI 客户端只是众多调用方之一的情况。

### 3.2 包装成 MCP Server
写一个 Server，把 REST 调用包装成 MCP tool，工具描述可以专门为 AI 优化（写得更详细，附上用法提示）。适合希望服务被各类 AI 客户端使用、需要做权限控制或参数过滤的情况。

| 维度 | 直接接 OpenAPI | 包装成 MCP |
|------|---------------|---------|
| 工作量 | 低（已经有了） | 中（要写包装层） |
| 工具描述质量 | OpenAPI 描述比较通用，对 AI 不太友好 | 可以针对 AI 优化 |
| 跨平台 | 限于框架内（如 LangChain 社区包里的 OpenAPI 工具包） | 任何 MCP Client |
| 鉴权 | 各 Client 自己处理 | MCP 层统一用 OAuth 2.1 |
| Resource / Prompt | 不支持 | 支持 |
| 服务端中途向用户提问 | 不支持 | 支持（Elicitation；2026 版通过多轮请求实现） |

内部服务、只是短期想用一下，直接接 OpenAPI 就行；如果要长期面向 AI、跨产品分发，就包装成 MCP。

### 3.3 自动转换工具
已经有一些现成的工具：独立包 `fastmcp` 的 `FastMCP.from_openapi()` 能根据 OpenAPI spec 生成 MCP Server；反方向有 Open WebUI 的 `mcpo`，能把 MCP Server 暴露成带 OpenAPI 描述的 REST 接口。

完全不想写代码的话可以试试这些自动方案，但生产环境我还是建议手写，这样工具描述的质量在你自己手里。

---

## 4. MCP 与 LangChain Tools / Pydantic AI Toolsets

框架内的 Tool 抽象只在那个框架的生态里有效，MCP 则是框架之外的协议。

| 维度 | LangChain `@tool` / Pydantic AI `@agent.tool` | MCP |
|------|----------------------------------------------|-----|
| 范围 | 单一框架内 | 跨框架、跨产品 |
| 部署 | 同一进程 | 子进程或远程 |
| 类型 | 全程类型推导 | 经过 schema 序列化 |
| 测试 | 直接写单元测试 | 需要启动 Server |
| 适合 | 和框架紧密耦合的工具 | 公共能力 |

完全在一个 Agent 框架内使用、不打算分发的工具，用框架原生的写法；可能被 IDE、CLI、Web 等多个产品使用的工具，用 MCP。

---

## 5. 几个常见场景怎么选

### 5.1 想让 Claude Code 调用内部 API
用 MCP Server，用 HTTP 包装内部 API。直接接 OpenAPI 行不通，Claude Code 不解析 OpenAPI。

### 5.2 用 LangChain 写一个 Agent，要调用 GitHub API
两条路都可以。走 MCP 的话，用 GitHub 官方的 MCP Server 加 `langchain.mcp.MCPAdapter`；走 LangChain Tool 的话，用 LangChain 自带的 GitHub 工具包。如果这个 Agent 以后也要在 Claude Code 里用，选 MCP；否则 LangChain Tool 更简单。

### 5.3 有一个 OpenAPI 服务想给 AI 用
写一个最小的 MCP 包装层（代码量不大，重点是几段手写的工具描述），体验会比直接用 OpenAPI 好很多。

### 5.4 要做一个 AI 能力中台
用 MCP Registry 加远程 Streamable HTTP，统一做鉴权、监控和计费。

### 5.5 想给一段固定的 prompt 模板加上用户参数
如果用户用的是 Claude Code 这类支持 Prompts 的客户端，就用 MCP Prompts；否则用框架里的 PromptTemplate。

---

## 6. 同一个能力的几种实现方式

以「根据用户问题查数据库并返回答案」为例，可以这样实现：

### 方式 A：纯 Function Calling
```python
# 直接给模型一个 sql_query 工具
client.chat.completions.create(          # 以 OpenAI 兼容接口为例
    model="qwen-flash",
    tools=[{"type": "function", "function": {"name": "sql_query", "parameters": {...}}}],
    ...
)
# 模型决定 SQL，调用，你执行
```
- 优点：简单
- 缺点：想在其他产品里复用，只能复制粘贴

### 方式 B：LangChain Tool
```python
from langchain.tools import tool

@tool
def sql_query(sql: str) -> str:
    return execute_sql(sql)

agent = create_agent(model, [sql_query])   # from langchain.agents import create_agent
```
- 优点：能用上框架的生态
- 缺点：离开 LangChain 就用不了

### 方式 C：MCP Server
```python
# db_mcp_server.py
from mcp.server import MCPServer
mcp = MCPServer("db")

@mcp.tool()
def sql_query(sql: str) -> str:
    return execute_sql(sql)

mcp.run()
```
- 优点：Claude Code、Cursor、VS Code、LangChain、Pydantic AI 等都能用
- 缺点：多了一层进程间通信（毫秒级开销）

### 方式 D：OpenAPI
```yaml
# openapi.yaml
paths:
  /query:
    post:
      ...
```
- 优点：不只 AI 能用
- 缺点：AI Client 默认不读 OpenAPI

选哪个，可以从三个问题考虑：这个工具会被多少个 AI 应用使用？多的话选 MCP。它有没有很多非 AI 的使用方？多的话要兼容 OpenAPI。对延迟是不是敏感到连毫秒级的进程间开销都不能接受？是的话用框架内的 Tool，在同一进程里调用。

---

## 7. 混合使用

很多生产环境里是几种方式混着用的：

```
框架内工具：Agent 内部状态读写、轻量计算
       +
MCP Tools：跨产品能力（GitHub、Sentry、内部 SaaS）
       +
OpenAPI ：暴露给非 AI 客户端的服务（依然存在）
```

每一层做它擅长的事，没必要从头到尾只用一种。

---

## 8. 决策树

```
问 1：这个能力会被几个 AI 应用消费？
├── 1 个 → 框架内 Tool 就行
└── 多个 → 继续

问 2：会被非 AI 客户端消费吗？
├── 会 → 同时存在 OpenAPI / REST + 包个 MCP 给 AI 用
└── 不会 → 纯 MCP

问 3：本地还是远程？
├── 本地工具、文件 IO、调本地 SDK → stdio
└── 远程服务、需要鉴权、多用户共享 → Streamable HTTP

问 4：要复杂状态机？
├── 是 → 加 Pydantic Graph 或 LangGraph（MCP 仍然是工具来源）
└── 否 → 直接 MCP Server + 框架默认 Agent
```

---

## 9. 三组对比的结论

| 比较对象 | 结论 |
|----------|--------|
| MCP 与 Function Calling | 处在不同的层。MCP 是工具来源的协议，Function Calling 是模型 API 的特性。 |
| MCP 与 OpenAPI | OpenAPI 面向所有客户端，MCP 针对 AI 客户端做了优化（工具描述、Resource/Prompt、中途追问用户）。 |
| MCP 与 LangChain Tool | LangChain Tool 只在框架内有效，MCP 在框架之外。需要复用就用 MCP。 |

---

## 10. 下一步

集成部分的 5 篇到这里结束。下一章从[用 Streamable HTTP 部署远程 MCP](../05-production/01-remote-mcp.md)开始，讲远程部署、OAuth、安全和可观测性。

## 参考资料

- OpenAI Function Calling 文档：https://platform.openai.com/docs/guides/function-calling
- Anthropic Tool Use：https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
- MCP 官方 SDK 列表：https://modelcontextprotocol.io/docs/2026-07-28/sdk
