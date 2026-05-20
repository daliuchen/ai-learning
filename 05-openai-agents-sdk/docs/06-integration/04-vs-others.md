# 跟 Pydantic AI / LangChain 互操作

> **一句话**：OpenAI Agents SDK 不锁你——能用 LangChain 的 tools、跟 Pydantic AI 的 agent 互调、Tools 互转——并不二选一。

---

## 1. 互操作的几个层面

1. **Tool 层面**：用 LangChain Tool 当 OpenAI Agents 的工具
2. **Agent 层面**：OpenAI Agent 调 Pydantic AI Agent（或反过来）
3. **观测层面**：共用 LangSmith / Logfire（详见 [02-observability.md](./02-observability.md)）
4. **协议层面**：通过 MCP 互通（详见 [01-mcp.md](./01-mcp.md)）

---

## 2. LangChain Tool → OpenAI Agents

LangChain 社区 tool 多（SerpAPI / Wolfram / Shell 等）。在 OpenAI Agents 里复用：

```python
from langchain_community.tools import ShellTool
from agents import Agent, function_tool


# LangChain tool
shell_tool = ShellTool()


# 包成 OpenAI Agents tool
@function_tool
def shell(command: str) -> str:
    """执行 shell 命令（小心！）"""
    return shell_tool.run(command)


agent = Agent(name="A", tools=[shell])
```

简单粗暴：写个 wrapper。

或者用 `langchain_openai_agents` 第三方包（如果有）自动桥接。

---

## 3. Pydantic AI Agent → OpenAI Agents 子工具

```python
from pydantic_ai import Agent as PydAgent
from agents import Agent, function_tool


# Pydantic AI 写的 agent
pyd_translator = PydAgent(
    "anthropic:claude-sonnet-4-6",
    system_prompt="翻译到英语",
)


# 包成 OpenAI Agents tool
@function_tool
async def translate(text: str) -> str:
    """翻译到英语"""
    result = await pyd_translator.run(text)
    return result.output


main = Agent(
    name="Main",
    instructions="用 translate 翻译",
    tools=[translate],
)
```

主 Agent 跑 OpenAI，子任务跑 Pydantic AI（用 Claude）——两者优势互补。

---

## 4. OpenAI Agent → Pydantic AI 工具

反过来也行。OpenAI Agents 没有"导出成 tool"的 API，自己包：

```python
from agents import Agent, Runner
from pydantic_ai import Agent as PydAgent


oai_classifier = Agent(name="Classifier", instructions="分类", output_type=Sentiment)


pyd_main = PydAgent("openai:gpt-4o", system_prompt="...")


@pyd_main.tool
async def classify(ctx, text: str) -> str:
    result = await Runner.run(oai_classifier, text)
    return result.final_output.model_dump_json()
```

---

## 5. LangGraph 节点里跑 OpenAI Agent

```python
from langgraph.graph import StateGraph
from agents import Agent, Runner


oai_agent = Agent(name="A", instructions="...")


async def my_node(state):
    result = await Runner.run(oai_agent, state["input"])
    return {"output": result.final_output, "agent": result.last_agent.name}


graph = StateGraph(...)
graph.add_node("oai", my_node)
```

LangGraph 做状态机外壳，OpenAI Agents 做具体节点执行——很常见的搭配。

---

## 6. 跟 RAG（LlamaIndex / 自己写）集成

LlamaIndex 的 index 用 retriever，包成 OpenAI Agents tool：

```python
from llama_index.core import VectorStoreIndex
from agents import function_tool


index = VectorStoreIndex.from_documents(my_docs)
query_engine = index.as_query_engine()


@function_tool
def search_docs(query: str) -> str:
    """搜公司文档"""
    response = query_engine.query(query)
    return str(response)


agent = Agent(name="A", tools=[search_docs])
```

---

## 7. 共用 Observability

LangChain 默认上 LangSmith，OpenAI Agents 默认上 OpenAI Platform。让它们同上一个平台：

```python
# 都上 Logfire
import logfire

logfire.configure()
logfire.instrument_openai()  # OpenAI SDK 维度
logfire.instrument_openai_agents()  # Agents SDK 高层
logfire.instrument_langchain()  # LangChain
```

或都上 LangSmith：

```python
# OpenAI Agents 的 OpenAI client 包 wrap_openai
# LangChain 走环境变量
```

观测层统一好处：跨框架 trace 串起来。

---

## 8. 跟 MCP 串

MCP 是更标准的"工具协议"，任何框架都能消费：

```python
# OpenAI Agents 消费 MCP
async with MCPServerStdio(...) as server:
    oai_agent = Agent(mcp_servers=[server])

# LangChain 消费 MCP
from langchain_mcp_adapters.client import MultiServerMCPClient
client = MultiServerMCPClient({"my-server": {"command": "...", "args": [...]}})
tools = await client.get_tools()

# Pydantic AI 消费 MCP（需要桥接）
```

详见 [03-mcp 手册](../../../03-mcp/README.md)。

---

## 9. 实战 pattern：混合栈

```
[Pydantic AI / LangGraph 主流程]
  ↓
  ├─ [OpenAI Agents]  // 需要 hosted tools / handoffs
  │     └─ MCP Server  // 内部工具
  │
  └─ [Pydantic AI Agent]  // 类型严格的子任务
```

实战经验：

- **主流程**：用 LangGraph / Pydantic AI（状态机 / 类型）
- **某些子任务**：OpenAI Agents（要 web_search / handoff 路由）
- **内部工具**：MCP Server（一处实现）
- **观测**：Logfire / LangSmith 统一

---

## 10. 互操作的坑

| 坑 | 解 |
|----|----|
| Token usage 在不同框架格式不同 | 统一收口到一个 metrics 系统 |
| 错误异常类型不同 | wrapper 里转一遍 |
| Streaming event 格式不同 | 抽象一层统一事件类型 |
| Session / 历史不互通 | 自己持久化，或者用 MCP 的 prompts |

---

## 11. 完整 demo：OpenAI Agents 调 Pydantic AI 翻译

```python
# demos/integration/04_interop.py
import asyncio
from pydantic_ai import Agent as PydAgent
from agents import Agent, Runner, function_tool


# Pydantic AI 用 Claude 翻译
translator = PydAgent(
    "anthropic:claude-sonnet-4-6",
    system_prompt="把任何文本翻译成英语，只输出译文。",
)


@function_tool
async def translate_to_en(text: str) -> str:
    """翻译到英语"""
    result = await translator.run(text)
    return result.output


# OpenAI Agent 用 GPT 综合
main = Agent(
    name="Editor",
    instructions="""你是编辑助手。
- 用户输入中文 → 先 translate_to_en 翻成英语
- 给英语版本 + 润色建议
""",
    tools=[translate_to_en],
    model="gpt-4o-mini",
)


async def run():
    result = await Runner.run(main, "我想买杯咖啡")
    print(result.final_output)


asyncio.run(run())
```

---

## 12. 何时不混

```
项目刚起步 / 团队小 → 选一家用到底
有明确多框架需求（已有 LangChain 代码 / 团队分工） → 互操作

LangGraph 复杂状态机 + OpenAI hosted tools → 混
单一 chatbot → 别混
```

---

## 13. 下一步

- 📖 实战：vs Pydantic AI / vs LangGraph 完整对比 → [08-practice/05-vs-others.md](../08-practice/05-vs-others.md)
- 📖 生产部署 → [07-production/01-deployment.md](../07-production/01-deployment.md)
- 📖 完整 MCP 章节 → [03-mcp/README.md](../../../03-mcp/README.md)
