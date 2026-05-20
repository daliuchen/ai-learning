# Pydantic AI 进阶 01：流式响应（Streaming）

> **一句话**：Pydantic AI 的流式不是简单地"把 token 一个一个吐出来"，它能让你**边生成边消费"已部分校验的结构化对象"**——这是 LangChain 等同类框架做不到、或者做得很糙的地方。

---

## 1. 为什么需要流式

LLM 应用的体感分水岭就一条：**首字延迟（Time-To-First-Token, TTFT）**。一次性等模型把 500 字写完再 print，用户体感就是"卡住了"；边写边打，体感就是"对答如流"。

但流式不只是为了视觉效果，它在工程上还有两个硬刚需：

1. **早期失败检测**：模型写到一半发现格式不对，可以立即 cancel，省钱省时
2. **结构化部分校验**：模型刚写完 `{"name": "张三"`，你就能拿到一个"年龄字段还没生成"的对象，先把名字 render 出去，等年龄到了再补上

Pydantic AI 把这两件事都做到了同一个 API 里：`agent.run_stream()` + `StreamedRunResult`。

---

## 2. 流式的三种粒度

Pydantic AI 把流式拆成了三档，越往下越细：

| 粒度 | 方法 | 输出 | 适合场景 |
|------|------|------|----------|
| **文本流** | `stream_text(delta=True)` | 字符串增量 / 累积字符串 | 聊天 UI、Markdown 渲染 |
| **结构化流** | `stream_output()` | 部分校验的 Pydantic 对象 | 表单/卡片边生成边渲染 |
| **事件流** | `stream_response()` | 完整 `ModelResponse` 快照 | 调试、自定义协议 |

记住这条：**`stream_text` 看到的是 token，`stream_output` 看到的是对象**。

---

## 3. 最小可运行例子

```python
import asyncio
from pydantic_ai import Agent

agent = Agent("openai:gpt-4o-mini", system_prompt="你是一位简洁的助手。")

async def main():
    async with agent.run_stream("用 50 字介绍 Python") as result:
        async for chunk in result.stream_text(delta=True):
            print(chunk, end="", flush=True)
    print()

asyncio.run(main())
```

关键观察：

1. `run_stream()` 是 **async context manager**，必须用 `async with`——这是和 `run()` / `run_sync()` 最大的区别
2. `async with` 会在退出时清理底层 HTTP 连接，**少写就泄漏连接**
3. `stream_text(delta=True)` 拿到的是"新增的那段字"，去掉 `delta=True` 拿到的是"累积到现在的全部字"

`stream_text` 两种模式的差别：

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

这是 Pydantic AI 的招牌功能。你声明 `output_type=PydanticModel`，然后用 `stream_output()`：

```python
from pydantic import BaseModel
from pydantic_ai import Agent

class Profile(BaseModel):
    name: str
    age: int
    skills: list[str]

agent = Agent("openai:gpt-4o-mini", output_type=Profile)

async def main():
    async with agent.run_stream("生成一个 28 岁后端工程师小李的资料") as result:
        async for partial in result.stream_output():
            print(partial)

asyncio.run(main())
```

输出会是一系列**部分填充**的 `Profile` 对象，随着 token 流入逐步补全：

```
Profile(name='小', age=0, skills=[])
Profile(name='小李', age=0, skills=[])
Profile(name='小李', age=28, skills=[])
Profile(name='小李', age=28, skills=['Python'])
Profile(name='小李', age=28, skills=['Python', 'FastAPI'])
```

注意 `age=0`——Pydantic AI 对未到字段会填**默认值**（int 默认 0、str 默认 `''`）。如果你想区分"还没到"和"模型给了 0"，把字段声明为 `Optional[int] = None`：

```python
class Profile(BaseModel):
    name: str = ""
    age: int | None = None
    skills: list[str] = []
```

这样在流式中你可以严格判断：

```python
if partial.age is not None:
    render_age(partial.age)
```

---

## 5. StreamedRunResult 对象速查

`async with agent.run_stream(...) as result:` 里的 `result` 是一个 [`StreamedRunResult`](https://pydantic.dev/docs/ai/core-concepts/output/) 实例。常用 API：

| 属性 / 方法 | 类型 | 何时用 |
|------------|------|--------|
| `stream_text(delta=False)` | `AsyncIterator[str]` | 文本流（最常用） |
| `stream_output(debounce_by=None)` | `AsyncIterator[OutputT]` | 结构化对象流 |
| `stream_response(debounce_by=0.01)` | `AsyncIterator[ModelResponse]` | 拿到完整消息快照 |
| `output` | `OutputT` | 流式跑完后的最终结果 |
| `get_output()` | `OutputT` | 一次性拿最终值（不迭代时用） |
| `usage()` | `RunUsage` | token 消耗 |
| `all_messages()` | `list[ModelMessage]` | 完整消息历史（含本次） |
| `cancel()` | coroutine | 中途打断生成 |
| `validate_response_output(msg, allow_partial=True)` | `OutputT` | 手动校验部分响应 |

**重点**：`output` / `usage()` / `all_messages()` 这些只在流**完整消费过**之后才稳定。如果你 `break` 提早跳出迭代，请显式 `await result.cancel()`。

---

## 6. 流式工具调用

`run_stream()` 内部会自动处理工具调用循环，但它有一个**关键约束**：

> `run_stream()` 把"第一个匹配 output_type 的输出"视作最终输出，**之后不会再调工具**。

也就是说 `run_stream()` 适合"工具调用完了之后流式产出最终答案"的场景。如果你想流式观察**整个 Agent 图**（含工具调用过程），用 `iter()` 或 `run_stream_events()`：

```python
async with agent.iter("北京和上海的天气？") as agent_run:
    async for node in agent_run:
        # node 可能是模型请求节点、工具调用节点、最终输出节点
        print(type(node).__name__)
```

`iter()` 给你的是"节点级"事件，可以在工具调用之间插桩。`stream_events()` 给你的是"消息部分增量"事件，对应每个 PartDeltaEvent / PartStartEvent。

---

## 7. 与 FastAPI SSE 集成

聊天 UI 的标配是 Server-Sent Events。三十行代码集成：

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic_ai import Agent

app = FastAPI()
agent = Agent("openai:gpt-4o-mini")

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

如果是结构化流：

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

前端拿到的就是 JSON 序列，每个都是当前快照。

---

## 8. vs LangChain 流式对比

| 维度 | Pydantic AI | LangChain |
|------|-------------|-----------|
| 文本流 | `stream_text(delta=True)` | `chain.stream(input)` |
| 结构化流 | `stream_output()` 返回 Pydantic 对象 | `JsonOutputParser` 流式返回 dict |
| 部分校验 | ✅ Pydantic Model 部分校验 | ⚠️ 仅 JSON partial 解析 |
| 异步管理 | 必须 `async with` | 隐式（`stream` 是普通生成器） |
| 工具调用流 | `iter()` / `run_stream_events()` | `astream_events()` |
| 取消 | `await result.cancel()` | 抛 `asyncio.CancelledError` |

简单说：**LangChain 流式更随意（不需要 async with），Pydantic AI 更严格但语义清晰**。Pydantic AI 把"流出来的是 Pydantic 对象"这件事做到了第一公民。

---

## 9. 实战：聊天 UI 边打边显示 + 结构化抽取

下面这个例子同时演示文本流和结构化流。**这就是 demo 的雏形**：

```python
from pydantic import BaseModel
from pydantic_ai import Agent
import asyncio

class Reply(BaseModel):
    summary: str
    follow_up_questions: list[str]

agent = Agent("openai:gpt-4o-mini", output_type=Reply)

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
        for q in result.output.follow_up_questions:
            print("•", q)
        print("\n— usage —")
        print(result.usage())

asyncio.run(main())
```

注意两个点：

- 用 `partial.summary[len(last_summary):]` 自己做 delta，因为 `stream_output()` 给的是累积值
- 退出 `async with` 后 `result.output` / `result.usage()` 才是最终值

---

## 10. 生产建议

| 议题 | 建议 |
|------|------|
| 取消 | 用户关闭浏览器时调 `result.cancel()`，否则 token 还在烧 |
| debounce | `stream_output(debounce_by=0.05)` 合并 50ms 内的更新，减少前端压力 |
| backpressure | SSE 用 `asyncio.Queue`，下游慢时丢弃中间帧 |
| 错误 | 流中抛错时 `async with` 会自动清理；但你要在 `event_stream` 外加 try/except 发 `data: {"error": ...}` |
| 日志 | Logfire 自带 stream 时延 / 取消 / 失败链路 |
| token 限制 | `model_settings={"max_tokens": ...}` 防止流太久不停 |

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `RuntimeError: cannot enter context twice` | `async with run_stream()` 写在了同步函数里 | 必须 `async def`，外层 `asyncio.run(main())` |
| 没看到流，一次性吐出 | 写成了 `agent.run(...)` 而不是 `run_stream(...)` | 改方法名 |
| 流没消费完就 break，连接泄漏 | 异步生成器需显式关闭 | 加 `await result.cancel()` 或别 break |
| `stream_output` 中拿到的对象字段全是默认值 | 模型还没写到那个字段 | 用 `Optional[...] = None` 区分 |
| `result.output` 是 `None` | 流没跑完就读 | 等迭代完再读，或者用 `await result.get_output()` |
| 部分校验失败抛错 | 用了不允许的字段约束（如 `min_length=10`） | 用 `validate_response_output(msg, allow_partial=True)` 容忍 |
| `stream_output` 收到 dict 而不是 Pydantic 对象 | 没声明 `output_type=Model` | 在 `Agent(..., output_type=Profile)` 加上 |
| `usage()` 永远为 0 | 流没消费完 / TestModel 没真实 token | 真实模型 + 完整消费 |

---

## 12. 本章 demo

完整可运行代码：[`demos/advanced/01_streaming.py`](../../demos/advanced/01_streaming.py)

下一篇：[02-multimodal.md](02-multimodal.md) —— 图片、音频、PDF 输入全攻略。
