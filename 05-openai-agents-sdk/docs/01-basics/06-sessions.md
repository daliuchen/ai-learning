# Sessions：会话状态自动管理

> 给 `Runner.run` 传一个 `session=`，SDK 会在每轮开始前取出历史消息拼进输入，跑完再把新内容写回去，多轮对话就不用自己维护 messages list 了。这篇讲内置的几种 session 后端、历史太长时怎么裁剪，以及 session 不该存什么。

---

## 1. 为什么需要 Sessions

不用 Sessions 的写法：

```python
messages = []
messages.append({"role": "user", "content": "我叫小明"})
r1 = await Runner.run(agent, messages)
messages = r1.to_input_list()

messages.append({"role": "user", "content": "我叫什么"})
r2 = await Runner.run(agent, messages)
```

每轮都得自己维护 messages list：先用 `to_input_list()` 把上一轮的结果拼回去，再追加新的用户消息。步骤不复杂，但哪次漏了一步，模型就丢了上下文。

---

## 2. 用 Sessions

```python
from agents import Agent, Runner, SQLiteSession

agent = Agent(name="A", instructions="...")

session = SQLiteSession("user_42")  # session_id

await Runner.run(agent, "我叫小明", session=session)
await Runner.run(agent, "我叫什么", session=session)
# 模型："你叫小明"
```

SDK 在每次 run 开始前从 session 取出历史消息，跑完再把新产生的 items 写回 session。

---

## 3. SQLiteSession：默认实现

```python
SQLiteSession(session_id="...", db_path=":memory:")  # 内存
SQLiteSession(session_id="...", db_path="sessions.db")  # 文件
```

默认 `db_path=":memory:"`，进程退出数据就没了。线上要传文件路径，或者换别的后端。

---

## 4. 操作 Session

```python
session = SQLiteSession("user_42", "sessions.db")

# 看历史
items = await session.get_items()
for item in items:
    print(item)

# 加一条
await session.add_items([
    {"role": "user", "content": "hi"},
])

# 清空
await session.clear_session()

# 弹出最后一条（重写场景）
last = await session.pop_item()
```

---

## 5. 内置后端与自定义 Session

0.0.x 的时候只有 SQLite，要用 Redis / Postgres 得自己写。现在 SDK 内置了不少后端（`agents.memory` / `agents.extensions.memory`）：

| 类 | 适合 | 备注 |
|----|------|------|
| `SQLiteSession` | 本地开发、简单应用 | 内置；`db_path` 不填=内存，填文件=持久化 |
| `AsyncSQLiteSession` | 异步 SQLite | 要 `aiosqlite` |
| `RedisSession` | 多进程/多实例共享 | `pip install "openai-agents[redis]"` |
| `SQLAlchemySession` | 已有数据库的生产应用 | `[sqlalchemy]`；`SQLAlchemySession.from_url("u42", url="postgresql+asyncpg://...", create_tables=True)` |
| `MongoDBSession` / `DaprSession` | 已有 MongoDB / Dapr 的团队 | 各有 extras |
| `OpenAIConversationsSession` | 历史存在 OpenAI 服务端 | Conversations API，仅 OpenAI |
| `OpenAIResponsesCompactionSession` | 超长对话自动压缩 | 包装别的 session，调用 `responses.compact`，仅 Responses |
| `AdvancedSQLiteSession` | 分支、用量分析 | SQLite 的增强版 |
| `EncryptedSession` | 落库加密 + TTL | 包装别的 session（`[encrypt]`） |

大多数情况直接用内置的就够了，比如 `SQLAlchemySession` 配 `sqlite+aiosqlite` 内存库，或者用 `EncryptedSession` 包一层 SQLite。要接自己的存储，继承 `SessionABC`（需要 `session_id`、`session_settings` 属性和 4 个异步方法）：

下面是一个最简单的 `DictSession`，作为 session 用时多轮记忆正常：

```python
import json
from agents import SessionSettings
from agents.memory import SessionABC


class DictSession(SessionABC):
    _store: dict = {}          # 演示用内存；换成 Redis/DB 同理

    def __init__(self, session_id: str, session_settings: SessionSettings | None = None):
        self.session_id = session_id
        self.session_settings = session_settings
        self.key = f"agents:session:{session_id}"

    async def get_items(self, limit: int | None = None):
        items = self._store.get(self.key, [])
        return items[-limit:] if limit else list(items)       # limit=最近 N 条

    async def add_items(self, items):
        self._store.setdefault(self.key, []).extend(json.loads(json.dumps(i, default=str)) for i in items)

    async def pop_item(self):
        s = self._store.get(self.key, [])
        return s.pop() if s else None

    async def clear_session(self):
        self._store.pop(self.key, None)
```

下面是自己用 Redis 实现的示例，仍然能用，不过直接用内置的 `RedisSession` 更省事：

```python
import json
from agents.memory import SessionABC
import redis.asyncio as redis


class MyRedisSession(SessionABC):
    session_settings = None

    def __init__(self, session_id: str, client: redis.Redis):
        self.session_id = session_id
        self.client = client
        self.key = f"agents:session:{session_id}"

    async def get_items(self, limit: int | None = None):
        raw = await self.client.lrange(self.key, 0, limit - 1 if limit else -1)
        return [json.loads(r) for r in raw]

    async def add_items(self, items):
        if not items:
            return
        await self.client.rpush(self.key, *[json.dumps(i, default=str) for i in items])
        # 可选：设过期
        await self.client.expire(self.key, 60 * 60 * 24 * 7)  # 7 天

    async def pop_item(self):
        raw = await self.client.rpop(self.key)
        return json.loads(raw) if raw else None

    async def clear_session(self):
        await self.client.delete(self.key)
```

用法：

```python
r = redis.Redis(host="localhost")
session = MyRedisSession("user_42", r)
await Runner.run(agent, "...", session=session)
```

---

## 6. 多用户 / 多会话隔离

```python
def session_for_user(user_id: str) -> SQLiteSession:
    return SQLiteSession(f"user_{user_id}", "sessions.db")


# FastAPI handler
@app.post("/chat/{user_id}")
async def chat(user_id: str, body: dict):
    session = session_for_user(user_id)
    result = await Runner.run(agent, body["message"], session=session)
    return {"reply": result.final_output}
```

`session_id` 是字符串，怎么编都行。

---

## 7. 跨多个 Agent 共享 Session

```python
session = SQLiteSession("user_42")

await Runner.run(billing_agent, "退款", session=session)
await Runner.run(support_agent, "另外有问题", session=session)
# support_agent 能看到刚才 billing 那轮历史
```

一条对话需要在几个专家 agent 之间流转时，可以这样共用一个 session。

---

## 8. handoff + Sessions

更常见的做法是用 handoffs，不用手动依次调用多个 Agent：

```python
triage = Agent(name="Triage", handoffs=[billing, support])
session = SQLiteSession("user_42")

await Runner.run(triage, "退款", session=session)
# 内部 handoff 到 billing，整段对话都在 session 里
```

---

## 9. 会话上限：上下文管理

session 里的 items 只增不减，prompt 会越来越长，token 开销也跟着涨。下面是几种控制办法。

### 策略 A：限制每次取回的历史条数（`SessionSettings`）

```python
from agents import RunConfig, SessionSettings

result = await Runner.run(
    agent, "总结一下刚才聊的", session=session,
    run_config=RunConfig(session_settings=SessionSettings(limit=50)),   # 只取最近 50 条 item
)
```

`limit=None`（默认）表示取全部。截断只发生在取回的时候，库里存的仍然是全量历史。

### 策略 A+：自己决定历史怎么和新输入合并（`session_input_callback`）

```python
def keep_recent(history, new_input):
    return history[-10:] + new_input     # 只保留最近 10 条历史

await Runner.run(agent, "...", session=session,
                 run_config=RunConfig(session_input_callback=keep_recent))
```

回调拿到的是副本，可以随意改。SDK 只会把本轮新产生的 items 写回 session，所以裁剪或重排旧历史不会导致旧消息被重复存储。如果要在调用模型前的最后一步再改输入（比如脱敏），用 `RunConfig(call_model_input_filter=...)`。

### 策略 A++：自动压缩（OpenAI Responses 专属）

```python
from agents import SQLiteSession
from agents.memory import OpenAIResponsesCompactionSession

session = OpenAIResponsesCompactionSession(
    session_id="conversation_123",
    underlying_session=SQLiteSession("conversation_123"),
)
```

每轮结束时检查阈值，超了就调 Responses 的 `compact` 重写历史。压缩期间 `run()` 和流式迭代器都会等它完成；对延迟敏感的话，可以设 `should_trigger_compaction=lambda _: False`，闲时手动 `await session.run_compaction({"force": True})`。第三方 Chat Completions 端点用不了。

### 策略 B：摘要 + 滚动

定期把旧历史压成一段摘要：

```python
async def compact_session(session, agent):
    items = await session.get_items()
    if len(items) < 30:
        return
    # 用一个 summarizer agent 把老的 20 条摘要
    old = items[:20]
    summary_result = await Runner.run(summarizer, str(old))
    await session.clear_session()
    await session.add_items([
        {"role": "system", "content": f"对话历史摘要: {summary_result.final_output}"},
    ])
    await session.add_items(items[20:])
```

---

## 10. Session 不存什么

Session 只存对话历史 items。用户 metadata（user_id、language）和业务对象（订单状态、token 余额）都不归它管，应该放进 context：`Runner.run(..., context=obj)`，工具里用 `RunContextWrapper[T]` 取出来，作用类似 Pydantic AI 的 deps。

另外两条约束：

- 同一次 run 里，session 不能和 `conversation_id` / `previous_response_id` / `auto_previous_response_id` 一起用，历史要么存在客户端，要么存在 OpenAI 服务端，只能选一种。
- 被审批挂起的 run 恢复时，要传同一个 session（或者同 ID、同后端的另一个实例）：`await Runner.run(agent, state, session=session)`。

---

## 11. 完整示例

```python
import asyncio
from agents import Agent, Runner, SQLiteSession

agent = Agent(name="Assistant", instructions="友好聊天，记住用户细节。")


async def main():
    session = SQLiteSession("demo_user", "session_demo.db")

    while True:
        msg = input("> ").strip()
        if not msg or msg in {"exit", "quit"}:
            break
        result = await Runner.run(agent, msg, session=session)
        print(result.final_output)


asyncio.run(main())
```

---

## 12. 和 Pydantic AI / LangChain 对比

| 框架 | 持久化 | 接口 |
|------|--------|------|
| OpenAI Agents | Session 接口（内置 SQLite / Redis / SQLAlchemy / Mongo / 加密 / 压缩…） | `Runner.run(..., session=)` |
| Pydantic AI | message_history list | `agent.run(..., message_history=)` |
| LangChain | RunnableWithMessageHistory + ChatMessageHistory | configurable_alternatives |

三者里 OpenAI Agents 的 Session 接口最简单，只有 `get_items / add_items / pop_item / clear_session` 四个方法，内置的后端反而最多。

---

## 13. 下一步

- 用守卫拦截 PII：[Input Guardrails：输入守卫](../04-guardrails/01-input-guardrails.md)
- 多 Agent 配合 Session：[Triage Pattern：分流模式](../03-handoffs/02-triage-pattern.md)
- 在 FastAPI 里用：[FastAPI / Lambda 部署](../06-integration/03-fastapi-deploy.md)
