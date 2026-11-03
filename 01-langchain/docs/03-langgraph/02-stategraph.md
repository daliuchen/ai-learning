# LangGraph 02：StateGraph 基础（Node / Edge / 条件边 / 编译运行）

> StateGraph 由三样东西组成：State schema、节点函数、边。每个 node 拿到当前 State，只返回需要改的那部分字段，LangGraph 负责把这部分合并回去。这篇把构图、编译、运行的基本操作走一遍，后面所有篇章都建立在这套写法上。

版本：以 `langgraph 1.2.19` 为准；`MemorySaver` 是 `InMemorySaver` 的别名。

---

## 1. StateGraph 构造步骤

```python
from langgraph.graph import StateGraph, START, END

# 1. 定义 State schema（TypedDict / Pydantic / dataclass）
class State(TypedDict):
    counter: int
    history: list[str]

# 2. 创建 builder
g = StateGraph(State)

# 3. 添加 node
g.add_node("inc", inc_fn)
g.add_node("dec", dec_fn)

# 4. 添加 edge
g.add_edge(START, "inc")
g.add_conditional_edges("inc", router_fn, {"go_dec": "dec", "stop": END})
g.add_edge("dec", END)

# 5. 编译
app = g.compile()

# 6. 执行
app.invoke({"counter": 0, "history": []})
```

---

## 2. Node 就是一个函数

签名：

```python
def node(state: State, config: RunnableConfig | None = None) -> dict
```

节点还可以声明 `runtime: Runtime[Context]` 参数，拿到本次运行的只读上下文（`runtime.context`）、`store`、`stream_writer`。上下文类型在 `StateGraph(State, context_schema=Context)` 里声明，调用时用 `app.invoke(x, context=Context(...))` 传入。

也可以写成异步：

```python
async def node(state, config=None) -> dict
```

返回的 dict 是对 State 的局部更新：

```python
def inc(s: State) -> dict:
    return {"counter": s["counter"] + 1}   # 只返回要更新的字段
```

没返回的字段保持原值，**不会被设成 None**。

LCEL Runnable 也能直接当 node，但节点的返回值必须是 dict（或 `Command`），返回字符串会抛 `InvalidUpdateError: Expected dict, got ...`。链的末尾是 `StrOutputParser` 时，要再接一步把结果包成 State 的字段：

```python
from langchain_core.runnables import RunnableLambda

chain = prompt | model | StrOutputParser() | RunnableLambda(lambda text: {"answer": text})
g.add_node("answer", chain)   # 输入 state，输出 {"answer": ...}
```

其他任意 callable 同样可以，返回值的要求一样。

---

## 3. State：三种 schema 写法

### 3.1 TypedDict（推荐）

```python
from typing_extensions import TypedDict

class State(TypedDict):
    question: str
    docs: list[str]
    answer: str
```

最常用，轻量，对静态类型检查友好。

### 3.2 Pydantic

```python
from pydantic import BaseModel

class State(BaseModel):
    question: str
    docs: list[str] = []
    answer: str = ""
```

能做校验（包括类型转换），代价是运行时开销稍大；节点收到的是模型实例，用 `state.question` 取值。

### 3.3 dataclass

```python
from dataclasses import dataclass, field

@dataclass
class State:
    question: str
    docs: list = field(default_factory=list)
    answer: str = ""
```

三种都能用，官方文档主推 TypedDict。dataclass 有个细节：默认值只在构造节点入参时补上，不会自动写进 state，图的最终输出始终是 dict。

---

## 4. Edge：三种

### 4.1 普通边

```python
g.add_edge("A", "B")   # A 之后必须执行 B
```

### 4.2 条件边

```python
def router(state) -> str:
    if state["counter"] > 10:
        return "end"
    return "continue"

g.add_conditional_edges(
    source="A",
    path=router,
    path_map={"end": END, "continue": "B"},
)
```

`path_map` 把 router 返回的字符串映射到目标节点。也可以省掉 path_map，让 router 直接返回节点名：

```python
def router(state):
    return END if state["counter"] > 10 else "B"

g.add_conditional_edges("A", router)
```

### 4.3 并行边（fan-out）

从同一个 source 连出多条普通边：

```python
g.add_edge("A", "B")
g.add_edge("A", "C")
```

A 执行完后，B 和 C 会并行执行，不需要写任何显式的 `parallel`。

### 4.4 入口与出口

```python
from langgraph.graph import START, END
g.add_edge(START, "first_node")
g.add_edge("last_node", END)
```

`set_entry_point` / `set_finish_point` 是等价的旧写法，现在仍能用，新代码统一写 `add_edge(START, ...)`。

---

## 5. 编译选项

```python
app = g.compile(
    checkpointer=InMemorySaver(),   # 持久化（HITL 必需）
    interrupt_before=["tool"],      # 静态断点：这些 node 执行前暂停（官方只建议调试用）
    interrupt_after=["plan"],       # 之后暂停
    debug=True,                     # 详细日志
    name="my-graph",                # LangSmith 显示名
)
```

`InMemorySaver`、`SqliteSaver` 这些在 05 篇专门讲。做人在回路时优先用节点内的 `interrupt()`（06 篇），静态断点留给调试。

---

## 6. invoke / stream / 输出

### 6.1 invoke

```python
app.invoke({"question": "hi"})
# 返回完整 final State
```

### 6.2 stream

```python
# stream_mode 有 values / updates / messages / custom / debug 等，可传列表组合
for event in app.stream({"question": "hi"}, stream_mode="updates"):
    # event 是 dict: {node_name: partial_state}
    print(event)
```

各种模式的区别见 [LangGraph 07：Streaming 的各种模式](./07-streaming.md)。

### 6.3 ainvoke / astream

异步版本，签名一致。

---

## 7. 第一个真实例子：计数循环

```python
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END

class S(TypedDict):
    n: int
    log: list[str]

def inc(s: S) -> dict:
    return {"n": s["n"] + 1, "log": s["log"] + [f"inc to {s['n']+1}"]}

def router(s: S) -> str:
    return END if s["n"] >= 5 else "inc"

g = StateGraph(S)
g.add_node("inc", inc)
g.add_edge(START, "inc")
g.add_conditional_edges("inc", router, {"inc": "inc", END: END})

app = g.compile()
print(app.invoke({"n": 0, "log": []}))
# {'n': 5, 'log': ['inc to 1','inc to 2','inc to 3','inc to 4','inc to 5']}
```

这段代码里有三点要留意：

- `log` 字段没用 reducer，每次都得返回完整的新列表才能实现追加
- 条件边让 "inc" 指回自己，这是完全合法的
- 一次运行最多跑多少步由 `recursion_limit` 决定，超出会抛 `GraphRecursionError`。它是运行时 config（`app.invoke(x, {"recursion_limit": 50})`），不是 `compile()` 的参数。当前默认值是 10007（可用环境变量 `LANGGRAPH_DEFAULT_RECURSION_LIMIT` 调整），死循环要跑很久才会停，最好自己设一个上限

---

## 8. Reducer 初探

上面的 `log` 字段要手动写 `s["log"] + [...]` 才能追加。LangGraph 提供的 Reducer 可以自动合并：

```python
from typing_extensions import Annotated
from operator import add

class S(TypedDict):
    n: int
    log: Annotated[list[str], add]   # 自动合并：旧 + 新

def inc(s: S) -> dict:
    return {"n": s["n"] + 1, "log": [f"inc to {s['n']+1}"]}   # 只返回新增项
```

这样 node 每次只返回新增的条目，LangGraph 自己做 `old + new`。

消息列表有专用的 reducer `add_messages`，下一篇细讲。

---

## 9. 并行 fan-out / fan-in

```python
def split(s): return {"items": s["items"]}        # 不变
def process_a(s): return {"out_a": "..."}
def process_b(s): return {"out_b": "..."}
def merge(s): return {"final": s["out_a"] + s["out_b"]}

g.add_node("split", split)
g.add_node("a", process_a)
g.add_node("b", process_b)
g.add_node("merge", merge)
g.add_edge(START, "split")
g.add_edge("split", "a")
g.add_edge("split", "b")
g.add_edge("a", "merge")
g.add_edge("b", "merge")
g.add_edge("merge", END)
```

`a` 和 `b` 并行执行，两个都完成后 `merge` 只执行一次，等齐这件事由 LangGraph 负责。

---

## 10. 可视化

```python
print(app.get_graph().draw_ascii())   # 需要 pip install grandalf
# 或保存图片
app.get_graph().draw_mermaid_png(output_file_path="graph.png")
```

导出的拓扑图可以直接贴进文档。

---

## 11. 本章 demo

完整代码：[`demos/langgraph/02_stategraph.py`](../../demos/langgraph/02_stategraph.py)

```python
# demos/langgraph/02_stategraph.py
from operator import add
from typing_extensions import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END

class S(TypedDict):
    n: int
    log: Annotated[list[str], add]

def inc(s):
    new = s["n"] + 1
    return {"n": new, "log": [f"inc -> {new}"]}

def router(s):
    return END if s["n"] >= 5 else "inc"

g = StateGraph(S)
g.add_node("inc", inc)
g.add_edge(START, "inc")
g.add_conditional_edges("inc", router, {"inc": "inc", END: END})
app = g.compile()

print(app.invoke({"n": 0, "log": []}))
print(app.get_graph().draw_ascii())
```

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `GraphRecursionError` | 循环没收敛 | 加退出条件 / 调用时传 `config={"recursion_limit": N}`（不是 `compile` 参数） |
| 字段每次被覆盖 | 没有 reducer | 用 `Annotated[..., add]` |
| 并行 node 写同一字段报 `InvalidUpdateError` | 同一步里多个写入且无 reducer | 给字段加 reducer |
| 编译报错 "no entry point" | 没 START 边 | `add_edge(START, "x")` |
| `END` 不识别 | import 错 | `from langgraph.graph import END` |

---

下一篇：[LangGraph 03：State 与 Reducer](03-state-and-reducers.md)
