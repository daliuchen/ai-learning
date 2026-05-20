# MCP Integration 03：LangChain 接 MCP

> **一句话**：用 `langchain-mcp-adapters` 包，一行代码把任意 MCP Server 的所有工具转成 LangChain Tool，无缝塞进 LangGraph / Agent。让你写的 MCP Server 同时被 Claude Code 和 LangChain Agent 复用。

---

## 1. 为什么要把 MCP 接到 LangChain

写 MCP Server 不只是为 Claude Code——你可能也想：

- 在 LangChain Agent 里复用同一份工具
- 在 LangGraph 多 Agent 工作流里用某个外部能力
- 在生产服务里跑 LangServe / Chains

`langchain-mcp-adapters` 提供桥：**MCP Server → LangChain Tool**，让两个生态打通。

---

## 2. 安装

```bash
pip install langchain-mcp-adapters langgraph langchain-openai
```

> 完整依赖在本手册 requirements.txt。

---

## 3. 最小可用代码

```python
import asyncio
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI


async def main():
    client = MultiServerMCPClient({
        "hello": {
            "command": "python",
            "args": ["/abs/path/to/06_first_server.py"],
            "transport": "stdio",
        },
        "math": {
            "url": "http://localhost:8765/mcp",
            "transport": "streamable_http",
        },
    })

    # 拿所有 Server 的工具，已经是 LangChain Tool 实例
    tools = await client.get_tools()

    agent = create_react_agent(
        model=ChatOpenAI(model="gpt-4o"),
        tools=tools,
    )

    result = await agent.ainvoke({"messages": [
        {"role": "user", "content": "现在几点？"}
    ]})
    print(result["messages"][-1].content)


asyncio.run(main())
```

`MultiServerMCPClient` 自动：

1. 启动所有配置的 Server（stdio 起子进程 / HTTP 建连接）
2. 对每个 Server 完成 initialize 握手
3. 调 `tools/list` 拿工具
4. 把每个工具用 `pydantic.BaseModel` 包成 LangChain `StructuredTool`

---

## 4. 工具的转换细节

MCP 工具 → LangChain Tool 时做了这些事：

| MCP | LangChain Tool |
|-----|---------------|
| `name` | `name`（**带 namespace**：`<server>__<tool>`） |
| `description` | `description` |
| `inputSchema` | `args_schema`（Pydantic BaseModel） |
| `tools/call` | `arun` 或 `coroutine` |
| `result.isError` | 抛 `ToolException` |
| `result.content` | 字符串化（多 content 拼接） |

调用 LangChain Tool 时实际走的是 `await session.call_tool(name, kwargs)`。

---

## 5. 单 Server 简化

只连一个 Server 不需要 MultiServerMCPClient：

```python
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

async with stdio_client(StdioServerParameters(command="python", args=["server.py"])) as (r, w):
    async with ClientSession(r, w) as session:
        await session.initialize()
        tools = await load_mcp_tools(session)
        # 现在 tools 是 list[BaseTool]，可以直接喂 LangGraph
```

---

## 6. Resource → LangChain？

`langchain-mcp-adapters` 也支持 Resource：

```python
from langchain_mcp_adapters.resources import load_mcp_resources

async with ... as session:
    resources = await load_mcp_resources(session)
    # resources: list[Blob] —— LangChain 的标准 Document 抽象
```

适合：把 MCP Resource 当作 RAG 数据源喂给 VectorStore。

---

## 7. Prompts → LangChain？

```python
from langchain_mcp_adapters.prompts import load_mcp_prompts

async with ... as session:
    prompts = await load_mcp_prompts(session)
    # prompts: list[PromptTemplate]
```

可以直接用 `prompts[0].format(...)` 取到 prompt 文本。

---

## 8. 在 LangGraph 里用

LangGraph 是 LangChain 的状态机框架，最适合多 Agent / 复杂工作流：

```python
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.prebuilt import ToolNode
from langchain_mcp_adapters.client import MultiServerMCPClient

async def build_graph():
    client = MultiServerMCPClient({...})
    tools = await client.get_tools()

    def call_model(state: MessagesState):
        model = ChatOpenAI(model="gpt-4o").bind_tools(tools)
        return {"messages": [model.invoke(state["messages"])]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    # ... 条件边
    return builder.compile()
```

整个 MCP Server 的能力都自动暴露给 LangGraph 的 `ToolNode`。

---

## 9. 注意事项

### 9.1 异步 vs 同步
MCP SDK 是纯 async，所以你的 LangChain 代码也要是 async（用 `ainvoke` 而非 `invoke`）。同步包装可以做但会有性能损失。

### 9.2 连接持久化
`MultiServerMCPClient` 的连接是**懒启动**——第一次调 `get_tools()` 时才建。生产里建议把 client 实例化和 tools 加载放在应用启动时。

### 9.3 错误处理
MCP 的 `isError: true` 会变成 LangChain `ToolException`，模型能看到。但**协议错误**（工具不存在）会直接抛 `McpError`，要 catch。

---

## 10. 完整 demo：LangGraph + 多 MCP Server

```python
# demos/integration/03_langchain_multi_mcp.py
"""LangGraph + 多个 MCP Server"""
import asyncio
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

DEMOS = Path(__file__).resolve().parents[1]


async def main():
    client = MultiServerMCPClient({
        "hello": {
            "command": "python",
            "args": [str(DEMOS / "basics" / "06_first_server.py")],
            "transport": "stdio",
        },
    })

    tools = await client.get_tools()
    print(f"加载 {len(tools)} 个工具：")
    for t in tools:
        print(f"  - {t.name}")

    agent = create_react_agent(
        ChatOpenAI(model="gpt-4o-mini"),
        tools,
    )

    questions = [
        "现在中国时间几点？",
        "计算 17 加 25",
    ]
    for q in questions:
        print(f"\n[USER] {q}")
        result = await agent.ainvoke({"messages": [("user", q)]})
        print(f"[Agent] {result['messages'][-1].content}")


asyncio.run(main())
```

需要 `OPENAI_API_KEY`。

---

## 11. vs 直接用 LangChain `@tool`

| 维度 | LangChain `@tool` | MCP Tool（经 adapter） |
|------|-------------------|----------------------|
| 写一次 | 只能在 LangChain 用 | Claude Code / Cursor / LangChain 都能用 |
| 部署 | 同进程 | 子进程 / 远程 HTTP |
| 类型安全 | Pydantic 直接拿 | 经 adapter 转 |
| 适合 | 框架内部工具、tight coupling | 公共能力、跨产品复用 |

**结论**：内部工具用 `@tool`；想分发给多个产品 / 写一次到处用 → MCP Server + adapter。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **工具找不到** | 检查 `MultiServerMCPClient` 配置 transport 字段 |
| **`McpError` 没 catch** | 协议错误用 try/except，业务错误已转 ToolException |
| **stdio Server 启动慢** | 客户端的 connection timeout 调大或先单独跑一遍预热 |
| **资源大被全部加载** | `load_mcp_resources` 谨慎用，可能拉 100MB 数据 |
| **多 Server 工具同名** | adapter 自动加 namespace，但要检查 LangGraph 端是否清理过名字 |
| **sync 代码混 async** | 用 `await agent.ainvoke(...)` 不是 `agent.invoke(...)` |

---

## 13. 下一步

- 📖 Pydantic AI 接 MCP → [04-pydantic-ai-mcp.md](./04-pydantic-ai-mcp.md)
- 📖 vs Function Calling / OpenAPI → [05-comparison.md](./05-comparison.md)
- 🔍 跨手册：LangChain 全家桶 → ../../01-langchain/

## 参考资料

- langchain-mcp-adapters：https://github.com/langchain-ai/langchain-mcp-adapters
- LangGraph 文档：https://langchain-ai.github.io/langgraph/
- LangChain Tools：https://python.langchain.com/docs/concepts/tools/
