# Pydantic AI 07：消息与对话历史（Messages & History）

> Pydantic AI 把对话历史表示成 `ModelMessage` 列表，每条消息由若干 `Part` 组成，能序列化、存库、回放。多轮聊天时把 `conversation=result.conversation` 传回去（或者把消息列表传给 `message_history`），存到哪里由你决定。

以 `pydantic-ai 2.64.0` 为准。V2 废弃了 `RetryPromptPart`，`result.usage()` 改成属性 `result.usage`。

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

这种写法的问题：

1. 模型分不清谁说的（要靠 prefix 约定）
2. 工具调用的历史没法放进去，字符串拼不出 JSON 结构
3. 切换模型时格式全乱（OpenAI / Anthropic 消息格式不同）
4. 想存 DB / Redis：纯字符串没结构，回放 / 审计困难

Pydantic AI 把对话建模成强类型对象：

```
ModelMessage = ModelRequest | ModelResponse
   │
   ├── ModelRequest  → parts: SystemPromptPart / UserPromptPart / ToolReturnPart / RetryFeedbackPart ...
   │                   （还有一个 instructions 字段：这次请求用的 instructions）
   └── ModelResponse → parts: TextPart / ToolCallPart / ThinkingPart / FilePart / NativeToolCallPart ...
```

---

## 2. 消息类型一览

| 类 | 含义 | 出现在 |
|----|------|--------|
| `ModelRequest` | 发给模型的一轮输入 | 历史 / 当前请求 |
| `ModelResponse` | 模型的一轮回复 | 历史 |
| `SystemPromptPart` | 系统提示（`system_prompt=` 产生的） | `ModelRequest` |
| `UserPromptPart` | 用户消息（可含多模态） | `ModelRequest` |
| `TextPart` | 模型文本输出 | `ModelResponse` |
| `ToolCallPart` | 模型发起的工具调用 | `ModelResponse` |
| `ToolReturnPart` | 工具返回结果；被要求重试时是 `outcome="retried"` | `ModelRequest` |
| `RetryFeedbackPart` | 输出校验失败等「告诉模型改正」的反馈（V2） | `ModelRequest` |
| `ThinkingPart` | 思维链（Claude / o 系列 / 千问思考模式） | `ModelResponse` |
| `ModelSettingsDeltaPart` 等 | 记录中途变化（如思考强度、工具可用性、能力加载） | `ModelRequest` |
| `RetryPromptPart` | 已废弃；旧历史反序列化仍兼容 | — |

> V2 的一处重要变化：`RetryPromptPart` 被废弃。模型被要求重试时，回到消息历史里的是那次工具调用自己的 `ToolReturnPart(outcome='retried')`，或者一个 `RetryFeedbackPart`。重试本身照常工作，但检查消息里 `isinstance(part, RetryPromptPart)` 的代码（尤其是测试辅助函数）不再匹配，要改认新部件。

`instructions` 没有对应的 part，它是 `ModelRequest.instructions` 字段。所以用 `message_history` 续聊时旧的 instructions 不会被重复发送，而 `system_prompt`（`SystemPromptPart`）会留在历史里。

一次 Agent run 完，`result.all_messages()` 大致长这样（`instructions` 给了的话挂在第一个 `ModelRequest` 上）：

```python
[
    ModelRequest(parts=[UserPromptPart('北京天气')], instructions='你是一位天气助手'),
    ModelResponse(parts=[ToolCallPart(tool_name='get_weather', args={'city': '北京'})]),
    ModelRequest(parts=[ToolReturnPart(tool_name='get_weather', content='晴 26°C')]),
    ModelResponse(parts=[TextPart('北京今天晴，26 度。')]),
]
```

---

## 3. 拿到消息：几个 API

```python
r = agent.run_sync("hi")

r.output              # 最终结果
r.all_messages()      # list[ModelMessage]，含用户 / 工具调用全过程（传入的历史也算）
r.new_messages()      # 本次 run 新增的（不含传入的 message_history）
r.all_messages_json() # bytes，JSON
r.new_messages_json() # bytes，JSON
r.conversation        # Conversation：消息 + 累计用量 + conversation_id
r.run_id, r.conversation_id   # 关联日志 / trace 用
```

两者的用途：

- `all_messages()`：写日志、审计、回放、存库
- `new_messages()`：只追加新增的部分（比如你自己每轮存增量）

---

## 4. 多轮对话：`conversation` 与 `message_history`

接上一轮有两种写法：

```python
from pydantic_ai import Agent

agent = Agent("openai-chat:qwen-flash", instructions="你是一位段子手。")

r1 = agent.run_sync("讲个 Python 笑话")

# 推荐：传 Conversation（带着累计用量，UsageLimits 管的是整段对话的总量）
r2 = agent.run_sync("解释一下笑点", conversation=r1.conversation)

# 也可以：只传消息
r3 = agent.run_sync("再讲个类似的", message_history=r2.all_messages())
```

- `message_history` 传 `new_messages()` 还是 `all_messages()`，结果差别很大：`all_messages()` 才是到目前为止的完整对话，只传 `new_messages()` 会丢掉更早的轮次。一般每轮传上一轮的 `all_messages()`，或直接传 `conversation`；
- 传了 `message_history` 时，agent 用当前的 `instructions`，历史里旧的 instructions 不再发送（`system_prompt` 则保留）；
- `run_sync` 内部自己开事件循环，在 async 代码或 async 工具里要用 `await agent.run(...)`。

---

## 5. 序列化与持久化

Pydantic AI 有意不替你选数据库，只提供一个保真的序列化边界：

```python
from pydantic_ai import Agent, ConversationTypeAdapter
from pydantic_ai.messages import ModelMessagesTypeAdapter

# A. 存整段对话（消息 + 累计用量 + conversation_id）
result = agent.run_sync("Tell me a joke.")
stored = ConversationTypeAdapter.dump_json(result.conversation)        # 存进 jsonb 列，用 result.conversation_id 做 key
conversation = ConversationTypeAdapter.validate_json(stored)
result = agent.run_sync("Explain?", conversation=conversation)
print(result.usage.requests)     # 2 —— 用量接着上一次往下数

# B. 只存消息
data: bytes = ModelMessagesTypeAdapter.dump_json(result.all_messages())
messages = ModelMessagesTypeAdapter.validate_json(data)
agent.run_sync("继续", message_history=messages)
```

`ModelMessagesTypeAdapter` 是 Pydantic 的 `TypeAdapter`，比 `json.dump` 多做了几件事：

- 自动处理 datetime / Enum / bytes
- 校验反序列化结果是合法 ModelMessage
- V1 存下来的历史在 V2 里仍然能反序列化

两个保真度上的细节：工具返回值里的 `datetime`、`UUID`、`Decimal` 这类没有 JSON 原生形式的值，存回来会变成字符串；UI 适配器（Vercel AI、AG-UI）的消息格式装不下 Pydantic AI 的全部字段，所以官方建议存 Pydantic AI 自己的格式，到边界上再转换。对话很长时，每轮重写整个对话太大，可以只追加每次运行的 `new_messages()`。

### 持久化指的是哪件事

官方用一张表把四个常被混在一起的需求拆开：

| 你想要的 | 用什么 | 在哪 |
|---------|--------|------|
| 存下一段对话，以后接着聊 | 存 `Conversation`，放你自己的数据库 | 核心 |
| 每次运行做了什么的日志，以及能续跑、能分叉的快照 | `StepPersistence` | harness（见[Capabilities（能力）与 ModelProfile](../03-advanced/06-capabilities.md)） |
| agent 跨对话记住某个人的偏好 | `Memory` | harness |
| 进程在工具调用中途挂掉，重启后重放已完成的步骤 | 持久执行（Temporal / DBOS / Prefect 等） | 核心 |

大多数人说的给 agent 加记忆，其实是第一行：agent 对当前对话的记忆就是消息历史，不需要另一套记忆系统。

---

## 6. 实战：SQLite 持久化聊天

```python
import sqlite3
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter

conn = sqlite3.connect("chat.db")
conn.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, messages BLOB)")
conn.commit()

agent = Agent("openai-chat:qwen-flash", instructions="你是一个友好的助手。")

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

print(chat("u1", "我叫 Ethan"))
print(chat("u1", "我刚刚说我叫什么？"))  # 应该记得名字
```

- 每条 conversation 一行，存全量 messages
- 也可以拆表：messages 一行一条，方便分页（牺牲一些写入便利性）

### 客户端送来的历史不可信

Pydantic AI 的服务端是无状态的：每次运行都由请求里带来的 `message_history`（以及延迟工具的结果）重建。框架不签名、也不校验工具调用、工具结果和审批，客户端完全可以伪造一次审批，或者塞进模型从没发出过的工具调用。官方明确说这是有意的设计，应对办法是：

- 在传输层做鉴权，把每个通过鉴权的调用方都当成「能提交任意历史」；
- 按调用方权限构建工具集；
- 高风险操作在服务端重新校验，**不要把历史里出现过审批当成授权凭据**；
- 来自浏览器的历史，传给 agent 之前先 `sanitize_messages(...)`（去掉客户端塞进来的系统提示等）：

```python
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import sanitize_messages

loaded = ModelMessagesTypeAdapter.validate_python(request_json["message_history"])
result = agent.run_sync("Tell me a different joke.", message_history=sanitize_messages(loaded))
```

LangChain 的状态存在服务端 checkpointer 里，客户端只传 `thread_id`，没有这个问题。两种设计各有代价，这是其中实打实的一项。

---

## 7. Token 计数

`r.usage` 拿 token 用量（现在是属性，不再是方法）：

```python
r = agent.run_sync("hi")
print(r.usage)
# RunUsage(input_tokens=20, output_tokens=8, requests=1)
```

`message_history` 越长，下一次的 `input_tokens` 越多，生产环境必须控制历史长度。最简单的是自己裁；V2 里推荐用 `ProcessHistory` capability 在发给模型前改写历史：

```python
from pydantic_ai import Agent, ModelMessage
from pydantic_ai.capabilities import ProcessHistory

def keep_recent(messages: list[ModelMessage]) -> list[ModelMessage]:
    return messages[-6:]

agent = Agent("openai:gpt-6.1-sol", capabilities=[ProcessHistory(keep_recent)])
```

处理器可以同步也可以异步，可以接 `RunContext`；处理结果会替换掉 state 里的历史。裁剪时要保证剩下的历史合法，工具调用和它的结果必须成对，否则 provider 会拒绝（下面的 `repair_messages` 就是修这个的）。更完整的上下文压缩（按 token 摘要、清理旧工具结果）有现成的 capability，见[Capabilities（能力）与 ModelProfile](../03-advanced/06-capabilities.md)。

```python
from pydantic_ai.messages import repair_messages
runnable = repair_messages(stored)     # 给没有结果的工具调用补一个「被中断」的返回，删掉孤立的返回
```

---

## 8. 直接构造消息（高级）

不通过 `run` 也能手工构造消息送进去：

```python
from pydantic_ai.messages import ModelRequest, UserPromptPart, ModelResponse, TextPart

history = [
    ModelRequest(parts=[UserPromptPart(content="先前我说要用 PostgreSQL")]),
    ModelResponse(parts=[TextPart(content="好的，我会用 PostgreSQL 语法。")]),
]

r = agent.run_sync("写一个查询所有用户的 SQL", message_history=history)
```

平时用不上，下面几种场景会用到：

- 从其他系统（LangChain / 自有 ORM）迁移历史进来
- 单元测试时构造特定状态
- 伪造一段 AI 之前说过的话，引导后续对话

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
            print(f"工具返回：{part.content}（outcome={part.outcome}）")
```

可以用来做审计日志、调试，或配合 Logfire trace 排查。`part.outcome` 能告诉你这次返回是正常的（`"success"`）、被要求重试的（`"retried"`），还是被拒绝 / 被中断的。

---

## 10. vs LangChain

| 任务 | LangChain 1.x | Pydantic AI |
|------|---------------|-------------|
| 消息类 | `HumanMessage` / `AIMessage` / `ToolMessage` | `ModelRequest` / `ModelResponse` 含多种 Part |
| 多轮历史 | checkpointer + `thread_id`（状态在服务端） | `conversation=` / `message_history=`（状态由你保存） |
| 持久化 | `InMemorySaver` / `SqliteSaver` / `PostgresSaver` | `ConversationTypeAdapter` 自己存；或 harness 的 `StepPersistence` |
| 跨对话记忆 | Store | harness 的 `Memory` |
| 裁剪历史 | middleware（摘要 / 裁剪） | `ProcessHistory` capability / 压缩 capability |
| Token 计数 | `usage_metadata` | `result.usage` |

思路上的差别：LangChain 把对话状态放进服务端 checkpointer；Pydantic AI 让你自己显式管理 messages 列表或 Conversation，概念简单，但存取要自己写，还要把客户端送来的历史当成不可信输入。

LangChain 等价：

```python
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

agent = create_agent("openai:gpt-6.1-sol", tools=[], checkpointer=InMemorySaver())
cfg = {"configurable": {"thread_id": "1"}}
agent.invoke({"messages": [{"role": "user", "content": "我叫 Ethan"}]}, cfg)
print(agent.invoke({"messages": [{"role": "user", "content": "我叫什么？"}]}, cfg)["messages"][-1].content)
```

Pydantic AI 等价：

```python
r1 = agent.run_sync("我叫 Ethan")
r2 = agent.run_sync("我叫什么？", conversation=r1.conversation)
print(r2.output)
```

---

## 11. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| 模型不记得上文 | 没传 `conversation` / `message_history` | 每次都传上一轮的 `conversation`（或 `all_messages()`） |
| 只传 `new_messages()` 后模型只记得上一轮 | `new_messages()` 只含本轮新增 | 续聊用 `all_messages()` 或 `conversation` |
| 测试里 `isinstance(part, RetryPromptPart)` 找不到了 | V2 废弃了它 | 认 `ToolReturnPart(outcome="retried")` / `RetryFeedbackPart` |
| `message_history` 越来越长，最后超窗口 | 没截断 | `ProcessHistory` / 压缩 capability；注意保持工具调用与结果成对 |
| 切了模型后历史报错 | 不同 provider 的工具调用 ID / 内置工具部件不兼容 | 启动新会话，或 `repair_messages` + 过滤 provider 专属部件 |
| 历史报「工具调用没有对应结果」 | 取消 / 崩溃 / 手工裁剪留下悬空调用 | `repair_messages(history)` |
| 工具里 `run_sync` 报 `event loop is already running` | 工具跑在事件循环里 | `await agent.run(...)` |
| 工具返回的 `datetime` 存回来变字符串 | JSON 没有这些类型 | 接受它，或自己定义序列化 |
| 把浏览器送来的历史直接喂给 agent | 客户端能伪造审批 / 系统提示 | `sanitize_messages` + 服务端重新校验高风险操作 |

---

## 12. 完整示例：CLI 多轮聊天

```python
from pydantic_ai import Agent

agent = Agent("openai-chat:qwen-flash", instructions="你是一位友好的助手。")

conversation = None
while True:
    user = input(">>> ")
    if user.lower() in {"exit", "quit"}:
        break
    r = agent.run_sync(user, conversation=conversation) if conversation else agent.run_sync(user)
    print(r.output)
    conversation = r.conversation     # 累积（含用量）
```

加持久化：

```python
import pathlib
from pydantic_ai import ConversationTypeAdapter

PATH = pathlib.Path("conversation.json")
conversation = ConversationTypeAdapter.validate_json(PATH.read_bytes()) if PATH.exists() else None

# ... 同上 ...

PATH.write_bytes(ConversationTypeAdapter.dump_json(conversation))
```

---

## 13. 本章 demo

完整可运行代码：[`demos/basics/07_messages_history.py`](../../demos/basics/07_messages_history.py)

基础篇到这里结束。下一部分讲工具系统，从 [Pydantic AI 02-01：Function Tools 函数工具](../02-tools/01-function-tools.md) 开始。
