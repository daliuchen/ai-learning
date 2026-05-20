# 横向对比：OpenAI Agents SDK vs Pydantic AI vs LangGraph

> **一句话**：三家都能写 Agent 但风格各异——OpenAI Agents 是"最小原语 + OpenAI 生态"，Pydantic AI 是"类型安全 + 框架无关"，LangGraph 是"图引擎 + 状态机"。本篇用同一个客服 Agent 用三家分别写出来对比。

---

## 1. 共同任务

同一个客服 Agent：

- Triage 分流到 Billing / Support / Sales
- 每个专家有 1-2 个 tool
- 支持多轮对话（session）
- 评测：同一份 evalset 跑三家

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
    model="gpt-4o-mini",
)


support = Agent(
    name="Support",
    instructions="技术支持",
    tools=[search_kb],
    model="gpt-4o-mini",
)


triage = Agent(
    name="Triage",
    instructions="分流到 Billing / Support",
    handoffs=[billing, support],
    model="gpt-4o-mini",
)


async def chat(msg: str, user_id: str):
    session = SQLiteSession(user_id)
    result = await Runner.run(triage, msg, session=session)
    return {"reply": result.final_output, "by": result.last_agent.name}
```

代码量：**~25 行**。Handoffs 是一等公民，最直接。

---

## 3. Pydantic AI 写法

```python
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage


billing = Agent(
    "openai:gpt-4o-mini",
    system_prompt="账单专员",
)


@billing.tool
def lookup_invoice(ctx, order_id: str) -> str:
    return f"Order {order_id}: $99"


support = Agent(
    "openai:gpt-4o-mini",
    system_prompt="技术支持",
)


@support.tool
def search_kb(ctx, query: str) -> str:
    return f"KB: {query}"


triage = Agent(
    "openai:gpt-4o-mini",
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

代码量：**~40 行**。Handoffs 要自己路由——更显式但代码多。优势是**类型严格**。

---

## 4. LangGraph 写法

```python
from langgraph.graph import StateGraph, END, START
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from typing import TypedDict


@tool
def lookup_invoice(order_id: str) -> str:
    return f"Order {order_id}: $99"


@tool
def search_kb(query: str) -> str:
    return f"KB: {query}"


llm = ChatOpenAI(model="gpt-4o-mini")


billing_agent = create_react_agent(llm, [lookup_invoice], prompt="账单专员")
support_agent = create_react_agent(llm, [search_kb], prompt="技术支持")


class State(TypedDict):
    messages: list
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

graph = workflow.compile()


async def chat(msg: str, user_id: str):
    # session 用 LangGraph checkpoint
    config = {"configurable": {"thread_id": user_id}}
    result = await graph.ainvoke({"messages": [("user", msg)]}, config=config)
    return {"reply": result["output"]}
```

代码量：**~60 行**。图状态机最显式——能精确控制每个状态转移，但启动复杂。

---

## 5. 横向对比表

| 维度 | OpenAI Agents | Pydantic AI | LangGraph |
|------|---|---|---|
| **代码量** | 25 行 | 40 行 | 60 行 |
| **学习曲线** | 平 | 平 | 较陡 |
| **多 Agent** | ✅ Handoffs 原生 | 手动调 | ✅ 图节点 |
| **类型安全** | 中 | ✅ 最强 | 弱 |
| **状态机** | 弱 | 弱 | ✅ 最强 |
| **Hosted Tools** | ✅ web_search 等 | ❌ | ❌ |
| **多 Provider** | LiteLLM 适配 | ✅ 原生 | ✅ 原生 |
| **观测** | ✅ OpenAI Dashboard 开箱 | Logfire | LangSmith |
| **Streaming** | ✅ | ✅ | ✅ |
| **Realtime/Voice** | ✅ | 弱 | 弱 |
| **持久化** | Sessions（简） | message_history | ✅ Checkpoint 最强 |
| **Human-in-Loop** | 通过 handoff | 自己写 | ✅ interrupt 一等公民 |
| **生态** | OpenAI 官方支持 | Pydantic / 社区 | LangChain 系大 |

---

## 6. 推荐决策树

```
用 OpenAI Agents SDK 如果：
├─ 跟 OpenAI 生态绑定
├─ 需要 web_search / file_search / Realtime
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

- Triage 分流为主 → **OpenAI Agents SDK**（Handoffs 自然）
- 全用 OpenAI 模型 → 加分
- 要 web_search 查最新政策 → 加分

### 场景 B：财务数据分析 Agent

- 类型严格（金额 / 货币精度）→ **Pydantic AI**
- 主要单 Agent 跑 SQL + 分析
- 不需要 Realtime

### 场景 C：复杂工作流（合同审核）

- 多步骤、可暂停、人工介入 → **LangGraph**
- Checkpoint / 时间旅行刚需
- 多 Agent 并行审不同段

### 场景 D：研究助手

- web_search 是硬需求 → **OpenAI Agents SDK**
- 或者：LangGraph 主流程 + OpenAI Agents 做研究节点

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

详见 [06-integration/04-vs-others.md](../06-integration/04-vs-others.md)。

---

## 9. 跨手册关联

- 完整 LangChain / LangGraph → [01-langchain 手册](../../../01-langchain/README.md)
- 完整 Pydantic AI → [02-pydantic-ai 手册](../../../02-pydantic-ai/README.md)
- MCP（三家都能消费）→ [03-mcp 手册](../../../03-mcp/README.md)
- Prompt 工程（三家通用）→ [04-prompt-engineering 手册](../../../04-prompt-engineering/README.md)

---

## 10. 一句话总结

- **OpenAI Agents SDK**：跟 OpenAI 玩，handoffs 顺手
- **Pydantic AI**：跟类型玩，多 provider 顺手
- **LangGraph**：跟图玩，复杂工作流顺手

没有"最好"，看你**任务的形状**像哪个。

---

## 11. 全本手册完结

走完这 38 篇你应该具备：

- ✅ 写一个 Agent + 工具
- ✅ 用 Handoffs 做多 Agent 路由
- ✅ 用 Guardrails 防御
- ✅ 用 Hosted Tools（OpenAI 独门）
- ✅ 接 LiteLLM 跑 Claude / Gemini
- ✅ 用 Realtime / Voice
- ✅ 接 MCP Server
- ✅ 接观测平台（Logfire / Langfuse / LangSmith）
- ✅ 部署到 FastAPI / Lambda
- ✅ 评测 + 迭代 + 生产化

去做点真事吧。

---

## 12. 下一步

- 📖 OpenAI Agents SDK 官方 docs：https://openai.github.io/openai-agents-python/
- 📖 跟 PE 手册的方法论结合 → [04-prompt-engineering/02-process](../../../04-prompt-engineering/docs/02-process/)
- 📖 用 Claude Code 自动迭代 Agent → [04-prompt-engineering/08-practice/03-claude-code-as-optimizer.md](../../../04-prompt-engineering/docs/08-practice/03-claude-code-as-optimizer.md)
