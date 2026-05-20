# Sessions：会话状态自动管理

> **一句话**：传 `session=` 参数，多轮对话自动续接——SDK 自动把历史 messages 拼到下一轮，省得你手动维护 messages list。

---

## 1. 为啥要 Sessions

不用 Sessions 的写法：

```python
messages = []
messages.append({"role": "user", "content": "我叫小明"})
r1 = await Runner.run(agent, messages)
messages = r1.to_input_list()

messages.append({"role": "user", "content": "我叫啥"})
r2 = await Runner.run(agent, messages)
```

每轮要：

1. 维护 messages list
2. 调 `to_input_list()` 把上轮结果拼回去
3. 把新 user message 加进去

容易忘 / 错。

---

## 2. 用 Sessions

```python
from agents import Agent, Runner, SQLiteSession

agent = Agent(name="A", instructions="...")

session = SQLiteSession("user_42")  # session_id

await Runner.run(agent, "我叫小明", session=session)
await Runner.run(agent, "我叫啥", session=session)
# 模型："你叫小明"
```

SDK 内部：

1. 跑前从 session 拿历史 messages
2. 跑完把新产生的 items 写回 session

---

## 3. SQLiteSession：默认实现

```python
SQLiteSession(session_id="...", db_path=":memory:")  # 内存
SQLiteSession(session_id="...", db_path="sessions.db")  # 文件
```

默认 `db_path=":memory:"`——进程退出就没了。生产用文件或自定义。

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

## 5. 自定义 Session（接 Redis / PostgreSQL）

实现 `Session` 协议：

```python
# demos/basics/06_redis_session.py
import json
from agents import Session
import redis.asyncio as redis


class RedisSession(Session):
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
session = RedisSession("user_42", r)
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

适合：单一对话流跨多个专家 agent。

---

## 8. handoff + Sessions

更常见的是用 handoffs（不需要手动调多个 Agent）：

```python
triage = Agent(name="Triage", handoffs=[billing, support])
session = SQLiteSession("user_42")

await Runner.run(triage, "退款", session=session)
# 内部 handoff 到 billing，整段对话都在 session 里
```

---

## 9. 会话上限：上下文管理

session 里 items 一直涨 → prompt 越来越长 → 烧 token。

策略：

### 策略 A：限制最近 N 条

```python
# 自定义 session 的 get_items 加 limit
items = await session.get_items(limit=20)
```

### 策略 B：摘要 + 滚动

定期摘要老历史：

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

## 10. Session 不存啥

不会自动存：

- 用户 metadata（user_id, language）：放 `context` 参数
- 业务对象（订单状态、token 余额）：放 `context`

Session 只存"对话历史 items"。其它放 context（详见 [Context 章节](#)，或参考 Pydantic AI 的 deps）。

---

## 11. 完整 demo

```python
# demos/basics/06_session_chat.py
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

## 12. 跟 Pydantic AI / LangChain 对比

| 框架 | 持久化 | 接口 |
|------|--------|------|
| OpenAI Agents | Session 接口（内置 SQLite） | `Runner.run(..., session=)` |
| Pydantic AI | message_history list | `agent.run(..., message_history=)` |
| LangChain | RunnableWithMessageHistory + ChatMessageHistory | configurable_alternatives |

OpenAI Agents 的 Session 抽象**最简单**——只有 `get_items / add_items / pop_item / clear`。

---

## 13. 下一步

- 📖 加守卫拦 PII → [04-guardrails/01-input-guardrails.md](../04-guardrails/01-input-guardrails.md)
- 📖 多 Agent + Session → [03-handoffs/02-triage-pattern.md](../03-handoffs/02-triage-pattern.md)
- 📖 部署：在 FastAPI 里用 → [06-integration/03-fastapi-deploy.md](../06-integration/03-fastapi-deploy.md)
