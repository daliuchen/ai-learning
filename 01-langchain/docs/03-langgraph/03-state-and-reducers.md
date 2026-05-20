# LangGraph 03：State 与 Reducer 深度

> **一句话**：State 是 LangGraph 唯一的"数据载体"，Reducer 是字段级的合并函数，决定多个 node 同时更新一个字段时怎么合并。设计好 State + Reducer，复杂图就成功了一半。

---

## 1. 默认行为：覆盖

不加 reducer 时，node 返回值**覆盖**对应字段：

```python
class State(TypedDict):
    name: str

def n1(s): return {"name": "Alice"}
def n2(s): return {"name": "Bob"}

# 如果 n1 / n2 串行，name 最终是 Bob
# 如果并行，会冲突报错（InvalidUpdateError）
```

---

## 2. 加 Reducer：Annotated

```python
from typing_extensions import Annotated
from operator import add

class State(TypedDict):
    name: str                              # 默认覆盖
    logs: Annotated[list[str], add]        # list 拼接
    tags: Annotated[set[str], set.union]   # set 合并
    total: Annotated[int, lambda a, b: a + b]  # 整数累加
```

每次 node 返回 partial 时：

```python
def n(s): return {"logs": ["new"], "tags": {"a"}, "total": 3}
```

LangGraph 会用对应 reducer 把 `partial` 合并到 `current`。

---

## 3. 消息专用 Reducer：add_messages

对话型图最常见，State 里要存消息列表：

```python
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
```

`add_messages` 比 `add` 更智能：

- 自动按 `id` 去重
- 支持"修改某条已有消息"（返回带相同 id 的新 message）
- 支持 RemoveMessage 来删消息

```python
def n(s):
    return {"messages": [AIMessage(content="hi", id="msg-1")]}
```

如果某次返回的 message 带已有 `id`，会**替换**而非追加，很适合"修正"场景。

LangGraph 还提供现成的 `MessagesState`：

```python
from langgraph.graph import MessagesState

class State(MessagesState):    # 已含 messages 字段
    custom_field: str
```

---

## 4. 自定义 Reducer

任意二元函数都行：

```python
def merge_dicts(left: dict, right: dict) -> dict:
    return {**left, **right}

class State(TypedDict):
    meta: Annotated[dict, merge_dicts]
```

要点：
- 必须是 **纯函数**（无副作用）
- 必须能处理 `left=None` 或 `right=None` 的情况（首次合并）
- 必须是**可结合**的（并行合并多个 partial 时顺序不保证）

---

## 5. 输入与输出 schema 分离

复杂应用里，外部 input、内部 state、最终 output 可能 schema 不一样：

```python
class InputState(TypedDict):
    question: str

class OutputState(TypedDict):
    answer: str

class InternalState(TypedDict):
    question: str
    docs: list[str]
    answer: str

g = StateGraph(InternalState, input=InputState, output=OutputState)
```

- 调 `invoke` 只需要传 `InputState` 字段
- 返回只暴露 `OutputState` 字段
- 内部 State 包含全部细节

---

## 6. 多 schema：private state

某节点的中间变量不想暴露给其他节点：

```python
class Public(TypedDict):
    question: str
    answer: str

class Private(TypedDict, total=False):
    debug_info: str

g = StateGraph(Public)
g.add_node("special", lambda s, write_to=None: ..., input=Private)
```

(具体 API 因版本略不同，参见官方文档。)

---

## 7. 字段类型实例

```python
class State(TypedDict):
    # 1. 简单覆盖
    user_id: str
    
    # 2. 整数累加
    cost_cents: Annotated[int, lambda l, r: (l or 0) + (r or 0)]
    
    # 3. 列表追加
    logs: Annotated[list[str], add]
    
    # 4. set union
    tags: Annotated[set[str], lambda l, r: (l or set()) | (r or set())]
    
    # 5. 消息列表
    messages: Annotated[list, add_messages]
    
    # 6. dict shallow merge
    meta: Annotated[dict, lambda l, r: {**(l or {}), **(r or {})}]
    
    # 7. 自定义最大值
    best_score: Annotated[float, max]
```

---

## 8. State 持久化与 reducer

reducer 不仅在并行时合并，**也在跨 checkpoint 恢复时使用**。因此 reducer 必须 idempotent（重放安全）。

例子：

```python
counter: Annotated[int, add]
def inc(s): return {"counter": 1}
```

每次 inc 加 1。如果某 checkpoint 之后重放 inc 节点 2 次，counter 会被加 2 次。注意区分"幂等"与"可重放"。

要避免重放副作用，用 `RemoveMessage` 或事件 ID 机制。

---

## 9. State 与 Configurable 区别

State：每次 invoke 流动的可变数据。
Configurable：本次 invoke 的不变配置（`thread_id`、`user_id`、模型名等）。

```python
config = {
    "configurable": {
        "thread_id": "abc",
        "user_id": "u1",
        "model": "gpt-4o-mini",
    },
}
app.invoke({"question": "..."}, config=config)
```

在 node 里访问：

```python
def node(state, config: RunnableConfig):
    user_id = config["configurable"]["user_id"]
    ...
```

---

## 10. 实战 demo

```python
# demos/langgraph/03_state_reducers.py
from operator import add
from typing_extensions import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

class S(TypedDict):
    counter: Annotated[int, lambda l, r: (l or 0) + (r or 0)]
    logs: Annotated[list[str], add]
    messages: Annotated[list[BaseMessage], add_messages]
    tags: Annotated[set[str], lambda l, r: (l or set()) | (r or set())]

def n1(s):
    return {"counter": 1, "logs": ["n1"], "messages": [HumanMessage(content="hi")], "tags": {"a"}}

def n2(s):
    return {"counter": 2, "logs": ["n2"], "messages": [AIMessage(content="hello")], "tags": {"b"}}

def n3(s):
    return {"counter": 10, "logs": ["n3"], "tags": {"c"}}

g = StateGraph(S)
g.add_node("n1", n1); g.add_node("n2", n2); g.add_node("n3", n3)
g.add_edge(START, "n1")
g.add_edge("n1", "n2")        # 串行
g.add_edge("n1", "n3")        # n2/n3 并行
g.add_edge("n2", END); g.add_edge("n3", END)

app = g.compile()
print(app.invoke({"counter": 0, "logs": [], "messages": [], "tags": set()}))
```

预期：

```python
{
  'counter': 1 + 2 + 10 = 13,
  'logs': ['n1','n2','n3'] or ['n1','n3','n2'],   # 并行不保证顺序
  'messages': [HumanMessage("hi"), AIMessage("hello")],
  'tags': {'a','b','c'},
}
```

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `InvalidUpdateError: Cannot have multiple updates ...` | 并行 node 写同字段没 reducer | 给字段加 reducer |
| reducer 报 None | 第一次没初始值 | reducer 处理 `(l or default)` |
| messages 重复 | 没用 `add_messages` | 改用它 |
| Pydantic State 改字段不生效 | 返回新对象时类型不对 | 用 TypedDict 简单点 |
| reducer 非纯函数偶发 bug | 副作用 | 保持纯 |

---

## 12. 本章 demo

[`demos/langgraph/03_state_reducers.py`](../../demos/langgraph/03_state_reducers.py)

下一篇：[04-react-agent.md](04-react-agent.md)
