# LangGraph 02：StateGraph 基础（Node / Edge / 条件边 / 编译运行）

> **一句话**：StateGraph = "State schema + 节点函数 + 边"。每个 node 接收当前 State，返回 partial State，LangGraph 自动把 partial 合并回 State。

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

## 2. Node：本质上是一个函数

签名：

```python
def node(state: State, config: RunnableConfig | None = None) -> dict
```

或异步：

```python
async def node(state, config=None) -> dict
```

返回的 dict 是 **State 的 partial update**：

```python
def inc(s: State) -> dict:
    return {"counter": s["counter"] + 1}   # 只返回要更新的字段
```

未返回的字段不变，**不会**被设成 None。

可以是 **LCEL Runnable** 直接当 node：

```python
chain = prompt | model | StrOutputParser()
g.add_node("answer", chain)   # 输入 state，输出 str → 自动包装
```

也可以传任意 callable。

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

最常用。轻量、静态类型友好。

### 3.2 Pydantic

```python
from pydantic import BaseModel

class State(BaseModel):
    question: str
    docs: list[str] = []
    answer: str = ""
```

支持校验，但运行时开销略大。

### 3.3 dataclass

```python
from dataclasses import dataclass, field

@dataclass
class State:
    question: str
    docs: list = field(default_factory=list)
    answer: str = ""
```

三种都能用，**TypedDict 是官方文档主推**。

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

`path_map` 把 router 返回的字符串映射到目标节点。也可省略 path_map，router 直接返回节点名：

```python
def router(state):
    return END if state["counter"] > 10 else "B"

g.add_conditional_edges("A", router)
```

### 4.3 并行边（fan-out）

同一个 source 加多个普通边：

```python
g.add_edge("A", "B")
g.add_edge("A", "C")
```

A 执行完后 **B、C 并行**执行。这是 LangGraph 重要特性，不需要显式 `parallel`。

### 4.4 入口与出口

```python
from langgraph.graph import START, END
g.add_edge(START, "first_node")
g.add_edge("last_node", END)
# 或者
g.set_entry_point("first_node")
g.set_finish_point("last_node")
```

START 必须从一个特殊常量来，END 也是。

---

## 5. 编译选项

```python
app = g.compile(
    checkpointer=MemorySaver(),     # 持久化（HITL 必需）
    interrupt_before=["tool"],      # 这些 node 执行前暂停
    interrupt_after=["plan"],       # 之后暂停
    debug=True,                     # 详细日志
    name="my-graph",                # LangSmith 显示名
)
```

`MemorySaver` / `SqliteSaver` 等下一篇专门讲。

---

## 6. invoke / stream / 输出

### 6.1 invoke

```python
app.invoke({"question": "hi"})
# 返回完整 final State
```

### 6.2 stream

```python
# stream_mode 有 4 种 ("values"/"updates"/"messages"/"debug")
for event in app.stream({"question": "hi"}, stream_mode="updates"):
    # event 是 dict: {node_name: partial_state}
    print(event)
```

详见 07 篇。

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

注意：

- `log` 字段没用 reducer，每次返回完整新列表才能"追加"
- 条件边自循环 "inc" → "inc" 完全合法
- 默认递归上限 `recursion_limit=25`，超出抛错

---

## 8. Reducer 初探（关键）

上面 `log` 字段需要手动 `s["log"] + [...]` 才能追加。LangGraph 提供 **Reducer** 自动合并：

```python
from typing_extensions import Annotated
from operator import add

class S(TypedDict):
    n: int
    log: Annotated[list[str], add]   # 自动合并：旧 + 新

def inc(s: S) -> dict:
    return {"n": s["n"] + 1, "log": [f"inc to {s['n']+1}"]}   # 只返回新增项
```

每次 node 返回的 `log` 都是"新增"，LangGraph 自动 `old + new`。

对消息列表有专用 reducer `add_messages`，下一篇详讲。

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

`a` 和 `b` 并行执行；都完成后 `merge` 一次执行。**LangGraph 自动等齐**。

---

## 10. 可视化

```python
print(app.get_graph().draw_ascii())
# 或保存图片
app.get_graph().draw_mermaid_png(output_file_path="graph.png")
```

得到一张漂亮的拓扑图，直接放文档里。

---

## 11. demo

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
| `RecursionError` | 循环没收敛 | 加退出条件 / `compile(recursion_limit=...)` |
| 字段每次被覆盖 | 没有 reducer | 用 `Annotated[..., add]` |
| 并行 node 写同一字段冲突 | 并发写无 reducer | 给字段加 reducer |
| 编译报错 "no entry point" | 没 START 边 | `add_edge(START, "x")` |
| `END` 不识别 | import 错 | `from langgraph.graph import END` |

---

## 13. 本章 demo

[`demos/langgraph/02_stategraph.py`](../../demos/langgraph/02_stategraph.py)

下一篇：[03-state-and-reducers.md](03-state-and-reducers.md)
