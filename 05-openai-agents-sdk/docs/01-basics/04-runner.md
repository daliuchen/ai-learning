# Runner 的三种运行方式：run / run_sync / run_streamed

> 跑 Agent 有三个入口：`Runner.run()` 是异步的，`Runner.run_sync()` 同步阻塞，`Runner.run_streamed()` 边跑边吐事件。这篇讲三者各适合什么场景，以及 max_turns、多轮续接、RunConfig 这些跑起来以后马上会碰到的问题。

---

## 1. 三种方式对比

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
    input,                  # str / messages list / RunState（恢复暂停的 run）
    context=None,           # 自定义 context，工具、hooks、guardrails 都能拿到
    session=None,           # Sessions
    max_turns=10,           # 最多 LLM 调用轮数；传 None 关闭上限（0.16 起）
    hooks=None,             # RunHooks（见 05-advanced/03）
    run_config=None,        # RunConfig：全局模型/守卫/tracing/历史处理等（见第 12 节）
    error_handlers=None,    # {"max_turns": ..., "model_refusal": ..., "invalid_final_output": ...}
    previous_response_id=None, conversation_id=None,   # OpenAI 服务端托管的会话续接
)
```

`max_turns` 是防止死循环的硬上限。每调用一次模型算 1 个 turn：模型发出 tool call 是一轮，拿到 tool result 后再调一次模型又是一轮。

---

## 3. run_sync：同步调用

```python
from agents import Agent, Runner

agent = Agent(name="A", instructions="...")
result = Runner.run_sync(agent, "你好")
print(result.final_output)
```

它底层相当于 `asyncio.run(Runner.run(...))`，所以不能在已有 event loop 的地方调用，比如 FastAPI handler 或 Jupyter cell。

---

## 4. run_streamed：拿事件流

```python
import asyncio
from agents import Agent, Runner

agent = Agent(name="A", instructions="讲个 300 字故事")


async def main():
    result = Runner.run_streamed(agent, "我家的猫")
    async for event in result.stream_events():
        if event.type == "raw_response_event":
            # 原始 delta（Chat Completions 模型也会被转成 Responses 事件）
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

事件有三类：

- `raw_response_event`：底层 LLM token 流
- `run_item_stream_event`：高层事件（message / tool call / tool output / handoff）
- `agent_updated_stream_event`：发生 handoff 切换 agent

官方文档里关于流式有几条要求：

- `stream_events()` 必须迭代到结束。`final_output`、`interruptions` 和 session 落库都要等迭代器跑完才完整；中途想停就调 `result.cancel()`（`cancel(mode="after_turn")` 会等当前这轮结束），调完也要继续把事件消费完。
- 流式运行出错时，异常从 `stream_events()` 里抛出，`run_streamed()` 本身不抛。
- 用第三方 Chat Completions 端点时，流式默认没有 token 用量，要 `ModelSettings(include_usage=True)`，加上之后 `context_wrapper.usage` 才有值。
- 想用 OpenAI Responses 的 WebSocket 传输（0.10 起，opt-in）：`responses_websocket_session()` 复用连接，适合多轮低延迟。

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
    {"role": "user", "content": "我叫什么"},
]
await Runner.run(agent, messages)
```

手动拼历史容易出错，一般用 [Sessions](./06-sessions.md) 自动管理更省事。

---

## 7. max_turns：防死循环

```python
await Runner.run(agent, "查 100 个城市天气", max_turns=20)
```

跑到 max_turns 还没有 final_output，会抛 `MaxTurnsExceeded`。如果不想抛异常，而是给用户一个兜底回答，可以用 `error_handlers`：

```python
result = await Runner.run(
    agent, "查 100 个城市天气", max_turns=3,
    error_handlers={"max_turns": lambda data: "步骤太多了，请缩小范围"},   # 返回字符串即 final_output
)
```

函数也可以返回 `RunErrorHandlerResult(final_output=..., include_in_history=False)`，`include_in_history=False` 表示这条兜底回答不写进 result 历史和 session。同样的机制还有 `"model_refusal"`（模型拒答，0.15 起默认抛 `ModelRefusalError`）和 `"invalid_final_output"`（结构化输出校验失败）。

多次搜索、推理交替的 ReAct 流程，或者 handoff 链比较长的场景，需要把 max_turns 调高。简单的分类、抽取任务可以调低到 5 左右，免得模型被 prompt 带偏、在工具之间来回跳。

---

## 8. 错误捕获

```python
from agents.exceptions import (
    MaxTurnsExceeded,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    ModelBehaviorError,      # 模型输出非法 JSON / 调了不存在的工具 / Responses 返回 failed
    ModelRefusalError,       # 模型拒答（0.15 起）
    ModelTimeoutError,       # 单次模型调用超过 ModelSettings.timeout
    ToolTimeoutError,        # @tool(timeout=..., timeout_behavior="raise_exception")
    UserError,               # 你的用法有误
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

## 9. 在同步代码里用流式

run_streamed 是异步的，在同步代码里只能这样包一层：

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

写法比较别扭，条件允许还是直接用 async。

---

## 10. 多轮状态：四种续接方式

| 想要 | 用 |
|------|----|
| 完全手动、不绑定 provider | `result.to_input_list()` 自己拼 |
| SDK 自动读写历史 | `session=...`（见 [Sessions：会话状态自动管理](./06-sessions.md)） |
| OpenAI 服务端保存 | `conversation_id=...`（或 `OpenAIConversationsSession`） |
| 服务端链式 | `previous_response_id=result.last_response_id` |

同一次 run 里，session 不能和 `conversation_id` / `previous_response_id` 一起用；服务端托管的会话也不支持 handoff `input_filter`。第三方 Chat Completions 端点不认 `previous_response_id` / `conversation_id`（SDK 会静默丢掉，想让它报错可用 `OpenAIProvider(use_responses=False, strict_feature_validation=True)`）。

---

## 11. 性能：并发跑多个 Agent

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

10 个 run 并发，实际吞吐受 OpenAI 的 rate limit 限制。如果要限制单次 run 内同时执行的本地工具数，用 `RunConfig(tool_execution=ToolExecutionConfig(max_function_tool_concurrency=2))`。

---

## 12. RunConfig：单次 run 的全局配置

不改 Agent 定义，只对某一次 run 覆盖行为：

```python
from agents import RunConfig, Runner

result = await Runner.run(
    agent, "你好",
    run_config=RunConfig(
        model="gpt-6.1-sol",                # 全局模型（设了就覆盖所有 agent 的 model）
        model_settings=ModelSettings(temperature=0.2),
        workflow_name="客服主流程",          # trace 名
        trace_metadata={"env": "prod"},
        trace_include_sensitive_data=False,  # trace 里不记 LLM/工具的输入输出
        tracing_disabled=False,
        input_guardrails=[...], output_guardrails=[...],   # 对所有 agent 生效
        handoff_input_filter=..., nest_handoff_history=False,
        session_settings=SessionSettings(limit=50),
        call_model_input_filter=...,         # 调模型前最后一次改写输入（裁剪历史、脱敏）
        tool_not_found_behavior="return_error_to_model",   # 模型调了不存在的工具时，把错误回给模型而不是抛异常
    ),
)
```

0.x 版本里这块加的字段最多，完整列表见官方 [Running agents](https://openai.github.io/openai-agents-python/running_agents/#run-config)。

---

## 13. 下一步

- [RunResult 与 Usage：结果、中间步骤与 token 用量](./05-run-result.md)
- [Sessions：会话状态自动管理](./06-sessions.md)
- 长任务里怎么用 max_turns：[Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](../02-tools/04-tool-choice.md)
