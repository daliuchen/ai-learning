# LangGraph 07：Streaming 全部模式

> **一句话**：LangGraph 的 `stream` 支持 `values / updates / messages / debug / custom` 五种 mode（可组合），分别覆盖"完整状态 / 增量更新 / Token 流 / 调试细节 / 自定义事件"五类需求。`astream_events` 仍可用，但 LangGraph 原生 stream 更细。

---

## 1. 五种 stream_mode

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

第一项告诉你是哪种 mode。

---

## 2. values：每步完整 State

```python
for state in app.stream(x, stream_mode="values"):
    print(state)
```

每次产出当前 State 全量快照，包含所有字段。**适合做"进度条"展示**，缺点是数据量大。

---

## 3. updates：每步增量

```python
for ev in app.stream(x, stream_mode="updates"):
    # ev = {"node_name": partial_state}
    print(ev)
```

典型输出：

```
{'plan': {'plan': '1. step ... 2. ...'}}
{'tools': {'messages': [ToolMessage(...)]}}
{'agent': {'messages': [AIMessage(...)]}}
```

最常用：**前端展示"AI 正在 plan / 正在调工具 / 正在生成"** 用这个。

---

## 4. messages：Token 级流

```python
for token, metadata in app.stream(x, stream_mode="messages"):
    print(token.content, end="", flush=True)
```

- `token` 是 `AIMessageChunk`
- `metadata` 含 `tags`, `name`, `langgraph_node` 等

可以根据 `metadata.langgraph_node` 区分是哪个 node 在输出 token（比如 plan vs final answer）。

异步：

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

事件 type：`task` / `task_result` / `checkpoint`。每次 node 进入/退出都有事件，附带 input/output/elapsed_time。**调试卡顿、内存爆炸时打开看耗时分布**。

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

**业务事件**走 custom，比塞进 metadata 干净。

---

## 7. astream_events（兼容 LCEL）

LangChain 那套 `astream_events(version="v2")` 在 LangGraph 上也能用：

```python
async for ev in app.astream_events(x, version="v2"):
    if ev["event"] == "on_chat_model_stream":
        print(ev["data"]["chunk"].content, end="", flush=True)
```

LangGraph 内部把每个 node 都包成 LCEL Runnable，所以事件都有。优势：可以同时拿到 LangChain 链的细节（如 retriever_end）。

---

## 8. 流式 + 工具调用

工具调用结果通常是同步生成的，不会"流"。但 LLM 生成 tool_call args 时是 token 流，可以拿到：

```python
for chunk in agent.stream(x, stream_mode="messages"):
    token, meta = chunk
    if token.tool_call_chunks:
        print("tool 增量:", token.tool_call_chunks)
```

`tool_call_chunks` 是部分 tool_call 字段（name / args / id 还没拼完整时）。

---

## 9. Stream + HITL

`interrupt` 出现时 stream 会 yield 一条 `__interrupt__` 事件然后停：

```python
for ev in app.stream(x, config=cfg, stream_mode="updates"):
    if "__interrupt__" in ev:
        print("等待：", ev["__interrupt__"][0].value)
        break
```

之后 `app.stream(Command(resume=...), config=cfg)` 继续。

---

## 10. 性能与背压

- 同一时刻只有一个 worker 跑某个 thread 的图（thread 内串行）
- 不同 thread 完全并行
- batch 多个不同 thread 输入：

```python
results = app.batch([
    {"messages": [...]},
    {"messages": [...]},
], config=[{"configurable": {"thread_id": f"t{i}"}} for i in range(2)])
```

`config` 是列表与 input 一对一。

---

## 11. demo

```python
# demos/langgraph/07_streaming.py
import asyncio
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

model = ChatOpenAI(model="gpt-4o-mini").bind_tools([add])

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
| `stream_mode="messages"` 拿不到 token | 模型没设 streaming | ChatOpenAI 默认 stream，确认没禁用 |
| 多种 mode 同时只拿到一种 | 没用 list 形式 | `stream_mode=["updates", "messages"]` 然后 unpack |
| custom 写不出 | 不在 node 内/没用 writer | `from langgraph.config import get_stream_writer` |
| 异步 stream 卡住 | 没 `async for` | 别和同步 mix |
| 进度条乱跳 | `values` mode 每条是完整 state | 改用 `updates` |

---

## 13. 本章 demo

[`demos/langgraph/07_streaming.py`](../../demos/langgraph/07_streaming.py)

下一篇：[08-multi-agent.md](08-multi-agent.md) — 多 Agent 编排，本系列最有料的一章。
