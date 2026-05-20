# LangGraph 04：ReAct Agent（create_react_agent + 手写版）

> **一句话**：ReAct = "LLM 看消息 → 决定调工具 → 看到工具结果 → 再决定 → ... → 输出最终答案"。LangGraph 内置 `create_react_agent` 一行代码完事；理解原理则要会手写。

---

## 1. 一行预编译版

```python
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city} 晴 25℃"

agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), [get_weather])

out = agent.invoke({"messages": [("human", "北京天气")]})
print(out["messages"][-1].content)
```

`create_react_agent` 还接受：

```python
create_react_agent(
    model,
    tools,
    state_schema=...,         # 自定义 State
    state_modifier="...",     # 注入 system prompt
    checkpointer=...,         # 持久化（HITL 必备）
    interrupt_before=...,
    interrupt_after=...,
    debug=True,
)
```

### 1.1 注入 system prompt

```python
agent = create_react_agent(
    model, tools,
    state_modifier="你是一位严谨的助理，只用中文回答。",
)
```

或者 function：

```python
def modifier(state):
    return [SystemMessage(content=f"你是 {state['role']} 的助理"), *state["messages"]]

agent = create_react_agent(model, tools, state_modifier=modifier)
```

### 1.2 流式

```python
for chunk in agent.stream({"messages": [("human", "...")]}, stream_mode="updates"):
    print(chunk)
```

`stream_mode` 五种：`values / updates / messages / debug / custom`，详见 07 篇。

---

## 2. 手写 ReAct（理解原理）

```python
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import BaseMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city} 晴 25℃"

@tool
def calc(expr: str) -> str:
    """算数"""
    return str(eval(expr, {"__builtins__": {}}, {}))

tools = [get_weather, calc]
by_name = {t.name: t for t in tools}
model = ChatOpenAI(model="gpt-4o-mini").bind_tools(tools)

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

def agent_node(state):
    return {"messages": [model.invoke(state["messages"])]}

def tool_node(state):
    last = state["messages"][-1]
    outs = []
    for call in last.tool_calls:
        result = by_name[call["name"]].invoke(call["args"])
        outs.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
    return {"messages": outs}

def router(state) -> str:
    return "tools" if state["messages"][-1].tool_calls else END

g = StateGraph(State)
g.add_node("agent", agent_node)
g.add_node("tools", tool_node)
g.add_edge(START, "agent")
g.add_conditional_edges("agent", router, {"tools": "tools", END: END})
g.add_edge("tools", "agent")

app = g.compile()
print(app.invoke({"messages": [("human", "北京天气？再算 (3+5)*2")]})["messages"][-1].content)
```

这就是 `create_react_agent` 的核心逻辑（删去了一些 prebuilt 的细节如错误处理、消息修饰）。

---

## 3. 使用 ToolNode prebuilt

`tool_node` 不用手写，LangGraph 提供 `ToolNode`：

```python
from langgraph.prebuilt import ToolNode

tool_node = ToolNode(tools)
g.add_node("tools", tool_node)
```

`ToolNode` 内部就是上面的 for 循环 + 错误处理 + 异步支持。

也有现成的条件函数 `tools_condition`：

```python
from langgraph.prebuilt import tools_condition

g.add_conditional_edges("agent", tools_condition)
```

`tools_condition` 检查最后一条消息有无 `tool_calls`，有就走 `"tools"` 节点，无就 END。

---

## 4. 加 system prompt 的两种方式

### 4.1 在 state 里塞 SystemMessage

```python
def agent_node(state):
    msgs = [SystemMessage(content="你是助手")] + state["messages"]
    return {"messages": [model.invoke(msgs)]}
```

这会让 SystemMessage 进入 state，缺点是历史会越积越多。

### 4.2 用 state_modifier（不修改 state）

```python
agent = create_react_agent(
    model, tools,
    state_modifier=lambda state: [SystemMessage("你是助手"), *state["messages"]],
)
```

state 里不存 SystemMessage，每次调用 LLM 前临时拼。

---

## 5. 给 ReAct 加自定义状态

需求：除了 messages，还要在 state 里存"是否已查过天气"。

```python
class State(MessagesState):
    weather_checked: bool

def agent_node(state):
    return {"messages": [model.invoke(state["messages"])]}

def tool_node(state):
    last = state["messages"][-1]
    outs = []
    weather = state.get("weather_checked", False)
    for call in last.tool_calls:
        result = by_name[call["name"]].invoke(call["args"])
        outs.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
        if call["name"] == "get_weather":
            weather = True
    return {"messages": outs, "weather_checked": weather}
```

`create_react_agent` 也支持自定义 schema：

```python
agent = create_react_agent(model, tools, state_schema=State)
```

---

## 6. 工具运行时配置注入

希望工具拿到当前 user_id / db connection：

```python
from typing_extensions import Annotated
from langchain_core.tools import InjectedToolArg
from langgraph.prebuilt.chat_agent_executor import AgentState
from langchain_core.runnables import RunnableConfig

@tool
def query_my_orders(
    keyword: str,
    config: Annotated[RunnableConfig, InjectedToolArg],
) -> str:
    """查询当前用户订单"""
    user_id = config["configurable"]["user_id"]
    return f"[user={user_id}] 关键词={keyword}"

agent = create_react_agent(model, [query_my_orders])
agent.invoke(
    {"messages": [("human", "我有哪些 iPhone 订单？")]},
    config={"configurable": {"user_id": "u_123"}},
)
```

工具能拿到 `config`，但 LLM 看不到 `config` 这个参数（因为是 InjectedToolArg）。

---

## 7. 异步与流式

```python
async for ev in agent.astream_events({"messages": [...]}, version="v2"):
    if ev["event"] == "on_chat_model_stream":
        print(ev["data"]["chunk"].content, end="", flush=True)
```

LangGraph 自带 `astream_events`，所有节点的事件都吐。

`stream_mode="messages"` 是 LangGraph 专属的细粒度消息流：

```python
async for token, metadata in agent.astream({"messages": [...]}, stream_mode="messages"):
    print(token.content, end="")
```

---

## 8. 完整 demo

```python
# demos/langgraph/04_react.py
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

load_dotenv()

@tool
def get_weather(city: str) -> str:
    """查询城市天气"""
    return f"{city} 晴 25℃"

@tool
def calc(expr: str) -> str:
    """计算数学表达式"""
    return str(eval(expr, {"__builtins__": {}}, {}))

agent = create_react_agent(
    ChatOpenAI(model="gpt-4o-mini"),
    [get_weather, calc],
    state_modifier="你是一位严谨的助手，思考前先理清问题",
)

print("\n--- invoke ---")
out = agent.invoke({"messages": [("human", "北京天气？再算 (3+5)*2")]})
for m in out["messages"]:
    print(type(m).__name__, ":", m.content if m.content else m.tool_calls)

print("\n--- stream updates ---")
for chunk in agent.stream({"messages": [("human", "上海天气")]}, stream_mode="updates"):
    print(chunk)
```

---

## 9. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| LLM 不调工具 | 工具 docstring 不清晰 | 完善 description |
| 死循环刷工具 | LLM 没拿到结果或结果异常 | 检查 ToolMessage 是否成功回填 |
| 工具异常打断 graph | 默认 raise | `@tool(handle_tool_errors=True)` 或 `ToolNode(handle_tool_errors=True)` |
| InjectedToolArg 工具 | 同步 invoke 没传 config | 必须 `config={"configurable": {...}}` |
| 用 prebuilt 但想改循环 | prebuilt 是黑盒 | 手写 graph |

---

## 10. 本章 demo

[`demos/langgraph/04_react.py`](../../demos/langgraph/04_react.py)：包含 prebuilt 与手写两版。

下一篇：[05-persistence.md](05-persistence.md)
