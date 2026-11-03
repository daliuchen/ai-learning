# LangGraph 04：ReAct Agent（create_agent + 手写版）

> ReAct 的流程是：LLM 读消息，决定调哪个工具，看到工具结果后再决定下一步，如此循环，直到给出最终答案。现在官方的写法是一行 `langchain.agents.create_agent`，它返回的就是一张 LangGraph 图。日常用这一行就够了，但要弄懂它在做什么，最好亲手写一遍。这篇两种都讲。

版本：以 `langchain 1.4.8`、`langgraph 1.2.19` 为准；`create_react_agent` 已弃用，改用 `create_agent`（`system_prompt=` + middleware）。

---

## 1. 一行预编译版

```python
import os
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city} 晴 25℃"

model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash"))
agent = create_agent(model, [get_weather])

out = agent.invoke({"messages": [("human", "北京天气")]})
print(out["messages"][-1].content)
```

`create_agent` 的主要参数：

```python
create_agent(
    model,                    # 模型实例，或 "openai:gpt-6.1-sol" 这样的字符串
    tools,
    system_prompt="...",      # 系统提示（str 或 SystemMessage）
    middleware=[...],         # 中间件：动态提示、摘要、人工审批、重试……
    response_format=...,      # 结构化输出
    state_schema=...,         # 自定义 State（继承 AgentState）
    context_schema=...,       # 运行时上下文类型
    checkpointer=...,         # 持久化（HITL 必备）
    store=...,
    interrupt_before=..., interrupt_after=...,   # 静态断点，调试用
    name="my-agent",
)
```

### 1.1 注入 system prompt

```python
agent = create_agent(model, tools, system_prompt="你是一位严谨的助理，只用中文回答。")
```

要根据运行时信息动态拼提示，用 `@dynamic_prompt` 中间件：

```python
from dataclasses import dataclass
from langchain.agents.middleware import dynamic_prompt, ModelRequest

@dataclass
class Ctx:
    role: str

@dynamic_prompt
def role_prompt(request: ModelRequest) -> str:
    return f"你是 {request.runtime.context.role} 的助理"

agent = create_agent(model, tools, middleware=[role_prompt], context_schema=Ctx)
agent.invoke({"messages": [("human", "...")]}, context=Ctx(role="财务"))
```

这个系统提示在每次调模型前临时生成，不会写进 state。

### 1.2 流式

```python
for chunk in agent.stream({"messages": [("human", "...")]}, stream_mode="updates"):
    print(chunk)
```

`stream_mode` 常用的几种是 `values / updates / messages / custom / debug`，07 篇会逐个讲。

> `create_agent` 里的节点名是 `model` 和 `tools`，旧的 `create_react_agent` 叫 `agent` 和 `tools`，按节点名过滤流式输出时别写错。

---

## 2. 手写 ReAct（理解原理）

```python
import os
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
model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash")).bind_tools(tools)

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

`create_agent` 的主循环就是这几十行，只是去掉了结构化输出、middleware 钩子和错误处理这些细节。

---

## 3. 使用 ToolNode prebuilt

`tool_node` 不必手写，LangGraph 提供了 `ToolNode`：

```python
from langgraph.prebuilt import ToolNode

tool_node = ToolNode(tools)
g.add_node("tools", tool_node)
```

`ToolNode` 内部做的就是上面那个 for 循环，外加错误处理和异步支持。

条件函数也有现成的 `tools_condition`：

```python
from langgraph.prebuilt import tools_condition

g.add_conditional_edges("agent", tools_condition)
```

它检查最后一条消息有没有 `tool_calls`，有就走 `"tools"` 节点，没有就到 END。

---

## 4. 加 system prompt 的两种方式

### 4.1 在 state 里塞 SystemMessage

```python
def agent_node(state):
    msgs = [SystemMessage(content="你是助手")] + state["messages"]
    return {"messages": [model.invoke(msgs)]}
```

这里的 SystemMessage 是调用前临时拼上的，节点只返回模型的回复，SystemMessage 不会写进 state。要是把它放进节点返回值或每轮的输入里，它才会进 state，而且越积越多。

### 4.2 用 system_prompt（不修改 state）

```python
agent = create_agent(model, tools, system_prompt="你是助手")
```

效果和 4.1 一样：state 里不存 SystemMessage，每次调用 LLM 前临时拼上，只是不用自己写节点。已弃用的 `create_react_agent` 里的 `state_modifier=` / `prompt=` 对应的就是这个参数。

---

## 5. 给 ReAct 加自定义状态

假设除了 messages，还想在 state 里记一下"是否已经查过天气"：

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

用 `create_agent` 时要继承 `AgentState`（不支持 Pydantic）：

```python
from typing_extensions import NotRequired
from langchain.agents import create_agent, AgentState

class MyState(AgentState):
    weather_checked: NotRequired[bool]

agent = create_agent(model, tools, state_schema=MyState)
```

---

## 6. 工具运行时配置注入

工具里想拿到当前 user_id、当前 state 或 store，就用 `ToolRuntime`。它是一个对 LLM 不可见的注入参数：

```python
from dataclasses import dataclass
from langchain.agents import create_agent
from langchain.tools import tool, ToolRuntime

@dataclass
class Ctx:
    user_id: str

@tool
def query_my_orders(keyword: str, runtime: ToolRuntime[Ctx]) -> str:
    """查询当前用户订单"""
    # runtime.context / runtime.state / runtime.store / runtime.config / runtime.tool_call_id
    return f"[user={runtime.context.user_id}] 关键词={keyword}"

agent = create_agent(model, [query_my_orders], context_schema=Ctx)
agent.invoke(
    {"messages": [("human", "我有哪些 iPhone 订单？")]},
    context=Ctx(user_id="u_123"),
)
```

LLM 看到的工具参数只有 `keyword`，`runtime` 不会出现在 schema 里。老写法 `Annotated[RunnableConfig, InjectedToolArg]` 加 `config["configurable"]` 仍然能用，新代码建议换成 `ToolRuntime`。

---

## 7. 异步与流式

```python
async for ev in agent.astream_events({"messages": [...]}, version="v2"):
    if ev["event"] == "on_chat_model_stream":
        print(ev["data"]["chunk"].content, end="", flush=True)
```

LangGraph 自带 `astream_events`，所有节点的事件都会吐出来。新代码更推荐用 `stream_mode`；`stream(..., version="v2")` 返回带 `type` / `ns` / `data` 的统一事件字典，细节见 [LangGraph 07：Streaming 的各种模式](./07-streaming.md)。

`stream_mode="messages"` 是 LangGraph 专有的细粒度消息流：

```python
async for token, metadata in agent.astream({"messages": [...]}, stream_mode="messages"):
    print(token.content, end="")
```

---

## 8. 完整 demo

完整代码：[`demos/langgraph/04_react.py`](../../demos/langgraph/04_react.py)：包含 `create_agent` 与手写两版。

```python
# demos/langgraph/04_react.py（节选）
import os
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv()

@tool
def get_weather(city: str) -> str:
    """查询城市天气"""
    return f"{city} 晴 25℃"

@tool
def calc(expr: str) -> str:
    """计算数学表达式"""
    return str(eval(expr, {"__builtins__": {}}, {}))   # 仅演示；生产不要 eval 用户输入

agent = create_agent(
    ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash")),
    [get_weather, calc],
    system_prompt="你是一位严谨的助手，思考前先理清问题",
)

print("\n--- invoke ---")
out = agent.invoke({"messages": [("human", "北京天气？再算 (3+5)*2")]})
for m in out["messages"]:
    print(type(m).__name__, ":", m.content if m.content else m.tool_calls)

print("\n--- stream updates ---")
for chunk in agent.stream({"messages": [("human", "上海天气")]}, stream_mode="updates"):
    print(chunk)
```

> 用千问（或任何 OpenAI 兼容服务）时，设好 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。有个坑：shell 里如果还留着旧的 `OPENAI_API_BASE`，`langchain-openai` 会优先用它，结果是 401，先 `unset OPENAI_API_BASE` 再跑。

---

## 9. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| LLM 不调工具 | 工具 docstring 写得不清楚 | 把 description 写具体 |
| 反复调工具停不下来 | LLM 没拿到结果，或结果异常 | 检查 ToolMessage 有没有正确回填 |
| 工具异常打断 graph | 工具里抛的普通异常默认直接抛出、中断运行（`create_agent` 和默认 `ToolNode` 都是这样） | 手写图时用 `ToolNode(handle_tool_errors=True)`（错误文本回给模型）；`create_agent` 里挂 `ToolErrorMiddleware`，或在工具内部自己 try/except |
| `ToolRuntime.context` 为 None | 没声明 `context_schema`，或调用时没传 `context=` | 两处都要配 |
| 想改循环形状 | `create_agent` 的循环形状固定，middleware 只能插钩子 | 手写 graph |
| `from langchain import create_agent` 报错 | 这个导入路径不存在 | `from langchain.agents import create_agent` |

---

下一篇：[LangGraph 05：持久化 Checkpointer 与跨会话记忆](05-persistence.md)
