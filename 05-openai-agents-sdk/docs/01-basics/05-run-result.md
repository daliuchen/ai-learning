# RunResult 与 Usage：结果、中间步骤与 token 用量

> `Runner.run()` 返回的 `RunResult` 除了最终输出，还带着这次运行的中间步骤（tool call、handoff）、token 用量和最后接手的 agent。做日志、计费、排查问题时，这些字段都会用到。

---

## 1. RunResult 全貌

```python
result = await Runner.run(agent, "查天气")

result.final_output       # str 或 output_type 实例；停在审批中断时为 None
result.new_items          # 本轮新产生的 items（消息 / tool call / tool output / handoff / 审批…）
result.input              # 本次 run 实际用的 input（handoff filter 改写过的会反映在这里）
result.last_agent         # 最后跑的 Agent（handoff 后会变）
result.context_wrapper.usage   # token 用量统计（注意：0.0.x 时代的 result.usage 已不存在）
result.raw_responses      # 每次模型调用的原始响应（ModelResponse）
result.last_response_id   # 最后一次响应 ID，可作下一轮 previous_response_id
result.interruptions      # 等待人工审批的工具调用；配合 result.to_state() 恢复
result.input_guardrail_results / output_guardrail_results            # 守卫结果
result.tool_input_guardrail_results / tool_output_guardrail_results  # 工具守卫结果
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

## 3. new_items：中间发生了什么

```python
result = await Runner.run(agent, "查北京天气并算北京到上海的距离")

for item in result.new_items:
    print(item.type, item)
```

`item.type` 可能是：

- `message_output_item`：Agent 说了一段话
- `reasoning_item`：推理摘要
- `tool_call_item`：调了 tool
- `tool_call_output_item`：tool 返回
- `tool_approval_item`：工具调用停下来等审批
- `handoff_call_item`：发起 handoff
- `handoff_output_item`：handoff 完成
- 还有 `tool_search_call_item` / `tool_search_output_item`（hosted tool search）、`mcp_list_tools_item` / `mcp_approval_request_item` / `mcp_approval_response_item`（hosted MCP）

`new_items` 里的元素是 `MessageOutputItem`、`ToolCallItem`、`ToolCallOutputItem` 这些类的实例，用 `isinstance` 判断比比较字符串更稳。

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
usage = result.context_wrapper.usage

print(f"Prompt: {usage.input_tokens}")
print(f"Output: {usage.output_tokens}")
print(f"Total: {usage.total_tokens}")
print(f"Requests: {usage.requests}")  # 总共多少次 LLM 调用
for i, r in enumerate(usage.request_usage_entries):   # 每次调用的明细，算成本/看上下文占用用
    print(i, r.input_tokens, r.output_tokens)
```

一次 run 里有多轮（多次 tool call、handoff）时，usage 是累加的。几个细节：

- 流式 run 的 usage 要等 `stream_events()` 迭代完才完整；
- 第三方 Chat Completions 端点（千问等）：非流式有用量，流式要加 `ModelSettings(include_usage=True)` 才有；LiteLLM 适配器同理；
- 想保留供应商原始 usage 字段：`ModelSettings(preserve_raw_usage=True)`，然后读 `result.raw_responses[i].raw_usage`；
- `usage.input_tokens_details.cached_tokens` 是 prompt cache 命中数，`output_tokens_details.reasoning_tokens` 是推理 token。

---

## 5. last_agent：handoff 后是谁

```python
triage = Agent(name="Triage", handoffs=[billing, support])
result = await Runner.run(triage, "我要退款")

print(result.last_agent.name)   # "Billing"（被 handoff 了）
```

可以拿它给 trace 打标签，或者决定下一轮对话交给哪个 agent 继续。

---

## 6. raw_responses：原始 LLM 响应

```python
for resp in result.raw_responses:
    print(resp.response_id, resp.request_id, resp.usage.total_tokens)   # Chat Completions 模型的 response_id 为 None
    # resp 是 SDK 的 ModelResponse（不再是 openai 的 Response 对象）：output / usage / response_id / request_id / raw_usage
```

调试或者取原始 metadata 时用。

---

## 7. 继续对话：从 result 取 input list

```python
result = await Runner.run(agent, "我叫小明")

# 拿到完整 input list（包含之前的 + 新生成的）
next_input = result.to_input_list()
# 经过 handoff 过滤/嵌套历史后想要“规范”的下一轮输入：result.to_input_list(mode="normalized")

# 后续可以加新消息继续
next_input.append({"role": "user", "content": "我叫什么"})
result2 = await Runner.run(agent, next_input)
```

一般用 [Sessions](./06-sessions.md) 自动管理会更省事。

---

## 8. Streaming 版的 RunResult

```python
result = Runner.run_streamed(agent, "...")

async for event in result.stream_events():
    pass  # 处理事件

# 跑完后拿最终结果
print(result.final_output)
print(result.context_wrapper.usage)
```

`run_streamed` 返回 `RunResultStreaming`，要等事件流跑完，这些属性才完整。

---

## 9. 实战示例：调用记录落库

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
        "input_tokens": result.context_wrapper.usage.input_tokens,
        "output_tokens": result.context_wrapper.usage.output_tokens,
        # 单价按 gpt-6-luna（每百万 token 输入 $0.10 / 输出 $0.50），换模型时按官方定价改
        "total_cost_usd": result.context_wrapper.usage.input_tokens * 0.10 / 1_000_000 \
                        + result.context_wrapper.usage.output_tokens * 0.50 / 1_000_000,
        # ModelResponse 上的字段是 response_id；Chat Completions 模型为 None
        "response_id": result.raw_responses[-1].response_id if result.raw_responses else None,
    }
    # await db.insert("chat_log", record)
    return record
```

---

## 10. 出错时还能拿到什么

抛 `MaxTurnsExceeded` 这类异常时拿不到 RunResult，不过异常上带着 `exc.run_data`（`RunErrorDetails`）。里面有 `new_items`、`raw_responses`、`context_wrapper`（含 usage）、`last_agent` 和 guardrail 结果，出错前跑了哪些步骤、用了多少 token 都查得到。

要看完整轨迹，可以去 trace 后台（platform.openai.com/traces）或者自己的 tracing 处理器里找；也可以改用 `Runner.run_streamed`，异常发生前的事件都能收到。如果希望 run 不抛异常，就用 `error_handlers={"max_turns": ...}` 返回一个兜底的 final_output。

## 11. 被审批挂起时

```python
result = await Runner.run(agent, "删掉临时文件")
if result.interruptions:                 # 有工具调用在等人工批准
    state = result.to_state()            # 可序列化的 RunState：state.to_string()
    for it in result.interruptions:
        state.approve(it)                # 或 state.reject(it, rejection_message="...")
    result = await Runner.run(agent, state)
```

完整流程见 [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](../02-tools/04-tool-choice.md) 第 10 节。

---

## 12. 完整示例

```python
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
    print(f"  Input: {result.context_wrapper.usage.input_tokens}")
    print(f"  Output: {result.context_wrapper.usage.output_tokens}")
    print(f"  Total: {result.context_wrapper.usage.total_tokens}")
    print(f"  LLM calls: {result.context_wrapper.usage.requests}")

    print("\n=== Last Agent ===")
    print(f"  {result.last_agent.name}")


asyncio.run(main())
```

---

## 13. 下一步

- 自动续接对话：[Sessions：会话状态自动管理](./06-sessions.md)
- 用 result 做评测：[评测：把 Prompt Engineering 手册的评测集用到 Agent 上](../07-production/05-evals.md)
- 把运行过程接到自己的 trace 系统：[自定义 Tracer：接 LangSmith / Langfuse / Logfire](../05-advanced/02-custom-tracer.md)
