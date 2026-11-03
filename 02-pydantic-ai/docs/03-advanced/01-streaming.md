# Pydantic AI 进阶 01：流式响应（Streaming）

> Pydantic AI 的流式不只是把 token 逐个吐出来。输出类型是 Pydantic 模型时，你可以边生成边拿到已经部分校验过的对象；想看工具调用的全过程，还有事件流和逐节点迭代两种方式。这篇把这几条路径和取消、SSE 集成一起讲清楚。

以 `pydantic-ai 2.64.0` 为准。V2 的 `StreamedRunResult` 没有 `.output` 属性，要用 `await result.get_output()`。

---

## 1. 为什么需要流式

用户对 LLM 应用快不快的感受，主要取决于首字延迟（Time-To-First-Token, TTFT）。等模型把 500 字全写完再一次性显示，用户会觉得卡住了；边生成边显示，体验就顺畅得多。

流式在工程上还有两个实际用处：

1. 早发现问题：模型写到一半格式就不对了，可以立即取消，省时间也省钱
2. 结构化部分校验：模型刚写完 `{"name": "张三"`，你就能拿到一个年龄字段还没生成的对象，先把名字显示出来，年龄到了再补上

这两件事在 Pydantic AI 里都通过 `agent.run_stream()` 和 `StreamedRunResult` 完成。

---

## 2. 流式的四种粒度

Pydantic AI 的流式分成几档，越往下越细：

| 粒度 | 方法 | 输出 | 适合场景 |
|------|------|------|----------|
| 文本流 | `stream_text(delta=True)` | 字符串增量 / 累积字符串 | 聊天 UI、Markdown 渲染 |
| 结构化流 | `stream_output()` | 部分校验的 Pydantic 对象 | 表单/卡片边生成边渲染 |
| 响应流 | `stream_response()` | 完整 `ModelResponse` 快照 | 自己控制校验、自定义协议 |
| 事件流 | `run_stream_events()` / `iter()` | 所有事件（含工具调用） | 聊天 UI 实时显示工具进度 |

`stream_text` 给你的是文本，`stream_output` 给你的是对象。

---

## 3. 最小可运行例子

```python
import asyncio
from pydantic_ai import Agent

agent = Agent("openai-chat:qwen-flash", instructions="你是一位简洁的助手。")

async def main():
    async with agent.run_stream("用 50 字介绍 Python") as result:
        async for chunk in result.stream_text(delta=True):
            print(chunk, end="", flush=True)
    print()

asyncio.run(main())
```

几点说明：

1. `run_stream()` 是 async context manager，必须用 `async with`，这是它和 `run()` / `run_sync()` 最大的区别
2. 退出 `async with` 时会清理底层 HTTP 连接，不这么写会泄漏连接
3. `stream_text(delta=True)` 每次给的是新增的那一段；去掉 `delta=True`，给的是到目前为止累积的全部文本

两种模式的差别：

```python
# delta=True，更适合 UI 增量渲染
# 第 1 次: "Pyth"
# 第 2 次: "on 是"
# 第 3 次: "一门"

# delta=False（默认），适合调试 / 模型纠错
# 第 1 次: "Pyth"
# 第 2 次: "Python 是"
# 第 3 次: "Python 是一门"
```

---

## 4. 结构化流式：边生成边校验

这是 Pydantic AI 很有特色的功能。声明 `output_type=PydanticModel`，然后用 `stream_output()`：

```python
from pydantic import BaseModel
from pydantic_ai import Agent

class Profile(BaseModel):
    name: str
    age: int
    skills: list[str]

agent = Agent("openai-chat:qwen-flash", output_type=Profile)

async def main():
    async with agent.run_stream("生成一个 28 岁后端工程师小李的资料") as result:
        async for partial in result.stream_output():
            print(partial)

asyncio.run(main())
```

输出是一串部分填充的 `Profile` 对象，随着 token 流入逐步补全：

```
Profile(name='小', age=0, skills=[])
Profile(name='小李', age=0, skills=[])
Profile(name='小李', age=28, skills=[])
Profile(name='小李', age=28, skills=['Python'])
Profile(name='小李', age=28, skills=['Python', 'FastAPI'])
```

注意 `age=0`：部分校验时，还没生成的字段会以占位值出现（也可能出现 `name=''`、`skills=[]`）。如果要区分「还没生成」和「模型给的就是 0」，把字段声明成 `Optional[int] = None`：

```python
class Profile(BaseModel):
    name: str = ""
    age: int | None = None
    skills: list[str] = []
```

这样流式过程中就能准确判断：

```python
if partial.age is not None:
    render_age(partial.age)
```

---

## 5. StreamedRunResult 对象速查

`async with agent.run_stream(...) as result:` 里的 `result` 是一个 [`StreamedRunResult`](https://pydantic.dev/docs/ai/core-concepts/output/) 实例。常用 API：

| 属性 / 方法 | 类型 | 何时用 |
|------------|------|--------|
| `stream_text(delta=False, debounce_by=0.1)` | `AsyncIterator[str]` | 文本流（最常用） |
| `stream_output(debounce_by=0.1)` | `AsyncIterator[OutputT]` | 结构化对象流 |
| `stream_response(debounce_by=0.1)` | `AsyncIterator[ModelResponse]` | 拿到完整消息快照 |
| `await get_output()` | `OutputT` | 拿最终值（没有 `.output` 属性，旧代码里的 `result.output` 会 `AttributeError`） |
| `usage` | `RunUsage`（属性） | token 消耗 |
| `all_messages()` / `new_messages()` | `list[ModelMessage]` | 消息历史 |
| `await cancel()` / `cancelled` | coroutine / bool | 中途打断生成 |
| `await validate_response_output(msg, allow_partial=True)` | `OutputT` | 手动校验部分响应 |

`get_output()`、`usage`、`all_messages()` 这些要等流完整消费完才稳定。如果用 `break` 提前跳出迭代，记得显式 `await result.cancel()`（见 6.1）。

---

## 6. 流式工具调用与事件流

`run_stream()` 内部会自动处理工具调用循环，但有一个限制需要先知道：

> `run_stream()` 把第一个匹配 output_type 的输出当作最终输出。同一次回复里排在它之后的工具调用不会执行（这一点不受 `end_strategy` 影响），结果也不会再交回模型。

所以 `run_stream()` 适合「工具都调完了，再流式输出最终答案」的场景。如果输出类型是 `str`，而模型在同一次回复里既写了文字又发了工具调用，这些工具调用在流式模式下**不会运行**。想流式观察整个 Agent 过程（包括工具调用），用 `run_stream_events()` 或 `iter()`：

```python
from pydantic_ai import AgentRunResultEvent

async with agent.run_stream_events("北京和上海的天气？") as events:
    async for event in events:
        if isinstance(event, AgentRunResultEvent):
            print("最终输出:", event.result.output)
        else:
            print(type(event).__name__)     # PartStartEvent / PartDeltaEvent / FunctionToolCallEvent / FunctionToolResultEvent / FinalResultEvent ...
```

`run_stream_events()` 其实是对 `run(event_stream_handler=...)` 的封装，总会把整个 agent 图跑完。它给的是原始事件，文本和结构化输出要自己用 `PartStartEvent` + `PartDeltaEvent` 拼起来。两边都想要，就用 `iter()`：

```python
async with agent.iter("北京和上海的天气？") as agent_run:
    async for node in agent_run:
        # node 可能是 UserPromptNode / ModelRequestNode / CallToolsNode / End
        print(type(node).__name__)
```

`iter()` 按节点给你事件，可以在节点之间插入自己的逻辑，也可以在 `ModelRequestNode` 里用 `async with node.stream(run.ctx) as stream` 拿到这次模型响应的流式事件。`run()` 和 `run_stream()` 还接受 `event_stream_handler=` 参数，在运行过程中接收所有事件。`iter()` 和工具还能发自定义事件（`CustomEvent`），把长任务的进度推给前端。

### 6.1 取消流

官方给的写法（在 `run_stream` 里取消）：

```python
async with agent.run_stream("Write a long essay about Python") as result:
    text = ""
    async for chunk in result.stream_text(delta=True):
        text += chunk
        if len(text) > 100:
            await result.cancel()      # 通知 provider 停止生成并关闭连接（模型集成支持时）
            break
    print(result.cancelled)                         # True
    print(result.response.state == "interrupted")   # True：最后的响应被标记为中断
```

需要注意：

- 只 `break` 不 `cancel()`，离开 `async with` 时流也会被清理，但远端不一定立刻停止生成和计费；要立刻停就调 `cancel()`
- 被取消的响应可能带着不完整的工具调用参数，Pydantic AI 会把它标成 `state="interrupted"`，之后复用历史时自动修复
- Google、xAI、Hugging Face 的 SDK 只提供异步迭代器，框架能安全地停止本地拉取，但不保证远端马上停
- 在千问这类 OpenAI 兼容端点上，`cancel()` 之后后台可能打印一条 `Task exception was never retrieved ... ModelConnectionError` 警告。它不影响程序退出码，只是输出很吵。想要干净的取消，用 `run()` + `CancellationToken`：它可以从运行外部、甚至别的线程取消，并返回可以续跑的历史（见[第一个 Agent](../01-basics/03-first-agent.md) 3.5 节）

取消整个运行：`token = CancellationToken(); agent.run(..., cancellation_token=token); token.cancel()`。被取消时抛 `RunCancelled`，异常里带着取消前已经完成的消息。

---

## 7. 与 FastAPI SSE 集成

聊天 UI 通常用 Server-Sent Events 推流，接起来大概三十行：

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent

app = FastAPI()
agent = Agent("openai-chat:qwen-flash")

@app.get("/chat")
async def chat(q: str):
    async def event_stream():
        async with agent.run_stream(q) as result:
            async for chunk in result.stream_text(delta=True):
                # SSE 协议：data: ...\n\n
                yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

前端用 `EventSource("/chat?q=xxx")` 就能收到增量。

结构化流的写法：

```python
import json

@app.get("/extract")
async def extract(text: str):
    async def event_stream():
        async with agent.run_stream(text) as result:
            async for partial in result.stream_output():
                yield f"data: {partial.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

前端收到的是一串 JSON，每条都是当前的快照。

---

## 8. 与 LangChain 流式对比

| 维度 | Pydantic AI | LangChain |
|------|-------------|-----------|
| 文本流 | `stream_text(delta=True)` | `chain.stream(input)` |
| 结构化流 | `stream_output()` 返回 Pydantic 对象 | 流式 JSON 解析（dict） |
| 部分校验 | 支持，对 Pydantic Model 做部分校验 | 只有 JSON 的 partial 解析 |
| 异步管理 | 必须 `async with` | 隐式（`stream` 是普通生成器） |
| 工具调用流 | `iter()` / `run_stream_events()` | `stream(stream_mode=...)` / `astream_events()` |
| 取消 | `CancellationToken` / `await result.cancel()` | 取消任务（`asyncio.CancelledError`） |

LangChain 的流式写起来更随意，不需要 `async with`；Pydantic AI 要求更严格，但语义更清楚，而且流出来的直接就是 Pydantic 对象。

---

## 9. 实战：聊天 UI 边打边显示 + 结构化抽取

下面的例子同时用到文本流和结构化流，demo 就是在它的基础上写的：

```python
from pydantic import BaseModel
from pydantic_ai import Agent
import asyncio

class Reply(BaseModel):
    summary: str
    follow_up_questions: list[str]

agent = Agent("openai-chat:qwen-flash", output_type=Reply)

async def main():
    async with agent.run_stream("总结一下今天 AI 圈的三件大事") as result:
        last_summary = ""
        async for partial in result.stream_output():
            # 只 print 新增的 summary 部分
            new_part = partial.summary[len(last_summary):]
            if new_part:
                print(new_part, end="", flush=True)
                last_summary = partial.summary
        print("\n\n— 后续问题 —")
        final = await result.get_output()
        for q in final.follow_up_questions:
            print("•", q)
        print("\n— usage —")
        print(result.usage)

asyncio.run(main())
```

两处细节：

- `stream_output()` 给的是累积值，所以用 `partial.summary[len(last_summary):]` 自己算增量
- 流消费完后用 `await result.get_output()` 拿最终值（流式结果没有 `.output`），`result.usage` 这时才是最终用量

---

## 10. 生产建议

| 议题 | 建议 |
|------|------|
| 取消 | 用户关掉页面时调 `result.cancel()` 或用 `CancellationToken`，否则 token 还在继续消耗 |
| debounce | 三个流方法默认 `debounce_by=0.1`，把 100ms 内的更新合并成一次；前端要更跟手就调小（如 0.05），要逐 token 就传 `None` |
| backpressure | SSE 用 `asyncio.Queue`，下游慢时丢弃中间帧 |
| 错误 | 流中抛错时 `async with` 会自动清理，但要在 `event_stream` 外加 try/except，发一条 `data: {"error": ...}` |
| 日志 | Logfire 自带流式时延、取消、失败的链路 |
| token 限制 | 用 `model_settings={"max_tokens": ...}` 防止流一直不结束 |

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `SyntaxError: 'async with' outside async function` | `async with run_stream()` 写在了同步函数里 | 必须 `async def`，外层 `asyncio.run(main())` |
| 没有流式效果，一次性全部输出 | 写成了 `agent.run(...)` 而不是 `run_stream(...)` | 改方法名 |
| 流没消费完就 break，仍在计费 | 只停了本地消费 | 加 `await result.cancel()` |
| `stream_output` 拿到的对象字段全是默认值 | 模型还没生成到那个字段 | 用 `Optional[...] = None` 区分 |
| `AttributeError: 'StreamedRunResult' object has no attribute 'output'` | 流式结果没有 `.output` | `await result.get_output()` |
| 部分校验失败抛错 | 用了部分数据满足不了的字段约束（如 `min_length=10`） | 用 `await result.validate_response_output(msg, allow_partial=True)` 放宽 |
| `stream_output` 收到 dict 而不是 Pydantic 对象 | 没声明 `output_type=Model` | 在 `Agent(..., output_type=Profile)` 加上 |
| `usage` 一直是 0 或很小 | 流没消费完，或 TestModel 没有真实 token | 用真实模型并完整消费 |
| 流式时工具没被调用 | `run_stream` 把首个匹配的输出当最终结果，之后的工具调用不执行 | 用 `run_stream_events()` / `iter()`，或在 instructions 里让模型先调工具再作答 |

---

## 12. 本章 demo

完整可运行代码：[`demos/advanced/01_streaming.py`](../../demos/advanced/01_streaming.py)

下一篇：[Pydantic AI 进阶 02：多模态输入（Image / Audio / Video / Document）](02-multimodal.md)，讲图片、音频、PDF 输入。
