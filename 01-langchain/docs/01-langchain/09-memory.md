# LangChain 09：Memory 记忆机制

> 这篇讲怎么让 LLM 应用记住前几轮说过的话。老版本 LangChain 有一大堆 `Memory` 类，现在都搬进了 `langchain-classic`。1.x 推荐的组合是 `create_agent` 加 checkpointer 管短期记忆、Store 管长期记忆；没有工具的简单聊天链才用 `RunnableWithMessageHistory`。

---

## 1. 三种记忆层次

| 层次 | 含义 | 实现 |
|------|------|------|
| 短期记忆（对话上下文） | 本次会话所有消息 | `create_agent(checkpointer=...)`；或链式场景 `RunnableWithMessageHistory` |
| 长期记忆（跨会话事实） | 用户偏好、历史决策 | LangGraph `Store` / 向量库 |
| 工作记忆（Agent 中间状态） | 一次任务的中间产物 | agent 的 `state`（`AgentState` 可扩展字段） |

本章先讲 1.x 推荐的 agent 记忆，再讲 `RunnableWithMessageHistory`（链式场景）。长期记忆见 LangGraph 05 篇。

---

## 2. 推荐做法：create_agent + checkpointer

agent 的短期记忆就是它的 `messages` 状态。裸 `invoke` 是无状态的；挂上 `checkpointer` 并用 `thread_id` 圈定会话，同一个 `thread_id` 的下一次调用自动接上历史：

```python
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

agent = create_agent(model, tools=[...], checkpointer=InMemorySaver())
cfg = {"configurable": {"thread_id": "u1"}}

agent.invoke({"messages": [{"role": "user", "content": "我叫小明"}]}, cfg)
out = agent.invoke({"messages": [{"role": "user", "content": "我叫什么？"}]}, cfg)
print(out["messages"][-1].text)     # 你叫小明。
```

`InMemorySaver` 只在进程内；生产换 `SqliteSaver` / `PostgresSaver`（`pip install langgraph-checkpoint-sqlite` / `-postgres`），就能跨重启。

对话一长会撑爆上下文，1.x 用中间件解决，不用手写：

```python
from langchain.agents.middleware import SummarizationMiddleware

agent = create_agent(
    model, tools=[...], checkpointer=InMemorySaver(),
    middleware=[SummarizationMiddleware(model="openai:gpt-6-luna", trigger=("tokens", 4000), keep=("messages", 20))],
)
```

另外也可以用 `@before_model` 中间件返回 `RemoveMessage` 裁剪或删除旧消息。这里有个细节：`messages` 的 reducer 是追加式的，得先清空再放回，也就是返回 `RemoveMessage(id=REMOVE_ALL_MESSAGES)`，再加上要保留的消息。

---

## 3. 老 Memory API（已废弃但你会在老项目见到）

```python
# ❌ 已弃用，仅了解。1.x 里 `from langchain.memory import ...` 会直接 ModuleNotFoundError，
# 只存在于 pip install langchain-classic 之后的 langchain_classic.memory
from langchain_classic.memory import ConversationBufferMemory
from langchain_classic.chains import ConversationChain

memory = ConversationBufferMemory()
chain = ConversationChain(llm=llm, memory=memory)
chain.predict(input="我叫小明")
chain.predict(input="我叫什么？")
```

老 API 的 Memory 类型一览（看到这些名字直接换新 API）：
- `ConversationBufferMemory`：全量缓存
- `ConversationBufferWindowMemory`：只留最近 K 条
- `ConversationSummaryMemory`：用 LLM 自动摘要
- `ConversationSummaryBufferMemory`：摘要 + 最近 K 条
- `ConversationTokenBufferMemory`：按 token 数截断
- `VectorStoreRetrieverMemory`：把每条记忆存向量库

---

## 4. 链式场景：BaseChatMessageHistory + RunnableWithMessageHistory

没有工具调用、只是「提示 → 模型 → 解析」的简单聊天链，仍然可以用它，它在 `langchain-core` 里，没被删掉。LangChain 把消息的存储和注入拆成了两件事：

```
BaseChatMessageHistory  ← 存储接口（内存 / Redis / SQL / 自定义）
        +
RunnableWithMessageHistory ← 把"存储 → 注入 placeholder"自动化
```

### 4.1 最简内存版

```python
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是友好的助手。"),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])

chain = prompt | ChatOpenAI(model="gpt-6-luna")

store: dict[str, InMemoryChatMessageHistory] = {}

def get_history(session_id: str):
    if session_id not in store:
        store[session_id] = InMemoryChatMessageHistory()
    return store[session_id]

bot = RunnableWithMessageHistory(
    chain,
    get_history,
    input_messages_key="input",
    history_messages_key="history",
)

# 同一 session_id 共享历史
cfg = {"configurable": {"session_id": "u1"}}
bot.invoke({"input": "我叫小明"}, config=cfg)
print(bot.invoke({"input": "我叫什么？"}, config=cfg).content)
# "你叫小明。"
```

### 4.2 持久化存储

#### Redis

```python
from langchain_community.chat_message_histories import RedisChatMessageHistory

def get_history(session_id):
    return RedisChatMessageHistory(session_id, url="redis://localhost:6379/0")
```

#### SQLite / MySQL / Postgres

```python
from langchain_community.chat_message_histories import SQLChatMessageHistory

def get_history(session_id):
    # 参数名是 connection（旧的 connection_string 已弃用）；需要 pip install greenlet
    return SQLChatMessageHistory(session_id, connection="sqlite:///memory.db")
```

#### 文件 / 自定义

继承 `BaseChatMessageHistory`，实现：

```python
class MyHistory(BaseChatMessageHistory):
    def __init__(self, session_id): ...
    @property
    def messages(self) -> List[BaseMessage]: ...
    def add_messages(self, messages: List[BaseMessage]) -> None: ...
    def clear(self) -> None: ...
```

### 4.3 配合 RAG / 工具调用

`RunnableWithMessageHistory` 支持任意 chain 输入：

```python
bot = RunnableWithMessageHistory(
    chain,
    get_history,
    input_messages_key="input",
    history_messages_key="history",
    output_messages_key="answer",   # 如果 chain 输出 dict
)
```

`history_messages_key` 必须和 prompt 里 placeholder 的 key 一致。

---

## 5. 长上下文：摘要 + 滚动窗口

历史一直增长，迟早会把上下文撑爆。常用的办法有两种：

### 5.1 截断（最近 N 条）

```python
from langchain_core.messages import trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnablePassthrough

chain = (
    RunnablePassthrough.assign(   # 在进 prompt 之前裁剪 history
        history=lambda x: trim_messages(
            x["history"], max_tokens=2000,
            token_counter=count_tokens_approximately, strategy="last",
        )
    )
    | prompt
    | model
)
```

裁剪要放在 `prompt` 前面：`prompt` 之后的输入已经是 `ChatPromptValue`，不再是带 `history` 键的 dict。

> `token_counter=model` 只对 OpenAI 官方模型可用（它要查 tiktoken 词表），换成千问等兼容模型会抛 `NotImplementedError: get_num_tokens_from_messages() is not presently implemented for model ...`。换成别的模型时，传 `count_tokens_approximately`（粗略估算，在 `langchain_core.messages.utils` 里），或者传 `len`，按消息条数裁剪。

`trim_messages` 高级用法：

```python
from langchain_core.messages import trim_messages
trimmed = trim_messages(
    messages,
    max_tokens=2000,
    token_counter=count_tokens_approximately,   # 或 len / 自定义函数；传 model 仅限 OpenAI 官方模型
    strategy="last",             # last / first
    start_on="human",            # 必须从某种消息开头
    end_on=("human", "tool"),    # 必须以某种消息结尾
    allow_partial=False,
    include_system=True,         # 保留 system message
)
```

### 5.2 摘要（自动总结老对话）

```python
async def summarize_if_long(messages, threshold=20):
    if len(messages) < threshold:
        return messages
    old = messages[:-10]
    recent = messages[-10:]
    summary = await summary_chain.ainvoke({"messages": old})
    return [SystemMessage(content=f"对话摘要：{summary}")] + recent
```

agent 里直接用 `SummarizationMiddleware`（见第 2 节），不用自己写。

---

## 6. 别忘了 System Prompt 也要管理

做个性化应用时，很多人把 system prompt 写死，结果同一个用户每次都要被重新介绍一遍「你叫张三……」。更好的写法是把用户信息做成变量：

```python
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {user_name} 的私人助理。上次他说自己{user_pref}。"),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])
```

user_name / user_pref 从长期记忆（LangGraph Store / 数据库）查出来填进去。

---

## 7. 异步与流式

`RunnableWithMessageHistory` 完全支持 `ainvoke / astream / astream_events`：

```python
async for ev in bot.astream_events(
    {"input": "..."},
    config={"configurable": {"session_id": "u1"}},
    version="v2",
):
    ...
```

---

## 8. 实战 demo

完整代码：[`demos/langchain/09_memory.py`](../../demos/langchain/09_memory.py)

```python
# demos/langchain/09_memory.py
from dotenv import load_dotenv
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import trim_messages
from langchain_openai import ChatOpenAI

load_dotenv()

model = ChatOpenAI(model="gpt-6-luna")
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是助手，记住用户告诉你的信息。"),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])

# 简单截断 wrapper
trimmer = lambda msgs: trim_messages(
    msgs, max_tokens=6, token_counter=len, strategy="last",
    include_system=True, start_on="human",
)
chain = (
    {
        "input": lambda x: x["input"],
        "history": lambda x: trimmer(x["history"]),
    }
    | prompt
    | model
)

store = {}
def get_history(sid):
    return store.setdefault(sid, InMemoryChatMessageHistory())

bot = RunnableWithMessageHistory(
    chain, get_history,
    input_messages_key="input",
    history_messages_key="history",
)

cfg = {"configurable": {"session_id": "u1"}}
print(bot.invoke({"input": "我叫小明，喜欢猫"}, config=cfg).content)
print(bot.invoke({"input": "我喜欢什么动物？"}, config=cfg).content)
```

---

## 9. 何时不要用 RunnableWithMessageHistory，改用 LangGraph

| 场景 | 推荐 |
|------|------|
| 简单聊天，单一 chain | RunnableWithMessageHistory |
| 多步 Agent + 工具调用 | `create_agent` + checkpointer |
| 需要状态分支 / 中断 / 回放 | LangGraph |
| 多 Agent 协作 | LangGraph / `create_agent` 子 agent |
| 跨用户长期事实存储 | LangGraph Store（可带向量检索） |

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `session_id` 不传报错 | RunnableWithMessageHistory 必须显式 configurable | `config={"configurable":{"session_id":"..."}}` |
| 历史无限增长 | 没截断 | 加 `trim_messages` |
| 重启或多实例部署后历史丢失、各实例看到的不一致 | 进程内 dict 做 store，只在当前进程有效 | 用 Redis / SQL 等外部存储 |
| `trim_messages` 报 `get_num_tokens_from_messages ... not implemented for model` | `token_counter=model` 只支持 OpenAI 官方模型 | 改 `count_tokens_approximately` 或 `len` |
| `from langchain.memory import ...` 报错 | 1.x 已移到 langchain-classic | `from langchain_classic.memory import ...`（或直接迁到 checkpointer） |

---

下一篇：[LangChain 10：Document Loaders 与 Text Splitters](10-document-loaders.md)
