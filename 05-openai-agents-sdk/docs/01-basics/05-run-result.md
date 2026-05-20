# RunResult & Usage：拿结果、中间步骤、token 计费

> **一句话**：`Runner.run()` 返回 `RunResult`，里面有最终输出、中间事件（tool call / handoff）、token 用量、最终 agent 等——生产里都用得上。

---

## 1. RunResult 全貌

```python
result = await Runner.run(agent, "查天气")

result.final_output       # str 或 output_type 实例
result.new_items          # 本轮新产生的 items（消息 / tool call / tool output / handoff）
result.input              # 原始 input
result.last_agent         # 最后跑的 Agent（handoff 后会变）
result.usage              # token 用量统计
result.raw_responses      # 原始 LLM responses
```

---

## 2. final_output

```python
agent = Agent(name="A", instructions="...")
result = await Runner.run(agent, "你好")
print(result.final_output)   # "你好！" - 字符串
```

设了 output_type 就是结构化：

```python
from pydantic import BaseModel

class Reply(BaseModel):
    text: str
    intent: str

agent = Agent(name="A", instructions="...", output_type=Reply)
result = await Runner.run(agent, "退款")

reply: Reply = result.final_output
print(reply.text, reply.intent)
```

---

## 3. new_items：看中间发生了啥

```python
result = await Runner.run(agent, "查北京天气并算北京到上海的距离")

for item in result.new_items:
    print(item.type, item)
```

`item.type` 可能是：

- `message_output_item`：Agent 说了一段话
- `tool_call_item`：调了 tool
- `tool_call_output_item`：tool 返回
- `handoff_call_item`：发起 handoff
- `handoff_output_item`：handoff 完成

```python
for item in result.new_items:
    if item.type == "tool_call_item":
        print(f"调了 {item.raw_item.name} 参数 {item.raw_item.arguments}")
    elif item.type == "tool_call_output_item":
        print(f"返回 {item.output}")
```

---

## 4. usage：token 计费

```python
result = await Runner.run(agent, "...")
usage = result.usage

print(f"Prompt: {usage.input_tokens}")
print(f"Output: {usage.output_tokens}")
print(f"Total: {usage.total_tokens}")
print(f"Requests: {usage.requests}")  # 总共多少次 LLM 调用
```

跨多轮（多次 tool call）的 usage 是累加的。

---

## 5. last_agent：handoff 后是谁

```python
triage = Agent(name="Triage", handoffs=[billing, support])
result = await Runner.run(triage, "我要退款")

print(result.last_agent.name)   # "Billing"（被 handoff 了）
```

用于：

- trace 上贴标签
- 决定下一步对话给谁继续

---

## 6. raw_responses：原始 LLM 响应

```python
for resp in result.raw_responses:
    print(resp.id, resp.model)
    # resp 是 openai.types.responses.Response 实例
```

debug / 拿原始 metadata 用。

---

## 7. 继续对话：从 result 取 input list

```python
result = await Runner.run(agent, "我叫小明")

# 拿到完整 input list（包含之前的 + 新生成的）
next_input = result.to_input_list()

# 后续可以加新消息继续
next_input.append({"role": "user", "content": "我叫啥"})
result2 = await Runner.run(agent, next_input)
```

⚠️ 更推荐用 [Sessions](./06-sessions.md) 自动管理。

---

## 8. Streaming 版的 RunResult

```python
result = Runner.run_streamed(agent, "...")

async for event in result.stream_events():
    pass  # 处理事件

# 跑完后拿最终结果
print(result.final_output)
print(result.usage)
```

`run_streamed` 返回 `RunResultStreaming`，stream 跑完属性才填完。

---

## 9. 实战示例：带 trace ID 落库

```python
import asyncio
from agents import Agent, Runner

agent = Agent(name="Bot", instructions="...")


async def chat(user_id: str, message: str):
    result = await Runner.run(agent, message)

    # 落库
    record = {
        "user_id": user_id,
        "input": message,
        "output": result.final_output,
        "agent": result.last_agent.name,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "total_cost_usd": result.usage.input_tokens * 0.00015 / 1000 \
                        + result.usage.output_tokens * 0.0006 / 1000,
        "trace_id": result.raw_responses[0].id if result.raw_responses else None,
    }
    # await db.insert("chat_log", record)
    return record
```

---

## 10. 错误时还能拿啥

抛 `MaxTurnsExceeded` 等异常时，**没有 RunResult 返回**。要拿到执行轨迹得：

- 看 trace dashboard（platform.openai.com/traces）
- 或者用 `Runner.run_streamed` 拿事件流（异常前的事件都收到）

---

## 11. 完整 demo

```python
# demos/basics/05_run_result.py
import asyncio
from agents import Agent, Runner, function_tool


@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 22°C"


agent = Agent(
    name="WeatherBot",
    instructions="用 get_weather 查询",
    tools=[get_weather],
)


async def main():
    result = await Runner.run(agent, "查北京和上海的天气")

    print("=== Final ===")
    print(result.final_output)

    print("\n=== Items ===")
    for item in result.new_items:
        print(f"  [{item.type}]")

    print("\n=== Usage ===")
    print(f"  Input: {result.usage.input_tokens}")
    print(f"  Output: {result.usage.output_tokens}")
    print(f"  Total: {result.usage.total_tokens}")
    print(f"  LLM calls: {result.usage.requests}")

    print("\n=== Last Agent ===")
    print(f"  {result.last_agent.name}")


asyncio.run(main())
```

---

## 12. 下一步

- 📖 加 Sessions 自动续接对话 → [06-sessions.md](./06-sessions.md)
- 📖 拿到 result 后做 evals → [07-production/05-evals.md](../07-production/05-evals.md)
- 📖 把 RunResult 转 trace → [05-advanced/02-custom-tracer.md](../05-advanced/02-custom-tracer.md)
