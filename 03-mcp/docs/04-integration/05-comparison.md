# MCP Integration 05：MCP vs Function Calling / OpenAPI / LangChain Tools

> **一句话**：MCP 不是"另一种工具调用"——它是**工具的来源协议**，和模型层的 Function Calling、传统 REST 描述的 OpenAPI、框架内 Tool 抽象的 LangChain Tools 各自在不同层。把它们的关系搞清，选型就有逻辑了。

---

## 1. 一张图先看层级

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

四层：**模型 API → Agent 框架 → MCP → 实际后端服务**。

---

## 2. MCP vs Function Calling

最常被搞混的两个。

### 2.1 Function Calling 是什么
- OpenAI 叫 `tools`、Anthropic 叫 `tool_use`、Google 叫 `function_call`
- 是**模型 API**的能力：模型在 response 里发"我要调 `xxx`，参数是 yyy"
- 每家格式不一样、互不兼容

### 2.2 MCP 是什么
- **协议层**：定义 AI 应用怎么把工具来源接入
- 模型层无关——MCP 工具最终还是要转成 OpenAI tools / Anthropic tool_use 格式喂给模型

### 2.3 关系（关键！）
```
                          ┌─[OpenAI tools 格式]─→ GPT-4o
[MCP Server 暴露 tools]──┤
                          └─[Anthropic 格式]─────→ Claude
```

同一份 MCP 工具，被 Host 转换成不同模型 API 的 Function Calling 格式。**MCP 不替代 Function Calling，它是 Function Calling 的"上游"**。

| 维度 | Function Calling | MCP |
|------|------------------|-----|
| 协议层 | 模型 API（每家不一样） | 通用协议（统一） |
| 谁实现 | OpenAI / Anthropic / ... | 任何人 |
| 谁消费 | AI 应用调模型时 | AI 应用启动 / 加载工具时 |
| 替代关系 | ❌ 是 MCP 的下游 | — |

---

## 3. MCP vs OpenAPI / gRPC

REST 服务和 AI 怎么接？两条路：

### 3.1 直接用 OpenAPI
- Agent 框架装个 `OpenAPI → Tool` 转换器
- 工具描述来自 OpenAPI spec
- 适合：服务已经有 OpenAPI、AI 客户端只是众多消费者之一

### 3.2 包成 MCP Server
- 写个 Server 把 REST 调用包装成 MCP tool
- 工具描述为 AI 优化（更详细、用例提示）
- 适合：服务想给 AI 时代的客户端用、需要权限/参数过滤

| 维度 | OpenAPI 直接接 | 包成 MCP |
|------|---------------|---------|
| 写作工作量 | 低（已有） | 中（写包装层） |
| 工具描述质量 | OpenAPI 通用、对 AI 不太友好 | 可针对 AI 优化 |
| 跨平台 | 框架内（如 LangChain 的 `OpenAPIChain`） | 任何 MCP Client |
| 鉴权 | 各 Client 自己处理 | MCP 层统一 OAuth 2.1 |
| Resource / Prompt | ❌ | ✅ |
| 双向通信 | ❌ | ✅ |

**结论**：内部服务 + 短期想用 → OpenAPI 直接；战略性接 AI、跨产品分发 → 包成 MCP。

### 3.3 自动转换工具
社区已有几个：
- `openapi-mcp-server`：把 OpenAPI spec 自动生成 MCP Server
- 反向：`mcp-to-openapi`：把 MCP 暴露成 REST

如果完全不想写代码，可以试自动方案。但生产环境建议手写——你能控制工具描述的质量。

---

## 4. MCP vs LangChain Tools / Pydantic AI Toolsets

框架内 Tool 抽象是"框架内的小生态"，MCP 是"框架外的协议"。

| 维度 | LangChain `@tool` / Pydantic AI `@agent.tool` | MCP |
|------|----------------------------------------------|-----|
| 范围 | 单一框架内 | 跨框架 / 跨产品 |
| 部署 | 同进程 | 子进程 / 远程 |
| 类型链路 | 全程类型推导 | 经 schema 序列化 |
| 测试 | 直接 unit test | 要起 Server |
| 适合 | 框架紧耦合工具 | 公共能力 |

**建议**：
- 100% 在一个 Agent 框架内、不打算分发 → 用框架原生
- 工具可能被多个产品（IDE、CLI、Web）消费 → MCP

---

## 5. 几个典型场景的选型

### 5.1 我要让 Claude Code 调我的内部 API
→ **MCP Server**（HTTP 包装内部 API）。OpenAPI 直接接不行（Claude Code 不解析 OpenAPI）。

### 5.2 我在 LangChain 写个 Agent，调 GitHub API
→ 两条路都行。**MCP**：用社区的 `mcp-server-github` + adapter；**LangChain Tool**：用 LangChain 的 `GitHubToolkit`。如果你计划这个 Agent 也跑在 Claude Code 里，用 MCP；否则 LangChain Tool 更简单。

### 5.3 我有个 OpenAPI 服务想给 AI 用
→ 写个**最小 MCP 包装层**（10 行代码 + 几个手工写的工具描述）比直接 OpenAPI 体验好得多。

### 5.4 我做一个"AI 能力中台"
→ 用 **MCP Registry + 远程 Streamable HTTP**。统一鉴权 / 监控 / 计费。

### 5.5 我想给一段确定的 prompt 模板加用户参数
→ 用 **MCP Prompts**（如果用户在 Claude Code 等支持 Prompts 的客户端用）。否则用框架内的 PromptTemplate。

---

## 6. 一个能力的多种实现路径

同一个能力——"按用户问题查数据库返回答案"——可以这样实现：

### 路径 A：纯 Function Calling
```python
# 直接给模型一个 sql_query 工具
client.messages.create(
    model="claude-sonnet-4-6",
    tools=[{"name": "sql_query", "input_schema": {...}}],
    ...
)
# 模型决定 SQL，调用，你执行
```
- ✅ 简单
- ❌ 跨产品复用？复制粘贴

### 路径 B：LangChain Tool
```python
from langchain.tools import tool

@tool
def sql_query(sql: str) -> str:
    return execute_sql(sql)

agent = create_react_agent(model, [sql_query])
```
- ✅ 框架生态
- ❌ 离开 LangChain 不能用

### 路径 C：MCP Server
```python
# db_mcp_server.py
@mcp.tool()
def sql_query(sql: str) -> str:
    return execute_sql(sql)
mcp.run()
```
- ✅ Claude Code、Cursor、LangChain、Pydantic AI、ChatGPT 都能用
- ❌ 多了一层 IPC（毫秒级）

### 路径 D：OpenAPI
```yaml
# openapi.yaml
paths:
  /query:
    post:
      ...
```
- ✅ 不只 AI 能用
- ❌ AI Client 默认不读 OpenAPI

**选哪个？** 三角权衡：
- 工具会被多少个 AI 应用消费？多 → MCP
- 这个工具的非 AI 用户多吗？多 → OpenAPI 兼容
- 性能要求毫秒级？是 → 框架内 Tool（同进程）

---

## 7. 混合使用的最佳实践

很多生产环境是**混合**：

```
框架内工具：Agent 内部状态读写、轻量计算
       +
MCP Tools：跨产品能力（GitHub、Sentry、内部 SaaS）
       +
OpenAPI ：暴露给非 AI 客户端的服务（依然存在）
```

把它们组合好，每层做擅长的事，不要"全栈用一种"。

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

## 9. 一句话总结

| 比较对象 | 一句话 |
|----------|--------|
| **MCP vs Function Calling** | 不同层。MCP 是工具的协议来源，Function Calling 是模型 API 的特性。 |
| **MCP vs OpenAPI** | OpenAPI 给所有客户端用，MCP 专为 AI 客户端优化（工具描述、Resource/Prompt、双向通信）。 |
| **MCP vs LangChain Tool** | LangChain Tool 是框架内的，MCP 是框架外的。要复用就用 MCP。 |

---

## 10. 下一步

04-integration 全部 5 篇结束。下一章 05-production：远程部署、OAuth、安全、可观测。

## 参考资料

- 官方对比文档：MCP 不与 Function Calling 比较，它在不同层
- OpenAI Function Calling 文档：https://platform.openai.com/docs/guides/function-calling
- Anthropic Tool Use：https://docs.anthropic.com/en/docs/build-with-claude/tool-use
