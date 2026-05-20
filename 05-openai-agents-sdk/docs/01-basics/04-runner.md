# Runner 三种姿势：run / run_sync / run_streamed

> **一句话**：`Runner.run()` 异步、`Runner.run_sync()` 同步阻塞、`Runner.run_streamed()` 流式拿事件——按使用场景挑。

---

## 1. 三种对比

| 方法 | 返回 | 场景 |
|------|------|------|
| `await Runner.run(agent, input)` | `RunResult` | 异步业务代码（FastAPI / 并发） |
| `Runner.run_sync(agent, input)` | `RunResult` | CLI / 脚本 / 不想管 asyncio |
| `Runner.run_streamed(agent, input)` | `RunResultStreaming`（拿事件流） | 前端打字机效果 / 进度展示 |

---

## 2. run：标准 async 调用

```python
import asyncio
from agents import Agent, Runner

agent = Agent(name="A", instructions="...")


async def main():
    result = await Runner.run(agent, "你好")
    print(result.final_output)


asyncio.run(main())
```

`run` 的完整签名：

```python
Runner.run(
    agent,
    input,                  # str 或 messages list
    context=None,           # 自定义 context（详见 05-modules）
    session=None,           # Sessions
    max_turns=10,           # 最多 LLM 调用轮数
)
```

`max_turns` 是防死循环的硬限制。Tool call → tool result → LLM 回应 = 1 turn。

---

## 3. run_sync：图省事

```python
from agents import Agent, Runner

agent = Agent(name="A", instructions="...")
result = Runner.run_sync(agent, "你好")
print(result.final_output)
```

底层 = `asyncio.run(Runner.run(...))`，**不能在已有 event loop 里调用**（比如 FastAPI handler、Jupyter cell）。

---

## 4. run_streamed：拿事件流

```python
# demos/basics/04_streaming.py
import asyncio
from agents import Agent, Runner

agent = Agent(name="A", instructions="讲个 300 字故事")


async def main():
    result = Runner.run_streamed(agent, "我家的猫")
    async for event in result.stream_events():
        if event.type == "raw_response_event":
            # 原始 OpenAI delta
            from openai.types.responses import ResponseTextDeltaEvent
            if isinstance(event.data, ResponseTextDeltaEvent):
                print(event.data.delta, end="", flush=True)
        elif event.type == "run_item_stream_event":
            # 工具调用、handoff 等
            print(f"\n[Event: {event.name}]")
        elif event.type == "agent_updated_stream_event":
            # 换 agent（handoff）
            print(f"\n[Switched to: {event.new_agent.name}]")
    print()
    print("Done. Final:", result.final_output)


asyncio.run(main())
```

**事件类型**：

- `raw_response_event`：底层 LLM token 流
- `run_item_stream_event`：高层事件（message / tool call / tool output / handoff）
- `agent_updated_stream_event`：发生 handoff 切换 agent

---

## 5. 在 FastAPI 流给前端

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from agents import Agent, Runner

agent = Agent(name="Chat", instructions="...")
app = FastAPI()


@app.post("/chat")
async def chat(req: dict):
    result = Runner.run_streamed(agent, req["message"])

    async def gen():
        from openai.types.responses import ResponseTextDeltaEvent
        async for event in result.stream_events():
            if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                yield f"data: {event.data.delta}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```

---

## 6. input：字符串 vs messages list

### 字符串（最常用）

```python
await Runner.run(agent, "你好")
```

### Messages list（继续多轮）

```python
messages = [
    {"role": "user", "content": "我叫小明"},
    {"role": "assistant", "content": "你好小明"},
    {"role": "user", "content": "我叫啥"},
]
await Runner.run(agent, messages)
```

但更推荐用 [Sessions](./06-sessions.md) 自动管理对话历史。

---

## 7. max_turns：防死循环

```python
await Runner.run(agent, "查 100 个城市天气", max_turns=20)
```

到 max_turns 还没产出 final_output → 抛 `MaxTurnsExceeded`。

调高 max_turns 适合：

- 复杂 ReAct（多次 search + reason）
- 长链 handoffs

调低（5）适合：

- 简单分类 / 抽取（防 prompt 让模型乱跳 tool）

---

## 8. 错误捕获

```python
from agents.exceptions import (
    MaxTurnsExceeded,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    AgentsException,
)

try:
    result = await Runner.run(agent, "...", max_turns=5)
except MaxTurnsExceeded:
    print("跑太久了")
except InputGuardrailTripwireTriggered as e:
    print(f"输入被守卫拦下: {e}")
except OutputGuardrailTripwireTriggered as e:
    print(f"输出被守卫拦下: {e}")
except AgentsException as e:
    print(f"其它错误: {e}")
```

---

## 9. 同步 + 流式（特殊场景）

run_streamed 是异步的。要在同步代码里流：

```python
import asyncio
from agents import Agent, Runner

agent = Agent(name="A", instructions="...")


def stream_sync(query: str):
    async def _impl():
        result = Runner.run_streamed(agent, query)
        async for event in result.stream_events():
            yield event
    loop = asyncio.new_event_loop()
    gen = _impl()
    while True:
        try:
            yield loop.run_until_complete(gen.__anext__())
        except StopAsyncIteration:
            break


for ev in stream_sync("你好"):
    print(ev.type)
```

不优雅。能上 async 就 async。

---

## 10. 性能：并发跑多个 Agent

```python
import asyncio
from agents import Agent, Runner

agent = Agent(name="A", instructions="...")


async def main():
    tasks = [
        Runner.run(agent, f"第 {i} 个问题")
        for i in range(10)
    ]
    results = await asyncio.gather(*tasks)
    for r in results:
        print(r.final_output[:30])


asyncio.run(main())
```

10 个并发跑，受限于 OpenAI rate limit。

---

## 11. 下一步

- 📖 RunResult 都能拿到啥 → [05-run-result.md](./05-run-result.md)
- 📖 加 Sessions → [06-sessions.md](./06-sessions.md)
- 📖 长任务的 max_turns 实战 → [02-tools/04-tool-choice.md](../02-tools/04-tool-choice.md)
