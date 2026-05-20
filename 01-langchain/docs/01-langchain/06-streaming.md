# LangChain 06：Streaming 全方位流式

> **一句话**：LangChain 提供四种流式 API（`stream` / `astream` / `astream_events` / `astream_log`），分别覆盖"末端 chunk 流"、"异步 chunk 流"、"事件流"、"差异 patch 流"四类需求。生产环境最常用的是 `astream_events`。

---

## 1. 为什么 Streaming 重要

LLM 应用的体感延迟主要在"等模型一口气写完"。直接 `invoke` 用户要等好几秒，流式可以让首字符在 200ms 内吐出，体验差异是质变的。

LangChain 的流式有两个层面：

1. **Token 级流式**：ChatModel 内部 yield token chunk
2. **链路级流式**：整个 chain 把中间事件透出来

只有 ChatModel 内部支持流式才能做到 token 级；如果链里有 Lambda/Parser 阻塞了输出，外面也流不起来。

---

## 2. 四个流式 API 全景

| API | 输入 | 输出 | 何时用 |
|-----|------|------|--------|
| `stream(input)` | 单条 | `Iterator[Output]` | 简单同步场景 |
| `astream(input)` | 单条 | `AsyncIterator[Output]` | 异步、Web 服务 |
| `astream_events(input, version="v2")` | 单条 | `AsyncIterator[StreamEvent]` | **最常用**：监听 chain 每一步 |
| `astream_log(input)` | 单条 | `AsyncIterator[RunLogPatch]` | 差异 patch 流（少用） |

后两者都是异步，所以 Web 场景几乎都用 `astream_events`。

---

## 3. stream / astream

### 3.1 ChatModel 直接流

```python
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="gpt-4o-mini")

for chunk in model.stream("讲个长一点的故事"):
    print(chunk.content, end="", flush=True)
```

每个 `chunk` 是 `AIMessageChunk`，能用 `+` 累加：

```python
acc = None
for chunk in model.stream("..."):
    acc = chunk if acc is None else acc + chunk
print(acc.content)
print(acc.usage_metadata)   # 完整 token 统计
```

### 3.2 整个 chain 流

```python
chain = prompt | model | StrOutputParser()
for chunk in chain.stream({"q": "..."}):
    print(chunk, end="", flush=True)   # chunk 是 str
```

LangChain 内部把每个 `AIMessageChunk.content` 传给 `StrOutputParser`，parser 流式 yield 字符串。

### 3.3 异步

```python
import asyncio
async def main():
    async for chunk in chain.astream({"q": "..."}):
        print(chunk, end="", flush=True)
asyncio.run(main())
```

`astream` 比 `stream` 更适合 Web 服务（FastAPI/Starlette），因为不阻塞事件循环。

---

## 4. astream_events：事件流（重点）

`stream` 只能流末端结果，看不到中间步骤；`astream_events` 暴露 chain 里每个节点的开始/中间/结束事件，是构建生产级 UI 的核心 API。

### 4.1 基本用法

```python
async for event in chain.astream_events({"q": "..."}, version="v2"):
    print(event["event"], event["name"])
```

输出（精简）：

```
on_chain_start RunnableSequence
on_prompt_start ChatPromptTemplate
on_prompt_end ChatPromptTemplate
on_chat_model_start ChatOpenAI
on_chat_model_stream ChatOpenAI    ← 多次
on_chat_model_stream ChatOpenAI
on_chat_model_end ChatOpenAI
on_parser_start StrOutputParser
on_parser_stream StrOutputParser   ← 多次
on_parser_end StrOutputParser
on_chain_end RunnableSequence
```

### 4.2 事件 schema

每个 event 是 dict：

```python
{
    "event": "on_chat_model_stream",
    "name": "ChatOpenAI",
    "run_id": "uuid",
    "tags": [...],
    "metadata": {...},
    "data": {
        "chunk": AIMessageChunk(content="hello"),
    },
}
```

按 `event` 类型 + `name` 类型分发处理：

```python
async for ev in chain.astream_events(x, version="v2"):
    kind = ev["event"]
    name = ev["name"]
    if kind == "on_retriever_end":
        # 把检索到的文档发给前端展示"已查到 5 篇资料"
        docs = ev["data"]["output"]
        await ws.send({"type": "docs", "n": len(docs)})
    elif kind == "on_chat_model_stream":
        token = ev["data"]["chunk"].content
        await ws.send({"type": "token", "text": token})
    elif kind == "on_tool_start":
        await ws.send({"type": "tool", "name": ev["name"]})
```

### 4.3 配合 FastAPI 做 SSE

```python
from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse

app = FastAPI()

@app.get("/chat")
async def chat(q: str):
    async def gen():
        async for ev in chain.astream_events({"q": q}, version="v2"):
            if ev["event"] == "on_chat_model_stream":
                yield {"event": "token", "data": ev["data"]["chunk"].content}
            elif ev["event"] == "on_retriever_end":
                yield {"event": "docs", "data": str(len(ev["data"]["output"]))}
    return EventSourceResponse(gen())
```

前端用 `EventSource` 监听即可。

### 4.4 过滤：include_names / include_tags

事件量很大，可以过滤：

```python
async for ev in chain.astream_events(
    x,
    version="v2",
    include_names=["my_step", "ChatOpenAI"],
    include_tags=["llm-call"],
    include_types=["chat_model", "retriever"],
):
    ...
```

或在 chain 里给节点打标签：

```python
chain = (prompt | model).with_config({"tags": ["main-llm"], "run_name": "main"})
```

---

## 5. astream_log

差异 patch 流，每个 patch 是 JSONPatch RFC 6902 风格：

```python
async for chunk in chain.astream_log(x, include_types=["llm"]):
    print(chunk)
```

输出：

```
RunLogPatch({'op': 'add', 'path': '/logs/ChatOpenAI/streamed_output/-', 'value': 'h'})
RunLogPatch({'op': 'add', 'path': '/logs/ChatOpenAI/streamed_output/-', 'value': 'i'})
```

`astream_log` 是早期 API，`astream_events` 是其升级版，**新项目首选 astream_events**。

---

## 6. 让 RunnableLambda 也流起来

默认 `RunnableLambda` 是阻塞的：

```python
chain = model | RunnableLambda(lambda s: s + "!!!")
# 即使 model 流式输出，lambda 也会等全部完成
```

让它流式：

```python
def make_stream(stream):
    for chunk in stream:
        yield chunk + "!"

# 函数接收 Iterator 并 yield，会自动被识别为流式
chain = model | RunnableLambda(make_stream)
```

或者用 `RunnableGenerator`：

```python
from langchain_core.runnables import RunnableGenerator

def transform(stream):
    for chunk in stream:
        yield chunk.upper()

chain = model | StrOutputParser() | RunnableGenerator(transform)
```

---

## 7. JSON 部分流式

`JsonOutputParser` 支持"边生成边解析不完整 JSON"：

```python
from langchain_core.output_parsers import JsonOutputParser

chain = prompt | model | JsonOutputParser()
for partial in chain.stream({"q": "返回 10 个水果数组"}):
    print(partial)
```

输出：

```
{}
{'fruits': []}
{'fruits': ['ap']}
{'fruits': ['apple']}
{'fruits': ['apple', 'ba']}
...
```

这是 LangChain 工程上很亮的点，前端写实时表单的好帮手。

---

## 8. 累计 token 用法

每个 chunk 单独的 usage 一般是空的，最后一个 chunk 才有完整 usage（OpenAI 返回 `stream_options={"include_usage": True}` 时）：

```python
model = ChatOpenAI(
    model="gpt-4o-mini",
    stream_usage=True,
)

acc = None
for chunk in model.stream("..."):
    acc = chunk if acc is None else acc + chunk
print(acc.usage_metadata)
```

`stream_usage=True` 让 LangChain 自动开 OpenAI 的 include_usage 选项。

---

## 9. 流式中的中断

异步流可以用 `asyncio.CancelledError` 中断：

```python
task = asyncio.create_task(consume_stream())
await asyncio.sleep(2)
task.cancel()   # 中断
```

LangChain 内部会取消正在进行的 LLM 调用（取决于供应商 SDK 是否支持中断）。

---

## 10. 综合 demo：astream_events 监听 RAG

```python
# demos/langchain/06_streaming.py
import asyncio
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

load_dotenv()
model = ChatOpenAI(model="gpt-4o-mini")

# 假装 retriever
async def fake_retrieve(q: str):
    await asyncio.sleep(0.5)
    return [f"文档 {i}" for i in range(3)]

retriever = RunnableLambda(fake_retrieve).with_config(run_name="my_retriever")

def format_docs(docs):
    return "\n".join(docs)

prompt = ChatPromptTemplate.from_messages([
    ("system", "基于：\n{ctx}"),
    ("human", "{q}"),
])

chain = (
    {
        "ctx": retriever | format_docs,
        "q": lambda x: x["q"],
    }
    | prompt
    | model
    | StrOutputParser()
)

async def main():
    async for ev in chain.astream_events({"q": "Python 优势"}, version="v2"):
        kind = ev["event"]
        if kind == "on_retriever_end" or ev["name"] == "my_retriever" and kind.endswith("_end"):
            print(f"\n[retriever 完成] {ev['data'].get('output')}")
        elif kind == "on_chat_model_stream":
            print(ev["data"]["chunk"].content, end="", flush=True)
    print()

asyncio.run(main())
```

跑起来你会先看到"retriever 完成"提示，然后实时看到 LLM token 输出。

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `stream` 不流，整段一起出 | chain 末端有阻塞 parser | 改 `StrOutputParser` 或自定义流式 |
| RunnableLambda 不流 | 函数返回值而非 yield | 把函数改成 generator |
| `astream_events` 报 `version` 错 | LangChain 旧版本 | 升级 `langchain-core>=0.2.30` |
| OpenAI 流式拿不到 usage | 没开 `stream_usage` | `ChatOpenAI(stream_usage=True)` |
| WebSocket 断了，LLM 还在烧 token | 没取消 | 用 `task.cancel()` |

---

## 12. 本章 demo

[`demos/langchain/06_streaming.py`](../../demos/langchain/06_streaming.py)

下一篇：[07-tools.md](07-tools.md) — Tools 工具系统。
