# LangGraph 05：持久化 Checkpointer 与跨会话记忆

> Checkpointer 相当于 LangGraph 的自动存档：每个 node 执行完，都把整个 State 存一份快照，挂在 `thread_id` 下面。有了它，图才能暂停、续跑、回放，多个会话之间也互不干扰。后面讲的人在回路和 Time-travel 都建立在这一篇的基础上。

版本：以 `langgraph 1.2.19`、`langgraph-checkpoint 4.3.1`、`langgraph-checkpoint-sqlite 3.1.3` 为准；`MemorySaver` 是 `InMemorySaver` 的别名。

---

## 1. 为什么需要 Checkpointer

LangChain 的 `RunnableWithMessageHistory` 只保存消息列表，LangGraph 的 Checkpointer 保存的是整个 State，包括中间变量、当前节点和待执行的边。这样就能做到：

- Human-in-the-loop：在某个节点暂停，等用户输入后续跑
- Time-travel：回到任意一个历史 checkpoint 重跑（比如换了 prompt 或模型）
- 跨进程恢复：进程挂了，下次启动从最后一个 checkpoint 继续
- 多会话隔离：每个 `thread_id` 是一条独立的对话线

---

## 2. 三类 Checkpointer

| 实现 | 包 | 用途 |
|------|----|------|
| `InMemorySaver`（旧名 `MemorySaver`） | `langgraph.checkpoint.memory` | 内存，单进程，开发/测试 |
| `SqliteSaver` | `langgraph-checkpoint-sqlite` | 本地文件 / 单机持久化 |
| `PostgresSaver` | `langgraph-checkpoint-postgres` | 多副本生产环境 |

三者接口一致：

```python
from langgraph.checkpoint.memory import InMemorySaver
checkpointer = InMemorySaver()

# 或 SQLite（from_conn_string 是上下文管理器）
from langgraph.checkpoint.sqlite import SqliteSaver
with SqliteSaver.from_conn_string("chat.db") as checkpointer:   # 或 ":memory:"
    ...

# 或 Postgres（pip install langgraph-checkpoint-postgres "psycopg[binary]"）
from langgraph.checkpoint.postgres import PostgresSaver
with PostgresSaver.from_conn_string("postgresql://...") as checkpointer:
    checkpointer.setup()      # 首次使用建表
```

---

## 3. 编译时挂上 checkpointer

```python
app = graph.compile(checkpointer=checkpointer)
```

挂上之后，每次 `invoke / stream` 都要传 `thread_id`：

```python
config = {"configurable": {"thread_id": "user-1-conv-A"}}
app.invoke(input, config=config)
```

编译时有 checkpointer 而调用时没传 `thread_id`，会直接报错。

---

## 4. 多轮对话最小例子

```python
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import InMemorySaver
from langchain_openai import ChatOpenAI

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

model = ChatOpenAI(model="qwen-flash")   # 千问：设 OPENAI_BASE_URL 指向 DashScope 兼容端点
def chat(state):
    return {"messages": [model.invoke(state["messages"])]}

g = StateGraph(State)
g.add_node("chat", chat)
g.add_edge(START, "chat")
g.add_edge("chat", END)

memory = InMemorySaver()
app = g.compile(checkpointer=memory)

cfg = {"configurable": {"thread_id": "t1"}}
app.invoke({"messages": [("human", "我叫小明")]}, config=cfg)
print(app.invoke({"messages": [("human", "我叫什么？")]}, config=cfg)["messages"][-1].content)
# 输出 "你叫小明"
```

第二次 `invoke` 时，LangGraph 先从 checkpointer 加载 state，再用 `add_messages` 把新消息合并进去，所以 LLM 能看到完整历史。

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

回到某个历史 checkpoint 重跑：

```python
history = list(app.get_state_history(cfg))
target = history[2]   # 想回去的那个

# 从这个 checkpoint 重新执行
app.invoke(None, config=target.config)
```

这就是 Time-travel：`invoke(None, 某个历史 config)` 会从那个 checkpoint 之后重新执行，模型也会重新调用。如果想先改值再跑，先 `update_state(old.config, {...})` 拿到一个新的 checkpoint 配置（相当于分叉），再对它 `invoke(None, 新配置)`。06 篇会展开讲。

> `get_state_history` 返回的 `metadata` 只有 `source`、`step`、`parents` 三个键，较早版本里的 `writes` 已经去掉了。想知道某一步写了什么，得对比相邻两个快照的 `values`。

---

## 6. update_state：手动改 State

```python
app.update_state(
    cfg,
    {"messages": [AIMessage(content="（人工修正版）")]},
)
```

效果相当于假装某个 node 输出了这份局部更新，同样会经过 reducer 合并。常见用途有两个：人工修正模型输出；测试时注入特定状态再续跑。

---

## 7. SqliteSaver 持久化文件

```python
from langgraph.checkpoint.sqlite import SqliteSaver

with SqliteSaver.from_conn_string("chat.db") as memory:
    app = g.compile(checkpointer=memory)
    app.invoke(..., config={"configurable": {"thread_id": "user1"}})
```

进程重启后用 `SqliteSaver.from_conn_string("chat.db")` 重新打开，所有 thread 的 state 都还在。

异步版是 `AsyncSqliteSaver`。

---

## 8. Postgres 生产部署

```python
from langgraph.checkpoint.postgres import PostgresSaver

DB_URI = "postgresql://user:pw@host:5432/db?sslmode=disable"

with PostgresSaver.from_conn_string(DB_URI) as checkpointer:
    checkpointer.setup()          # 首次创建表（幂等）
    app = g.compile(checkpointer=checkpointer)
    app.invoke(..., config=cfg)
```

异步用 `AsyncPostgresSaver`（`async with ... from_conn_string(...)`，`await checkpointer.setup()`）。`setup()` 建的主要是三张表：每个 step 的 state 快照（`checkpoints`）、增量写（`checkpoint_writes`）和大对象（`checkpoint_blobs`），另有一张记录迁移版本的 `checkpoint_migrations`。同一个 thread 的并发运行要在应用层串行化，别让两个请求同时跑同一个 `thread_id`。

> 如果把图部署到 LangSmith Deployment（Agent Server，见 [LangGraph 12：部署与 Studio](./12-deployment.md)），平台自带 PostgreSQL 检查点，这时不要自己传 checkpointer。

---

## 9. 跨会话长期记忆（Store）

Checkpointer 管的是一个 thread 内部的状态，也就是同一段对话；Store 管的是跨 thread 的长期数据，比如用户偏好和历史事实。

```python
from langgraph.store.memory import InMemoryStore
# 生产用 langgraph.store.postgres.PostgresStore（同样 from_conn_string + setup()）

store = InMemoryStore()
app = g.compile(checkpointer=checkpointer, store=store)
```

在 node 里访问：

```python
from langgraph.runtime import Runtime

def remember_pref(state, runtime: Runtime):
    # 写
    runtime.store.put(("users", "u_123"), "favorite_color", {"value": "blue"})
    # 读
    item = runtime.store.get(("users", "u_123"), "favorite_color")
    return {...}
```

老写法 `def node(state, *, store)` 也还能用；新代码推荐 `Runtime`，上下文、store、stream_writer 都从它上面取。

Store 也支持基于 embeddings 的语义检索：

```python
# 千问 embedding：OpenAIEmbeddings(model="text-embedding-v4", check_embedding_ctx_length=False)，dims 改成 1024
store = InMemoryStore(index={"embed": OpenAIEmbeddings(), "dims": 1536})
store.put(("users","u"), "fact-1", {"text": "用户喜欢蓝色"})
items = store.search(("users", "u"), query="favorite color", limit=3)
```

需要长期个性化的应用，用这个比自己维护一张偏好表省事。

---

## 10. 本章 demo

完整代码：[`demos/langgraph/05_persistence.py`](../../demos/langgraph/05_persistence.py)

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

model = ChatOpenAI(model="qwen-flash")   # 千问：设 OPENAI_BASE_URL 指向 DashScope 兼容端点

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
| compile 加了 checkpointer，invoke 却没带 thread_id | 这时 thread_id 必传 | `config={"configurable":{"thread_id":...}}` |
| 多用户串话 | 用了同一个 thread_id | 加用户隔离前缀 |
| SqliteSaver 文件越来越大 | 历史 checkpoint 一直累积 | 定期清理，或换 Postgres |
| node 里拿不到 store | 编译时没传 `store=`，或参数名写错 | `compile(store=...)`；节点签名用 `runtime: Runtime` 或 `(state, *, store)` |
| update_state 不生效 | thread 不对 | 用同一个 thread_id |

---

下一篇：[LangGraph 06：Human-in-the-loop 与 Time-travel](06-human-in-the-loop.md)
