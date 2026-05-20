# LangGraph 05：持久化 Checkpointer 与跨会话记忆

> **一句话**：Checkpointer 是 LangGraph 的"自动存档"机制——每个 node 执行后都把整个 State 写一份快照，挂在 `thread_id` 下。从此你的图天然支持暂停、续跑、回放、多会话隔离。

---

## 1. 为什么需要 Checkpointer

LangChain 的 `RunnableWithMessageHistory` 只保存"消息列表"。LangGraph 的 Checkpointer 保存的是**整个 State**（包括中间变量、当前节点、待办边）。这带来：

- **Human-in-the-loop**：在某节点暂停，等用户输入，再续跑
- **Time-travel**：回到任意历史 checkpoint 重跑（修改 prompt / 模型）
- **跨进程恢复**：进程挂了，下次启动从最后 checkpoint 继续
- **多会话隔离**：每个 `thread_id` 是一条独立的"对话线"

---

## 2. 三类 Checkpointer

| 实现 | 包 | 用途 |
|------|----|------|
| `MemorySaver` | `langgraph.checkpoint.memory` | 内存，单进程，开发/测试 |
| `SqliteSaver` | `langgraph-checkpoint-sqlite` | 本地文件 / 单机持久化 |
| `PostgresSaver` | `langgraph-checkpoint-postgres` | 多副本生产环境 |

接口完全一致：

```python
from langgraph.checkpoint.memory import MemorySaver
checkpointer = MemorySaver()

# 或 SQLite
from langgraph.checkpoint.sqlite import SqliteSaver
checkpointer = SqliteSaver.from_conn_string(":memory:")  # 或 "chat.db"

# 或 Postgres
from langgraph.checkpoint.postgres import PostgresSaver
checkpointer = PostgresSaver.from_conn_string("postgresql://...")
```

---

## 3. 编译时挂上 checkpointer

```python
app = graph.compile(checkpointer=checkpointer)
```

之后所有 `invoke / stream` 都必须传 `thread_id`：

```python
config = {"configurable": {"thread_id": "user-1-conv-A"}}
app.invoke(input, config=config)
```

如果不传 `thread_id`，会报错（编译有 checkpointer 时必传）。

---

## 4. 多轮对话最小例子

```python
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

model = ChatOpenAI(model="gpt-4o-mini")
def chat(state):
    return {"messages": [model.invoke(state["messages"])]}

g = StateGraph(State)
g.add_node("chat", chat)
g.add_edge(START, "chat")
g.add_edge("chat", END)

memory = MemorySaver()
app = g.compile(checkpointer=memory)

cfg = {"configurable": {"thread_id": "t1"}}
app.invoke({"messages": [("human", "我叫小明")]}, config=cfg)
print(app.invoke({"messages": [("human", "我叫什么？")]}, config=cfg)["messages"][-1].content)
# 输出 "你叫小明"
```

第二次 `invoke` 时 LangGraph 自动从 checkpointer 加载 state，把新消息合并进 messages（reducer 是 `add_messages`），LLM 看到完整历史。

---

## 5. 查看与回放历史

```python
# 当前 state
snap = app.get_state(cfg)
print(snap.values)
print(snap.next)             # 下一步要跑哪个 node
print(snap.config["configurable"]["checkpoint_id"])

# 历史所有 checkpoint
for s in app.get_state_history(cfg):
    print(s.config["configurable"]["checkpoint_id"], "next=", s.next)
```

回到某历史 checkpoint 重跑：

```python
history = list(app.get_state_history(cfg))
target = history[2]   # 想回去的那个

# 从这个 checkpoint 重新执行
app.invoke(None, config=target.config)
```

这就是 **Time-travel**。06 篇会展开。

---

## 6. update_state：手动改 State

```python
app.update_state(
    cfg,
    {"messages": [AIMessage(content="（人工修正版）")]},
)
```

效果等于"伪装某 node 输出了这个 partial"，会被 reducer 合并。常用于：

- 人工修正模型输出
- 测试时注入特定状态再续跑

---

## 7. SqliteSaver 持久化文件

```python
from langgraph.checkpoint.sqlite import SqliteSaver

with SqliteSaver.from_conn_string("chat.db") as memory:
    app = g.compile(checkpointer=memory)
    app.invoke(..., config={"configurable": {"thread_id": "user1"}})
```

进程重启后再次 `SqliteSaver.from_conn_string("chat.db")` 打开，所有 thread 的 state 都还在。

异步版：`AsyncSqliteSaver`。

---

## 8. Postgres 生产部署

```python
from psycopg_pool import ConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver

pool = ConnectionPool("postgresql+psycopg://user:pw@host/db", max_size=20)
memory = PostgresSaver(pool)
memory.setup()  # 首次创建表

app = g.compile(checkpointer=memory)
```

表 schema：
- `checkpoints`：每个 step 的 state 快照
- `checkpoint_writes`：增量写

Postgres Checkpointer 支持多副本 worker 安全并发（基于 thread_id 锁）。

---

## 9. 跨会话长期记忆（Store）

**Checkpointer = 一个 thread 内的状态**（同一对话）。
**Store = 跨 thread 的长期数据**（用户偏好、历史事实）。

```python
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres import PostgresStore

store = InMemoryStore()
app = g.compile(checkpointer=checkpointer, store=store)
```

在 node 里访问：

```python
def remember_pref(state, *, store):
    # 写
    store.put(
        namespace=("users", "u_123"),
        key="favorite_color",
        value={"value": "blue"},
    )
    # 读
    item = store.get(("users", "u_123"), "favorite_color")
    return {...}
```

或者基于语义检索（带 embeddings）：

```python
store = InMemoryStore(index={"embed": OpenAIEmbeddings(), "dims": 1536})
store.put(("users","u"), "fact-1", {"text": "用户喜欢蓝色"})
items = store.search(("users", "u"), query="favorite color", limit=3)
```

非常适合"长期个性化"应用。

---

## 10. demo

```python
# demos/langgraph/05_persistence.py
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.store.memory import InMemoryStore

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

model = ChatOpenAI(model="gpt-4o-mini")

def chat(state, *, store, config):
    user = config["configurable"]["user_id"]
    pref = store.get(("users", user), "favorite_color")
    sys = f"用户偏好: {pref.value if pref else '无'}"
    msgs = [("system", sys)] + state["messages"]
    return {"messages": [model.invoke(msgs)]}

g = StateGraph(State)
g.add_node("chat", chat)
g.add_edge(START, "chat"); g.add_edge("chat", END)

with SqliteSaver.from_conn_string(":memory:") as memory:
    store = InMemoryStore()
    store.put(("users", "u1"), "favorite_color", {"value": "蓝色"})
    app = g.compile(checkpointer=memory, store=store)

    cfg = {"configurable": {"thread_id": "t1", "user_id": "u1"}}
    print(app.invoke({"messages": [("human", "推荐一双适合我喜欢的颜色的鞋")]}, config=cfg)["messages"][-1].content)
    print(app.invoke({"messages": [("human", "刚才你说的鞋什么颜色？")]}, config=cfg)["messages"][-1].content)
```

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| compile 加了 checkpointer 但 invoke 不带 thread_id | 必传 | `config={"configurable":{"thread_id":...}}` |
| 多用户串话 | 用同一个 thread_id | 加用户隔离前缀 |
| SqliteSaver 文件越来越大 | 历史 checkpoint 累积 | 定期清理 / 用 Postgres |
| store 在 node 里拿不到 | 参数名错 | 必须命名参数 `store=`，签名 `def n(state, *, store)` |
| update_state 不生效 | thread 不对 | 用同一个 thread_id |

---

## 12. 本章 demo

[`demos/langgraph/05_persistence.py`](../../demos/langgraph/05_persistence.py)

下一篇：[06-human-in-the-loop.md](06-human-in-the-loop.md)
