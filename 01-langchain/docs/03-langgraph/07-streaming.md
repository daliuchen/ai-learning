# LangGraph 07：Streaming 的各种模式

> LangGraph 的 `stream` 有好几种 mode：`values` 给完整状态，`updates` 给增量，`messages` 给 Token 流，`custom` 给自定义事件，`debug` 给调试细节，另外还有 `checkpoints` 和 `tasks`，而且可以组合使用。LangChain 的 `astream_events` 在这里照样能用，不过原生 stream 分得更细。这篇逐个过一遍，最后讲怎么和 HITL 配合。

版本：以 `langgraph 1.2.19` 为准。`stream` / `astream` 默认输出 `v1` 格式（下文都按它写），传 `version="v2"` 后每个 chunk 统一成带 `type` 的字典。

---

## 1. 常用的五种 stream_mode

| mode | 每次产出 | 何时用 |
|------|----------|--------|
| `"values"` | 每步完整 State | 看整体演进 |
| `"updates"` | 每步 partial（{node: partial}） | 看每个 node 做了什么 |
| `"messages"` | (token, metadata) tuple | LLM token 级流（UI 输出） |
| `"debug"` | 详细调试事件 | 排查 |
| `"custom"` | 节点内 `stream_writer` 推出的事件 | 自定义业务事件 |

可以同时传多个：

```python
for mode, ev in app.stream(input, stream_mode=["updates", "messages"]):
    print(mode, ev)
```

元组的第一项是 mode 名。用 `version="v2"` 的话，每个 chunk 都是带 `type` 的字典：

```python
for ev in app.stream(input, stream_mode=["updates", "custom"], version="v2"):
    print(ev["type"], ev["ns"], ev["data"])    # ns 是子图命名空间，顶层图为 ()
```

---

## 2. values：每步完整 State

```python
for state in app.stream(x, stream_mode="values"):
    print(state)
```

每次产出当前 State 的全量快照，所有字段都在里面。拿来做进度展示很直观，缺点是数据量大。

---

## 3. updates：每步增量

```python
for ev in app.stream(x, stream_mode="updates"):
    # ev = {"node_name": partial_state}
    print(ev)
```

输出大致是这样：

```
{'plan': {'plan': '1. step ... 2. ...'}}
{'tools': {'messages': [ToolMessage(...)]}}
{'agent': {'messages': [AIMessage(...)]}}
```

这是我用得最多的 mode。前端要显示"AI 正在规划 / 正在调工具 / 正在生成"这类状态，用它就够了。

---

## 4. messages：Token 级流

```python
for token, metadata in app.stream(x, stream_mode="messages"):
    print(token.content, end="", flush=True)
```

`token` 是 `AIMessageChunk`，`metadata` 里有 `tags`、`name`、`langgraph_node` 等字段。根据 `metadata.langgraph_node` 能分辨是哪个 node 在输出 token，比如区分规划阶段和最终回答。

异步写法：

```python
async for token, metadata in app.astream(x, stream_mode="messages"):
    ...
```

---

## 5. debug：调试事件

```python
for ev in app.stream(x, stream_mode="debug"):
    print(ev["type"], ev["payload"])
```

事件类型有 `task` 和 `task_result`，挂了 checkpointer 时还有 `checkpoint`。`task` 的 payload 包含 `id / name / input / triggers`，`task_result` 包含 `result / error / interrupts`。每个 node 开始和结束各产生一条事件，事件本身带 `step` 和 `timestamp`，前后相减就能算出每个 node 的耗时。排查卡顿时，打开它看耗时分布最直接。

---

## 6. custom：从节点内部 emit 事件

```python
from langgraph.config import get_stream_writer

def plan(state):
    writer = get_stream_writer()
    writer({"event": "planning_started"})
    plan_text = ...
    writer({"event": "planning_done", "preview": plan_text[:100]})
    return {"plan": plan_text}

for ev in app.stream(x, stream_mode="custom"):
    print(ev)
```

业务事件走 custom，比塞进 metadata 干净。

---

## 7. astream_events（兼容 LCEL）

LangChain 的 `astream_events(version="v2")` 在 LangGraph 上同样能用，能拿到 `on_chain_*`、`on_chat_model_*`、`on_tool_*` 这些事件：

```python
async for ev in app.astream_events(x, version="v2"):
    if ev["event"] == "on_chat_model_stream":
        print(ev["data"]["chunk"].content, end="", flush=True)
```

LangGraph 内部把每个 node 都包成了 LCEL Runnable，所以这些事件都会出现。它的好处是能顺带拿到 LangChain 链内部的细节，比如 retriever_end。

---

## 8. 流式 + 工具调用

工具的执行结果通常是一次性生成的，谈不上流式。不过 LLM 生成 tool_call 参数的过程是 token 流，这部分可以拿到：

```python
for chunk in agent.stream(x, stream_mode="messages"):
    token, meta = chunk
    if token.tool_call_chunks:
        print("tool 增量:", token.tool_call_chunks)
```

`tool_call_chunks` 是还没拼完整的 tool_call 片段（name / args / id）。

---

## 9. Stream + HITL

`interrupt()` 触发时，stream 会先 yield 一条 `__interrupt__` 事件再停下（静态断点不会这样，见 [LangGraph 06：Human-in-the-loop 与 Time-travel](./06-human-in-the-loop.md)）：

```python
for ev in app.stream(x, config=cfg, stream_mode="updates"):
    if "__interrupt__" in ev:
        print("等待：", ev["__interrupt__"][0].value)
        break
```

之后用 `app.stream(Command(resume=...), config=cfg)` 继续。

---

## 10. 性能与背压

同一个 thread 内的请求要串行，不要让两个运行同时写同一个 `thread_id`；不同 thread 之间互不影响，可以并行。多个不同 thread 的输入可以一起 batch：

```python
results = app.batch([
    {"messages": [...]},
    {"messages": [...]},
], config=[{"configurable": {"thread_id": f"t{i}"}} for i in range(2)])
```

这里 `config` 是一个列表，和 input 一一对应。

---

## 11. 本章 demo

完整代码：[`demos/langgraph/07_streaming.py`](../../demos/langgraph/07_streaming.py)

```python
# demos/langgraph/07_streaming.py
import os
from typing_extensions import Annotated, TypedDict
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.config import get_stream_writer
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()

@tool
def add(a: int, b: int) -> int:
    """加法"""
    return a + b

class S(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash")).bind_tools([add])

def agent(state):
    writer = get_stream_writer()
    writer({"event": "agent_started"})
    msg = model.invoke(state["messages"])
    writer({"event": "agent_done", "tool_calls": bool(msg.tool_calls)})
    return {"messages": [msg]}

g = StateGraph(S)
g.add_node("agent", agent)
g.add_node("tools", ToolNode([add]))
g.add_edge(START, "agent")
g.add_conditional_edges("agent", tools_condition)
g.add_edge("tools", "agent")
app = g.compile()

x = {"messages": [("human", "1+2 是多少？")]}

print("\n--- updates ---")
for ev in app.stream(x, stream_mode="updates"):
    print(ev)

print("\n--- messages (token) ---")
for token, meta in app.stream(x, stream_mode="messages"):
    if token.content:
        print(token.content, end="", flush=True)

print("\n\n--- custom ---")
for ev in app.stream(x, stream_mode="custom"):
    print(ev)

print("\n--- multi ---")
for mode, ev in app.stream(x, stream_mode=["updates", "custom"]):
    print(mode, ev)
```

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `stream_mode="messages"` 拿不到 token | 模型设了 `disable_streaming=True`，或节点里调的不是 LangChain 的 ChatModel | messages 模式靠 LangChain 回调拿 token，用 LangChain 模型并且别禁用流式 |
| 同时传多种 mode 只拿到一种 | 没用 list 形式 | `stream_mode=["updates", "messages"]`，然后解包 |
| custom 事件发不出来 | 不在 node 内，或没用 writer | `from langgraph.config import get_stream_writer` |
| 异步 stream 卡住 | 没用 `async for` | 别和同步写法混用 |
| 进度条乱跳 | `values` mode 每条都是完整 state | 改用 `updates` |

---

下一篇：[LangGraph 08：多 Agent 编排（Supervisor / Network / Swarm / Hierarchical）](08-multi-agent.md)，这一部分内容最多的一章。
