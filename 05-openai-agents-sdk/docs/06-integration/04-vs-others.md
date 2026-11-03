# 与 Pydantic AI / LangChain 互操作

> 用了 OpenAI Agents SDK，并不意味着要放弃别的框架。LangChain 的工具可以拿来用，Pydantic AI 的 agent 可以和它互相调用，工具也能互转。这篇看具体怎么接。

---

## 1. 互操作的几个层面

能打通的地方有四层：

1. 工具层：把 LangChain Tool 当 OpenAI Agents 的工具
2. Agent 层：OpenAI Agent 调 Pydantic AI Agent，或者反过来
3. 观测层：共用 LangSmith / Logfire（见 [与 LangSmith / Langfuse / Logfire 集成](./02-observability.md)）
4. 协议层：通过 MCP 互通（见 [MCP 集成：让 Agent 消费 MCP Server](./01-mcp.md)）

---

## 2. LangChain Tool → OpenAI Agents

LangChain 社区的工具很多（SerpAPI、Wolfram、Shell 等），在 OpenAI Agents 里复用的办法是包一层：

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

没有官方的一键桥接包。LangChain 的工具大多最终就是个普通的 Python 可调用对象，包成 `@function_tool` 最稳。更规范的做法是让工具走 MCP，见第 8 节。

---

## 3. Pydantic AI Agent → OpenAI Agents 子工具

```python
from pydantic_ai import Agent as PydAgent
from agents import Agent, function_tool


# Pydantic AI 写的 agent（这里用千问的 OpenAI 兼容端点；换 Claude 就写 "anthropic:claude-sonnet-5-5"）
import os
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

qwen = OpenAIChatModel("qwen-flash", provider=OpenAIProvider(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"]))
pyd_translator = PydAgent(qwen, system_prompt="翻译到英语")


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

主 Agent 用 OpenAI Agents SDK，子任务交给 Pydantic AI，后者可以接 Claude 或千问。`pydantic-ai-slim 2.64` 和 `openai-agents 0.24` 可以装在同一个环境里（两者对 openai 3.x 的要求都满足）。输入中文「我想买杯咖啡」时，主 Agent 调 `translate`，由 Pydantic AI 子 Agent 译成英文。

---

## 4. OpenAI Agent → Pydantic AI 工具

反过来也可以。OpenAI Agents 的 `as_tool()` 只能把它自己的 Agent 变成它自己的 tool，没有导出给别的框架的 API，所以还是自己包：

```python
from agents import Agent, Runner
from pydantic_ai import Agent as PydAgent, RunContext


oai_classifier = Agent(name="Classifier", instructions="分类", output_type=Sentiment)


pyd_main = PydAgent("openai:gpt-6.1-sol", system_prompt="...")


@pyd_main.tool
async def classify(ctx: RunContext[None], text: str) -> str:
    result = await Runner.run(oai_classifier, text)
    return result.final_output.model_dump_json()
# Pydantic AI 主 Agent 调 classify → 里面 Runner.run 一个 OpenAI Agents 的 Agent，把结构化结果转成 JSON 交回
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

LangGraph 负责外层的状态机，具体节点交给 OpenAI Agents 执行，这种搭配挺常见。

---

## 6. 跟 RAG（LlamaIndex / 自己写）集成

把 LlamaIndex 的 query engine 包成 OpenAI Agents 的工具：

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

LangChain 默认上报 LangSmith，OpenAI Agents 默认上报 OpenAI Platform。想让它们进同一个平台：

```python
# 都上 Logfire
import logfire

logfire.configure()
logfire.instrument_openai()          # OpenAI SDK 维度
logfire.instrument_openai_agents()   # Agents SDK 高层
logfire.instrument_pydantic_ai()     # Pydantic AI
# LangChain 没有 logfire.instrument_langchain：它走自己的 callback / OTel 通道（如 LANGSMITH_OTEL_ENABLED），再汇到同一个 OTel 后端
```

或者都上 LangSmith：

```python
# OpenAI Agents 的 OpenAI client 包 wrap_openai
# LangChain 走环境变量
```

观测统一之后，跨框架的调用能在一条 trace 里串起来看。

---

## 8. 跟 MCP 串

MCP 是一个通用的工具协议，哪个框架都能接：

```python
# OpenAI Agents 消费 MCP
async with MCPServerStdio(...) as server:
    oai_agent = Agent(mcp_servers=[server])

# LangChain 消费 MCP（1.4 起内置 langchain.mcp.MCPAdapter；旧的 langchain-mcp-adapters 包也还能用，见 01-langchain 手册）
from langchain_mcp_adapters.client import MultiServerMCPClient
client = MultiServerMCPClient({"my-server": {"command": "...", "args": [...]}})
tools = await client.get_tools()

# Pydantic AI 消费 MCP（原生 MCP 客户端 toolset，见 02-pydantic-ai 手册）
```

详见 [MCP 手册](../../../03-mcp/README.md)。

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

我的分工习惯是：主流程放在 LangGraph 或 Pydantic AI 里，靠它们的状态机和类型约束；需要 web_search 或 handoff 路由的子任务交给 OpenAI Agents；内部工具做成 MCP Server，只实现一次；观测统一到 Logfire 或 LangSmith。

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
import asyncio
from pydantic_ai import Agent as PydAgent
from agents import Agent, Runner, function_tool


# Pydantic AI 用 Claude 翻译（需要 ANTHROPIC_API_KEY；没有这个 key 可以把模型换成千问：
#   PydAgent(OpenAIChatModel("qwen-flash", provider=OpenAIProvider(base_url=..., api_key=...)), ...)）
translator = PydAgent(
    "anthropic:claude-sonnet-5-5",
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
    model="gpt-6-luna",
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

- 三个框架的完整对比 → [横向对比：OpenAI Agents SDK vs Pydantic AI vs LangGraph](../08-practice/05-vs-others.md)
- 生产部署 → [部署形态选型](../07-production/01-deployment.md)
- 完整 MCP 章节 → [MCP 手册](../../../03-mcp/README.md)
