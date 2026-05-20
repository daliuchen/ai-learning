# LangChain 09：Memory 记忆机制

> **一句话**：让 LLM 应用记住跨轮对话。LangChain 老版本提供了一大堆 `Memory` 类，新版本统一推荐 `RunnableWithMessageHistory`；更复杂的多轮/跨会话/长期记忆请用 LangGraph 的 checkpointer + Store。

---

## 1. 三种"记忆"层次

| 层次 | 含义 | 实现 |
|------|------|------|
| **短期记忆**（对话上下文） | 本次会话所有消息 | `BaseChatMessageHistory` + `RunnableWithMessageHistory` |
| **长期记忆**（跨会话事实） | 用户偏好、历史决策 | LangGraph `Store` / 向量库 |
| **工作记忆**（Agent 中间状态） | 一次任务的中间产物 | LangGraph `State` |

本章主讲短期记忆。长期/工作记忆见 LangGraph 章。

---

## 2. 老 Memory API（已废弃但你会在老项目见到）

```python
# ❌ 已弃用，仅了解
from langchain.memory import ConversationBufferMemory
from langchain.chains import ConversationChain

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

## 3. 新方式：BaseChatMessageHistory + RunnableWithMessageHistory

LangChain 把"消息存储"和"消息注入"解耦：

```
BaseChatMessageHistory  ← 存储接口（内存 / Redis / SQL / 自定义）
        +
RunnableWithMessageHistory ← 把"存储 → 注入 placeholder"自动化
```

### 3.1 最简内存版

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

chain = prompt | ChatOpenAI(model="gpt-4o-mini")

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

### 3.2 持久化存储

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
    return SQLChatMessageHistory(session_id, connection_string="sqlite:///memory.db")
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

### 3.3 配合 RAG / 工具调用

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

## 4. 长上下文：摘要 + 滚动窗口

历史无限增长会把上下文撑爆。两种典型策略：

### 4.1 截断（最近 N 条）

```python
from langchain_core.messages import trim_messages

def get_history(session_id):
    raw = store.setdefault(session_id, InMemoryChatMessageHistory())
    return raw

chain = (
    prompt
    | (lambda x: {**x, "history": trim_messages(x["history"], max_tokens=2000, token_counter=model, strategy="last")})
    | model
)
```

`trim_messages` 高级用法：

```python
from langchain_core.messages import trim_messages
trimmed = trim_messages(
    messages,
    max_tokens=2000,
    token_counter=model,         # 或自定义函数
    strategy="last",             # last / first
    start_on="human",            # 必须从某种消息开头
    end_on=("human", "tool"),    # 必须以某种消息结尾
    allow_partial=False,
    include_system=True,         # 保留 system message
)
```

### 4.2 摘要（自动总结老对话）

```python
async def summarize_if_long(messages, threshold=20):
    if len(messages) < threshold:
        return messages
    old = messages[:-10]
    recent = messages[-10:]
    summary = await summary_chain.ainvoke({"messages": old})
    return [SystemMessage(content=f"对话摘要：{summary}")] + recent
```

LangGraph 的 `SummarizationNode` 提供更完整封装，第 18 章会讲。

---

## 5. 别忘了 System Prompt 也要管理

很多人写"个性化"应用时把 system 写死，导致同一个用户每次都被介绍一遍："你叫张三..."。**正确做法**：

```python
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {user_name} 的私人助理。上次他说自己{user_pref}。"),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])
```

user_name / user_pref 从长期记忆（LangGraph Store / 数据库）查出来填进去。

---

## 6. 异步与流式

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

## 7. 实战 demo

```python
# demos/langchain/09_memory.py
from dotenv import load_dotenv
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import trim_messages
from langchain_openai import ChatOpenAI

load_dotenv()

model = ChatOpenAI(model="gpt-4o-mini")
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是助手，记住用户告诉你的信息。"),
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])

# 简单截断 wrapper
trimmer = lambda msgs: trim_messages(
    msgs, max_tokens=1000, token_counter=model, strategy="last",
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

## 8. 何时不要用 RunnableWithMessageHistory，改用 LangGraph

| 场景 | 推荐 |
|------|------|
| 简单聊天，单一 chain | RunnableWithMessageHistory |
| 多步 Agent + 工具调用 | **LangGraph + checkpointer** |
| 需要状态分支 / 中断 / 回放 | **LangGraph** |
| 多 Agent 协作 | **LangGraph** |
| 跨用户长期事实存储 | **LangGraph Store + 向量化** |

---

## 9. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `session_id` 不传报错 | RunnableWithMessageHistory 必须显式 configurable | `config={"configurable":{"session_id":"..."}}` |
| 历史无限增长 | 没截断 | 加 `trim_messages` |
| 多用户串号 | 全局变量做 store 没线程隔离 | 用 Redis / SQLite |
| 流式时 history 没保存 | astream 异步分支没等完整 | LangChain 0.3+ 已修复，确保升级 |

---

## 10. 本章 demo

[`demos/langchain/09_memory.py`](../../demos/langchain/09_memory.py)

下一篇：[10-document-loaders.md](10-document-loaders.md)
