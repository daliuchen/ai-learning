# Pydantic AI 07：消息与对话历史（Messages & History）

> **一句话**：Pydantic AI 把对话历史抽象成 `ModelMessage` 列表，每条消息由若干 `Part` 组成，**可序列化、可持久化、可回放**，多轮聊天只要把上次 `result.new_messages()` 喂进下一次 `run`。

---

## 1. 为什么不直接用字符串

朴素的多轮聊天容易这样写：

```python
# ❌ 朴素拼接
history = ""
while True:
    user = input(">>> ")
    history += f"User: {user}\n"
    resp = call_llm(history)
    history += f"AI: {resp}\n"
```

问题：

1. 模型分不清谁说的（要靠 prefix 约定）
2. **工具调用历史**怎么塞进去？字符串拼不出 JSON 结构
3. 切换模型时格式全乱（OpenAI / Anthropic 消息格式不同）
4. 想存 DB / Redis：纯字符串没结构，回放 / 审计困难

Pydantic AI 把对话建模成**强类型对象**：

```
ModelMessage = ModelRequest | ModelResponse
   │
   ├── ModelRequest  → parts: SystemPromptPart / UserPromptPart / ToolReturnPart / RetryPromptPart
   │
   └── ModelResponse → parts: TextPart / ToolCallPart / ThinkingPart
```

---

## 2. 消息类型一览

| 类 | 含义 | 出现在 |
|----|------|--------|
| `ModelRequest` | 发给模型的一轮输入 | 历史 / 当前请求 |
| `ModelResponse` | 模型的一轮回复 | 历史 |
| `SystemPromptPart` | 系统提示 | `ModelRequest` |
| `UserPromptPart` | 用户消息（可含多模态） | `ModelRequest` |
| `TextPart` | 模型文本输出 | `ModelResponse` |
| `ToolCallPart` | 模型发起的工具调用 | `ModelResponse` |
| `ToolReturnPart` | 工具返回结果 | `ModelRequest` |
| `RetryPromptPart` | 校验失败后告诉模型 retry | `ModelRequest` |
| `ThinkingPart` | 思维链（Claude/o1 等） | `ModelResponse` |

一次 Agent run 完，`result.all_messages()` 大致长这样：

```python
[
    ModelRequest(parts=[SystemPromptPart('...'), UserPromptPart('北京天气')]),
    ModelResponse(parts=[ToolCallPart(tool_name='get_weather', args={'city':'北京'})]),
    ModelRequest(parts=[ToolReturnPart(tool_name='get_weather', content='晴 26°C')]),
    ModelResponse(parts=[TextPart('北京今天晴，26 度。')]),
]
```

---

## 3. 拿到消息：4 个 API

```python
r = agent.run_sync("hi")

r.output              # 最终结果
r.all_messages()      # list[ModelMessage]，含 system / user / 工具调用全过程
r.new_messages()      # 本次 run 新增的（不含传入的 message_history）
r.all_messages_json() # bytes，JSON
r.new_messages_json() # bytes，JSON
```

**关键区分**：

- `all_messages()`：写日志、审计、回放
- `new_messages()`：作为下一轮 `message_history` 传入（只追加新增）

---

## 4. 多轮对话：`message_history` 参数

```python
from pydantic_ai import Agent

agent = Agent("openai:gpt-4o-mini", system_prompt="你是一位段子手。")

# 第一轮
r1 = agent.run_sync("讲个 Python 笑话")
print(r1.output)

# 第二轮，把上一轮的消息传进去
r2 = agent.run_sync("解释一下笑点", message_history=r1.new_messages())
print(r2.output)

# 第三轮，继续累加
r3 = agent.run_sync("再讲个类似的", message_history=r2.all_messages())
print(r3.output)
```

注意：

- **`message_history` 传 `new_messages()` 还是 `all_messages()` 都行**，但 `all_messages()` 会把 system 重复
- 标准做法：第二轮起统一用 `r_prev.new_messages()`，第一轮自己注入 system
- **Pydantic AI 会自动去重 system**（同一份 system_prompt 不会重复发送）

---

## 5. 序列化与持久化

存数据库 / Redis 时把消息转 JSON：

```python
from pydantic_ai.messages import ModelMessagesTypeAdapter

# 序列化
data: bytes = ModelMessagesTypeAdapter.dump_json(r1.all_messages())
# 存到 DB / Redis / 文件
db.set("conv:1", data)

# 反序列化
raw = db.get("conv:1")
messages = ModelMessagesTypeAdapter.validate_json(raw)

# 继续对话
r2 = agent.run_sync("继续", message_history=messages)
```

`ModelMessagesTypeAdapter` 是 Pydantic 的 `TypeAdapter`，比 `json.dump` 强：

- 自动处理 datetime / Enum / bytes
- 校验反序列化结果是合法 ModelMessage
- 跨版本兼容性最好

---

## 6. 实战：SQLite 持久化聊天

```python
import sqlite3
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter

conn = sqlite3.connect("chat.db")
conn.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, messages BLOB)")
conn.commit()

agent = Agent("openai:gpt-4o-mini", system_prompt="你是一个友好的助手。")

def chat(conv_id: str, prompt: str) -> str:
    row = conn.execute("SELECT messages FROM conversations WHERE id=?", (conv_id,)).fetchone()
    history = ModelMessagesTypeAdapter.validate_json(row[0]) if row else []

    r = agent.run_sync(prompt, message_history=history)

    new_blob = ModelMessagesTypeAdapter.dump_json(r.all_messages())
    conn.execute(
        "INSERT INTO conversations(id, messages) VALUES (?, ?) "
        "ON CONFLICT(id) DO UPDATE SET messages=excluded.messages",
        (conv_id, new_blob),
    )
    conn.commit()
    return r.output

print(chat("u1", "我叫刘晨"))
print(chat("u1", "我刚刚说我叫什么？"))  # 应该记得名字
```

要点：

- 每条 conversation 一行，存全量 messages
- 也可以拆表：messages 一行一条，方便分页（牺牲一些写入便利性）

---

## 7. Token 计数

`r.usage()` 拿 token 用量：

```python
r = agent.run_sync("hi")
print(r.usage())
# Usage(requests=1, request_tokens=20, response_tokens=8, total_tokens=28)
```

`message_history` 越长，下一次 `request_tokens` 越多。**生产环境必须做截断**：

```python
def trim(messages, keep_last=10):
    # 保留 system + 最近 N 条
    sys_msgs = [m for m in messages if any(
        p.__class__.__name__ == "SystemPromptPart" for p in m.parts
    )]
    others = [m for m in messages if m not in sys_msgs]
    return sys_msgs + others[-keep_last:]
```

更进一步可以用 LLM 做摘要 + 滚动窗口（参考 LangChain 的 `ConversationSummaryBufferMemory`）。

---

## 8. 直接构造消息（高级）

不通过 `run` 也能手工构造消息送进去：

```python
from pydantic_ai.messages import ModelRequest, UserPromptPart, SystemPromptPart

history = [
    ModelRequest(parts=[
        SystemPromptPart(content="你是一位 SQL 专家。"),
        UserPromptPart(content="先前我说要用 PostgreSQL"),
    ]),
]

r = agent.run_sync("写一个查询所有用户的 SQL", message_history=history)
```

**注意**：通常你不需要这么写，但下面场景有用：

- 从其他系统（LangChain / 自有 ORM）迁移历史进来
- 单元测试时构造特定状态
- "假装 AI 之前说过 XX" 来引导后续对话

---

## 9. 检查模型用了哪些工具

```python
from pydantic_ai.messages import ToolCallPart, ToolReturnPart

r = agent.run_sync("北京和杭州的天气")
for msg in r.all_messages():
    for part in msg.parts:
        if isinstance(part, ToolCallPart):
            print(f"调用工具：{part.tool_name}({part.args})")
        elif isinstance(part, ToolReturnPart):
            print(f"工具返回：{part.content}")
```

适合做**审计日志 / 调试 / Logfire trace 追踪**。

---

## 10. vs LangChain

| 任务 | LangChain | Pydantic AI |
|------|-----------|-------------|
| 消息类 | `HumanMessage` / `AIMessage` / `ToolMessage` | `ModelRequest` / `ModelResponse` 含多种 Part |
| 多轮历史 | `messages=[HumanMessage(...), AIMessage(...)]` | `message_history=r.new_messages()` |
| 持久化 | 手写 `model_dump` / `model_validate` | `ModelMessagesTypeAdapter.dump_json/validate_json` |
| 内置 Memory | `ConversationBufferMemory` 等一打 | 没有，**鼓励你自己写**（更简单） |
| Token 计数 | `model.get_num_tokens(...)` | `r.usage()` |

哲学差异：LangChain 提供"开箱即用的 Memory 类"，Pydantic AI 让你**显式管理 messages 列表**，简单但你要自己写存取。对长期项目反而清晰。

LangChain 等价：

```python
from langchain_core.messages import HumanMessage, AIMessage

messages = []
messages.append(HumanMessage("我叫刘晨"))
ai = model.invoke(messages)
messages.append(ai)
messages.append(HumanMessage("我叫什么？"))
print(model.invoke(messages).content)
```

Pydantic AI 等价：

```python
r1 = agent.run_sync("我叫刘晨")
r2 = agent.run_sync("我叫什么？", message_history=r1.new_messages())
print(r2.output)
```

---

## 11. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| 模型不记得上文 | 没传 `message_history` | 每次都传上一轮 `r.new_messages()` |
| `message_history` 越来越长，最后 OOM | 没截断 | 加滚动窗口或定期摘要 |
| 切了模型后历史报错 | 不同 provider 的工具调用 ID 不兼容 | 启动新会话或丢掉工具相关消息 |
| `all_messages()` 里 system 重复 | 把 system 也拼到 history 又传 | 用 `new_messages()`，让 agent 自己处理 system |
| JSON 序列化里有 `<bytes>` 字段 | 多模态图片用 bytes | `ModelMessagesTypeAdapter` 已处理，自己 `json.dumps` 不行 |
| 复用 `r.all_messages()` 作为新轮历史，token 翻倍 | system + 历史重复 | 用 `r.new_messages()` 接续 |
| 工具调用 ID 不匹配报错 | 手动改了 messages 但 ID 没对上 | 不要乱删 `ToolCallPart` / `ToolReturnPart`，要删一起删 |

---

## 12. 完整示例：CLI 多轮聊天

```python
from pydantic_ai import Agent

agent = Agent("openai:gpt-4o-mini", system_prompt="你是一位友好的助手。")

messages = []
while True:
    user = input(">>> ")
    if user.lower() in {"exit", "quit"}:
        break
    r = agent.run_sync(user, message_history=messages)
    print(r.output)
    messages = r.all_messages()  # 累积
```

加持久化：

```python
import json, pathlib
from pydantic_ai.messages import ModelMessagesTypeAdapter

PATH = pathlib.Path("history.json")
messages = ModelMessagesTypeAdapter.validate_json(PATH.read_bytes()) if PATH.exists() else []

# ... 同上 ...

PATH.write_bytes(ModelMessagesTypeAdapter.dump_json(messages))
```

---

## 13. 本章 demo

完整可运行代码：[`demos/basics/07_messages_history.py`](../../demos/basics/07_messages_history.py)

至此 **01-basics 七篇全部完成**！

接下来进入 [02-tools/01-function-tools.md](../02-tools/01-function-tools.md) —— 工具系统。
