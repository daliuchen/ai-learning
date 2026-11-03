# 横向对比：OpenAI Agents SDK vs Pydantic AI vs LangGraph

> 三个框架都能写 Agent，思路却不一样：OpenAI Agents SDK 原语少，和 OpenAI 生态绑得紧；Pydantic AI 强调类型安全，不挑模型厂商；LangGraph 用图和状态机来组织流程。这篇用同一个客服 Agent，三家各写一遍，放在一起比。

---

## 1. 共同任务

三家实现的是同一个客服 Agent：Triage 把请求分给 Billing / Support / Sales，每个专家带 1-2 个工具，支持多轮对话（session），最后用同一份 evalset 评测。

---

## 2. OpenAI Agents SDK 写法

```python
from agents import Agent, Runner, function_tool, SQLiteSession


@function_tool
def lookup_invoice(order_id: str) -> str:
    return f"Order {order_id}: $99"


@function_tool
def search_kb(query: str) -> str:
    return f"KB: {query} solution"


billing = Agent(
    name="Billing",
    instructions="账单专员",
    tools=[lookup_invoice],
    model="gpt-6-luna",
)


support = Agent(
    name="Support",
    instructions="技术支持",
    tools=[search_kb],
    model="gpt-6-luna",
)


triage = Agent(
    name="Triage",
    instructions="分流到 Billing / Support",
    handoffs=[billing, support],
    model="gpt-6-luna",
)


async def chat(msg: str, user_id: str):
    session = SQLiteSession(user_id)
    result = await Runner.run(triage, msg, session=session)
    return {"reply": result.final_output, "by": result.last_agent.name}
```

不算空行大约 30 行。Handoffs 是框架内置的概念，分流写起来最直接。

---

## 3. Pydantic AI 写法

```python
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage


billing = Agent(
    "openai:gpt-6-luna",
    system_prompt="账单专员",
)


@billing.tool
def lookup_invoice(ctx, order_id: str) -> str:
    return f"Order {order_id}: $99"


support = Agent(
    "openai:gpt-6-luna",
    system_prompt="技术支持",
)


@support.tool
def search_kb(ctx, query: str) -> str:
    return f"KB: {query}"


triage = Agent(
    "openai:gpt-6-luna",
    system_prompt="判断主题。Billing / Support / OTHER。只返回名字。",
)


_history: dict[str, list[ModelMessage]] = {}


async def chat(msg: str, user_id: str):
    history = _history.get(user_id, [])

    # 1. Triage 判断
    triage_result = await triage.run(msg, message_history=history)
    intent = triage_result.output.strip()

    # 2. 路由
    if "Billing" in intent:
        result = await billing.run(msg, message_history=history)
        by = "Billing"
    elif "Support" in intent:
        result = await support.run(msg, message_history=history)
        by = "Support"
    else:
        result = triage_result
        by = "Triage"

    _history[user_id] = result.all_messages()
    return {"reply": result.output, "by": by}
```

不算空行大约 40 行。路由要自己写，逻辑更显式，代价是代码多一些；好处是类型检查严格。

---

## 4. LangGraph 写法

```python
from typing import Annotated, TypedDict
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages


@tool
def lookup_invoice(order_id: str) -> str:
    return f"Order {order_id}: $99"


@tool
def search_kb(query: str) -> str:
    return f"KB: {query}"


llm = ChatOpenAI(model="gpt-6-luna")


billing_agent = create_agent(llm, [lookup_invoice], system_prompt="账单专员")
support_agent = create_agent(llm, [search_kb], system_prompt="技术支持")


class State(TypedDict):
    messages: Annotated[list, add_messages]      # 每轮的新消息追加进历史，而不是覆盖
    intent: str
    output: str


def triage_node(state):
    resp = llm.invoke([
        ("system", "判断主题。返回 Billing / Support / OTHER"),
        ("user", state["messages"][-1].content),
    ])
    return {"intent": resp.content.strip()}


def billing_node(state):
    out = billing_agent.invoke({"messages": state["messages"]})
    return {"output": out["messages"][-1].content}


def support_node(state):
    out = support_agent.invoke({"messages": state["messages"]})
    return {"output": out["messages"][-1].content}


def router(state):
    if "Billing" in state["intent"]:
        return "billing"
    elif "Support" in state["intent"]:
        return "support"
    return END


workflow = StateGraph(State)
workflow.add_node("triage", triage_node)
workflow.add_node("billing", billing_node)
workflow.add_node("support", support_node)

workflow.add_edge(START, "triage")
workflow.add_conditional_edges("triage", router, ["billing", "support", END])
workflow.add_edge("billing", END)
workflow.add_edge("support", END)

graph = workflow.compile(checkpointer=InMemorySaver())     # 有 checkpointer，thread_id 才能续上历史


async def chat(msg: str, user_id: str):
    # session 用 LangGraph checkpoint
    config = {"configurable": {"thread_id": user_id}}
    result = await graph.ainvoke({"messages": [("user", msg)]}, config=config)
    return {"reply": result["output"]}
```

不算空行大约 50 行。每一次状态转移都写在图里，控制最精细，但起步的样板代码也最多。

---

## 5. 横向对比表

| 维度 | OpenAI Agents | Pydantic AI | LangGraph |
|------|---|---|---|
| 代码量（上面三段，不算空行） | 约 30 行 | 约 40 行 | 约 50 行 |
| 学习曲线 | 平 | 平 | 较陡 |
| 多 Agent | Handoffs 原生支持 | 手动调 | 图节点 |
| 类型安全 | 中 | 最强 | 弱 |
| 状态机 | 弱 | 弱 | 最强 |
| Hosted Tools | 有（web_search 等） | 有（Native Tools，provider 原生工具） | 无 |
| 多 Provider | 兼容端点（Chat Completions）/ Any-LLM / LiteLLM（0.24 与 litellm 有 `openai` 版本冲突，见 [Multi-provider：接千问 / Claude / Gemini / 本地模型](../05-advanced/04-multi-provider.md)） | 原生支持 | 原生支持 |
| 观测 | OpenAI Dashboard 开箱（需 OpenAI key）+ 30+ 第三方处理器 | Logfire | LangSmith |
| Streaming | 支持 | 支持 | 支持 |
| Realtime/Voice | 支持 | 弱 | 弱 |
| 持久化 | Sessions（内置 SQLite / Redis / SQLAlchemy / Mongo / 加密 / 压缩…）+ `RunState` 可序列化续跑 | message_history（另有耐久执行集成） | Checkpoint，最强 |
| Human-in-Loop | `needs_approval` + `RunState`（原生，兼容端点上也可用） | Deferred Tools + `requires_approval`（原生，见 [Pydantic AI 进阶 08：Deferred Tools 与人审批](../../../02-pydantic-ai/docs/03-advanced/08-deferred-tools.md)） | interrupt 是一等公民 |
| 测试替身 | `agents.testing`（假模型 / 假 Realtime / 假 Voice） | `TestModel` / `FunctionModel` | 自己 mock |
| 生态 | OpenAI 官方支持 | Pydantic / 社区 | LangChain 系大 |

---

## 6. 推荐决策树

```
用 OpenAI Agents SDK 如果：
├─ 跟 OpenAI 生态绑定
├─ 需要 web_search / file_search / Realtime（这几样只在 OpenAI Responses 模型上可用）
├─ 客服分流类场景（Triage / Handoffs）
└─ 想要"最少配置最快出活"

用 Pydantic AI 如果：
├─ 类型安全是硬约束
├─ 多 provider 平等支持
├─ 已经熟 Pydantic
└─ 单 agent 流程多

用 LangGraph 如果：
├─ 复杂状态机 / 长流程
├─ 需要 checkpoint / 时间旅行
├─ 并发 / fanout / map-reduce
├─ Human-in-Loop 重要
└─ 已在 LangChain 生态
```

---

## 7. 真实场景如何选

### 场景 A：电商客服

以分流为主，Handoffs 用起来很自然，选 OpenAI Agents SDK。如果全用 OpenAI 模型、还要 web_search 查最新政策，就更合适了。

### 场景 B：财务数据分析 Agent

金额和货币精度要求类型严格，选 Pydantic AI。这类任务主要是单个 Agent 跑 SQL 加分析，也用不到 Realtime。

### 场景 C：复杂工作流（合同审核）

流程多步、要能暂停、要人工介入，checkpoint 和时间旅行是刚需，还要多个 Agent 并行审不同段落，选 LangGraph。

### 场景 D：研究助手

web_search 是硬需求，选 OpenAI Agents SDK；也可以用 LangGraph 做主流程，把研究节点交给 OpenAI Agents。

---

## 8. 混搭也常见

```
[LangGraph 主流程]
  ↓ 工作流引擎
  ├─ [OpenAI Agents SDK 节点]
  │    ├─ Handoffs
  │    └─ Hosted Tools
  ↓
  └─ [Pydantic AI 节点]
       └─ 严格类型抽取
```

具体接法见 [与 Pydantic AI / LangChain 互操作](../06-integration/04-vs-others.md)。

---

## 9. 跨手册关联

- 完整 LangChain / LangGraph → [01-langchain 手册](../../../01-langchain/README.md)
- 完整 Pydantic AI → [02-pydantic-ai 手册](../../../02-pydantic-ai/README.md)
- MCP（三家都能消费）→ [03-mcp 手册](../../../03-mcp/README.md)
- Prompt 工程（三家通用）→ [04-prompt-engineering 手册](../../../04-prompt-engineering/README.md)

---

## 10. 总结

OpenAI Agents SDK 适合用 OpenAI 模型、需要 handoff 分流的场景；Pydantic AI 适合重视类型、要换着用多家模型的场景；LangGraph 适合流程复杂、需要精细控制状态的场景。选哪个，先看手上的任务属于哪一类。

---

## 11. 读完这本手册

这 38 篇覆盖的内容，大致对应下面这些事：

- 写一个带工具的 Agent
- 用 Handoffs 做多 Agent 路由
- 用 Guardrails（含 tool guardrails）和 `needs_approval` 人工审批做防御
- 用 OpenAI 的 Hosted Tools
- 接千问等 OpenAI 兼容端点，通过 Any-LLM / LiteLLM 跑 Claude / Gemini
- 用 Realtime / Voice
- 接 MCP Server
- 接观测平台（Logfire / Langfuse / LangSmith）
- 部署到 FastAPI / Lambda
- 做评测、迭代和生产化

---

## 12. 下一步

- OpenAI Agents SDK 官方 docs：https://openai.github.io/openai-agents-python/
- 跟 PE 手册的方法论结合 → [Prompt Engineering 手册流程篇](../../../04-prompt-engineering/docs/02-process/)
- 用 Claude Code 自动迭代 Agent → [PE Practice 03：用 Claude Code 当 Prompt 优化器](../../../04-prompt-engineering/docs/08-practice/03-claude-code-as-optimizer.md)
