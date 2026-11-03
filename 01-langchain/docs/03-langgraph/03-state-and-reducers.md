# LangGraph 03：State 与 Reducer

> State 是图里唯一的数据载体，Reducer 是挂在字段上的合并函数，决定多个 node 更新同一个字段时结果怎么算。我的经验是，State 和 Reducer 设计对了，复杂图就成功了一半。这篇把默认行为、消息专用的 `add_messages`、自定义 reducer 和几种 schema 拆分方式依次讲清楚。

---

## 1. 默认行为：覆盖

不加 reducer 时，node 的返回值会直接覆盖对应字段：

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
    tags: Annotated[set[str], lambda l, r: l | r]   # set 合并（注意：不能直接用 set.union，内建方法没有签名，LangGraph 会报错）
    total: Annotated[int, lambda a, b: a + b]  # 整数累加
```

node 返回局部更新时：

```python
def n(s): return {"logs": ["new"], "tags": {"a"}, "total": 3}
```

LangGraph 会用每个字段各自的 reducer，把这次的更新合并到当前值上。

---

## 3. 消息专用 Reducer：add_messages

对话型的图最常见，State 里要存一份消息列表：

```python
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
```

`add_messages` 比 `add` 多做了几件事：

- 按 `id` 自动去重
- 可以修改某条已有消息（返回一条相同 id 的新 message）
- 支持用 RemoveMessage 删除消息

```python
def n(s):
    return {"messages": [AIMessage(content="hi", id="msg-1")]}
```

返回的 message 如果带着已有的 `id`，就会替换原消息而不是追加，适合需要修正某条回复的场景。

LangGraph 还提供了现成的 `MessagesState`：

```python
from langgraph.graph import MessagesState

class State(MessagesState):    # 已含 messages 字段
    custom_field: str
```

---

## 4. 自定义 Reducer

任意二元函数都可以：

```python
def merge_dicts(left: dict, right: dict) -> dict:
    return {**left, **right}

class State(TypedDict):
    meta: Annotated[dict, merge_dicts]
```

写自定义 reducer 有三条要求：

- 纯函数，没有副作用
- 能处理 `left=None` 或 `right=None`（首次合并时会遇到）
- 满足结合律，因为并行合并多个更新时顺序没有保证

---

## 5. 输入与输出 schema 分离

复杂应用里，外部输入、内部状态和最终输出的 schema 往往不一样：

```python
class InputState(TypedDict):
    question: str

class OutputState(TypedDict):
    answer: str

class InternalState(TypedDict):
    question: str
    docs: list[str]
    answer: str

g = StateGraph(InternalState, input_schema=InputState, output_schema=OutputState)   # 旧的 input= / output= 已弃用
```

这样一来，调 `invoke` 只需要传 `InputState` 的字段，返回值也只暴露 `OutputState` 的字段，所有细节留在内部 State 里。

`output_schema` 只约束 `invoke` 的返回值，管不到流式输出：`stream_mode="values"` 照样会吐出全部字段，别靠它隐藏敏感数据。

---

## 6. 多 schema：private state

有时某个节点的中间变量不想暴露给其他节点：

```python
class Public(TypedDict):
    question: str
    answer: str

class Scratch(TypedDict):          # 私有：没有传给 StateGraph
    debug_info: str

def step1(state: Public) -> Scratch:
    return {"debug_info": "只给 step2 看"}

def step2(state: Scratch) -> Public:   # 入参注解决定它能看到哪些字段
    return {"answer": state["debug_info"] + " → done"}
```

规则是这样的：只要某个节点的注解里出现了 `Scratch`，它的字段就会成为图里的 channel，也就是所谓私有 state。节点只会收到自己入参注解里声明的字段，但写入不受这个限制，图里任何字段都能写。

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
    best_score: Annotated[float, lambda l, r: max(l or 0, r)]   # 内建 max 同样没有签名，要包一层 lambda
```

---

## 8. State 持久化与 reducer

reducer 在每次节点写入时把更新合并进 state，checkpoint 里存的是合并后的值。恢复（resume）时，被 `interrupt()` 中断的节点会从头重跑，但它上一次没有执行到 `return`，写入从未提交，所以累加型 reducer（比如 `add`）不会被多加一次；同一步里已经跑完的其他节点，写入作为 pending writes 存在 checkpoint 里，也不会重跑。

```python
counter: Annotated[int, add]
def a(s): return {"counter": 1}                        # 和 b 并行，先跑完
def b(s): interrupt("确认？"); return {"counter": 10}  # 中断后 resume
# resume 之后 counter == 11：a 不重跑，b 重跑一次只提交一次
```

真正会重复执行的是节点里 `interrupt()` 之前的代码。写数据库、发请求这类副作用要放在 `interrupt()` 之后，或者做成幂等的。

想整体替换而不是累加，用 `Overwrite` 绕过 reducer：

```python
from langgraph.types import Overwrite

def reset_logs(s):
    return {"logs": Overwrite([])}    # 直接把 logs 设成 []，不走 add
```

只返回空列表 `[]` 是清不掉的，因为 `old + []` 还是 old。消息列表里要删个别消息，用 `RemoveMessage(id=...)`。

---

## 9. State 与 Configurable 区别

State 是每次 invoke 里流动、会被修改的数据；Configurable 是本次 invoke 固定不变的配置，比如 `thread_id`、`user_id`、模型名。

```python
config = {
    "configurable": {
        "thread_id": "abc",
        "user_id": "u1",
        "model": "qwen-flash",
    },
}
app.invoke({"question": "..."}, config=config)
```

在 node 里这样读：

```python
def node(state, config: RunnableConfig):
    user_id = config["configurable"]["user_id"]
    ...
```

新版更推荐 runtime context：声明 `context_schema`，调用时用 `context=` 传入，节点里通过 `Runtime` 读取，还能拿到类型检查：

```python
from dataclasses import dataclass
from langgraph.runtime import Runtime

@dataclass
class Ctx:
    user_id: str

g = StateGraph(State, context_schema=Ctx)

def node(state: State, runtime: Runtime[Ctx]):
    return {"answer": runtime.context.user_id}

app.invoke({"question": "..."}, context=Ctx(user_id="u1"))
```

`configurable` 里仍然放 `thread_id` 这类框架自己要用的键。

---

## 10. 实战 demo

完整代码：[`demos/langgraph/03_state_reducers.py`](../../demos/langgraph/03_state_reducers.py)

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
| `InvalidUpdateError: Cannot have multiple updates ...` | 并行 node 写同一字段，没有 reducer | 给字段加 reducer |
| reducer 收到 None | 第一次合并时没有初始值 | reducer 里写 `(l or default)` |
| messages 重复 | 没用 `add_messages` | 换成它 |
| Pydantic State 改字段不生效 | 返回新对象时类型不对 | 改用 TypedDict 更省事 |
| reducer 偶发 bug | 函数有副作用 | 保持纯函数 |

---

下一篇：[LangGraph 04：ReAct Agent（create_agent + 手写版）](04-react-agent.md)
