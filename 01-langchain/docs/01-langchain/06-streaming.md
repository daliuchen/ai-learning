# LangChain 06：Streaming 流式输出

> LangChain 有四个流式 API：`stream` 和 `astream` 输出链末端的 chunk（一个同步一个异步），`astream_events` 输出每一步的事件，`astream_log` 输出差异 patch。上线的应用里用得最多的是 `astream_events`。这篇逐个讲，最后补上 agent 的 `stream_mode`。

---

## 1. 为什么 Streaming 重要

用户觉得 LLM 应用慢，多半是在等模型把整段话写完。直接 `invoke` 要等好几秒，流式能让第一个字在几百毫秒内出来，体验完全不一样。

LangChain 的流式分两层。一层是 token 级，由 ChatModel 内部逐个 yield token chunk；另一层是链路级，整个 chain 把中间步骤的事件透出来。token 级流式的前提是 ChatModel 本身支持流式，而且链里没有哪个 Lambda 或 Parser 把输出攒住，否则外面也流不起来。

---

## 2. 四个流式 API

| API | 输入 | 输出 | 何时用 |
|-----|------|------|--------|
| `stream(input)` | 单条 | `Iterator[Output]` | 简单同步场景 |
| `astream(input)` | 单条 | `AsyncIterator[Output]` | 异步、Web 服务 |
| `astream_events(input, version="v2")` | 单条 | `AsyncIterator[StreamEvent]` | 最常用，监听 chain 每一步 |
| `astream_log(input)` | 单条 | `AsyncIterator[RunLogPatch]` | 差异 patch 流（少用） |

后两个都是异步的，Web 场景基本都用 `astream_events`。

---

## 3. stream / astream

### 3.1 ChatModel 直接流

```python
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="gpt-6-luna")

for chunk in model.stream("讲个长一点的故事"):
    print(chunk.content, end="", flush=True)
```

每个 `chunk` 是一个 `AIMessageChunk`，可以用 `+` 累加：

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

LangChain 把每个 `AIMessageChunk.content` 依次交给 `StrOutputParser`，parser 再逐段 yield 字符串。

### 3.3 异步

```python
import asyncio
async def main():
    async for chunk in chain.astream({"q": "..."}):
        print(chunk, end="", flush=True)
asyncio.run(main())
```

在 FastAPI、Starlette 这类 Web 服务里用 `astream`，它不会阻塞事件循环。

---

## 4. astream_events：事件流

`stream` 只输出最后一步的结果，中间步骤看不到。`astream_events` 会把 chain 里每个节点的开始、中间、结束事件都发出来，做正式产品的界面基本都靠它。

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

每个 event 是一个 dict：

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

按 `event` 和 `name` 分发处理：

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

前端用 `EventSource` 接收。

### 4.4 过滤：include_names / include_tags

事件很多，可以按名字过滤：

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

也可以在 chain 里给节点打标签，再按标签过滤：

```python
chain = (prompt | model).with_config({"tags": ["main-llm"], "run_name": "main"})
```

---

## 5. astream_log

它输出的是差异 patch，格式遵循 JSONPatch（RFC 6902）：

```python
async for chunk in chain.astream_log(x, include_types=["llm"]):
    print(chunk)
```

输出：

```
RunLogPatch({'op': 'add', 'path': '/logs/ChatOpenAI/streamed_output/-', 'value': 'h'})
RunLogPatch({'op': 'add', 'path': '/logs/ChatOpenAI/streamed_output/-', 'value': 'i'})
```

`astream_log` 是早期的 API，现在还在，但已经不推荐。`astream_events` 是它的升级版，新项目直接用 `astream_events`。

---

## 6. 让 RunnableLambda 也流起来

默认情况下 `RunnableLambda` 会等拿到完整输入再处理：

```python
chain = model | RunnableLambda(lambda s: s + "!!!")
# 即使 model 流式输出，lambda 也会等全部完成
```

改成「接收迭代器、yield 输出」的生成器函数，直接放进 `|` 链，LangChain 会把它包成 `RunnableGenerator`，按 chunk 流式处理：

```python
def make_stream(stream):
    for chunk in stream:
        yield chunk + "!"

chain = model | StrOutputParser() | make_stream   # 自动包成 RunnableGenerator
```

注意不要再套一层 `RunnableLambda(make_stream)`：`RunnableLambda` 会先把上游输出攒完整再调用函数，流式就失效了。也可以显式用 `RunnableGenerator`：

```python
from langchain_core.runnables import RunnableGenerator

def transform(stream):
    for chunk in stream:
        yield chunk.upper()

chain = model | StrOutputParser() | RunnableGenerator(transform)
```

---

## 7. JSON 部分流式

`JsonOutputParser` 可以边生成边解析不完整的 JSON：

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

前端要实时填充表单时，这个能力很好用。

---

## 8. 统计 token 用量

单个 chunk 的 usage 一般是空的，只有最后一个 chunk 带完整用量（Chat Completions 接口需要请求里带 `stream_options={"include_usage": True}`）：

```python
model = ChatOpenAI(
    model="gpt-6-luna",
    stream_usage=True,
)

acc = None
for chunk in model.stream("..."):
    acc = chunk if acc is None else acc + chunk
print(acc.usage_metadata)
```

`stream_usage=True` 会让 LangChain 在请求里带上 include_usage。用 OpenAI 官方地址时它默认就是开的；设了 `base_url`（比如接千问）或传了自定义 client 时默认关闭，要显式打开，前提是对方接口支持。

---

## 9. Agent 的流式：stream_mode（1.x 新增）

前面讲的都是单个 chain 或模型的流式。`create_agent` 返回的是 LangGraph 图，它有自己的一套 `stream_mode`。要在界面上分别展示 agent 正在思考、正在调工具、给出答案，用它更合适：

```python
from langchain.agents import create_agent

agent = create_agent(model, tools=[get_weather])
inp = {"messages": [{"role": "user", "content": "北京天气？"}]}

# 1) messages：逐 token 拿模型输出（含 tool_call_chunks），meta 里有 langgraph_node
for token, meta in agent.stream(inp, stream_mode="messages"):
    print(meta["langgraph_node"], token.text, end="")

# 2) updates：每个节点跑完给一次增量（model / tools 交替出现），适合画进度
for ev in agent.stream(inp, stream_mode="updates"):
    print(list(ev))            # ['model'] -> ['tools'] -> ['model']

# 3) 组合：传列表，拿到 (mode, data) 元组
for mode, data in agent.stream(inp, stream_mode=["updates", "messages"]):
    ...
```

此外还有 `custom`（在工具里用 `get_stream_writer()` 主动推送自定义事件，比如「已查到 3 条订单」）、`values`（每一步的完整状态）、`debug` 等模式。`astream_events(version="v2")` 在 agent 上同样可用，事件类型就是 `on_chat_model_stream` / `on_tool_start` / `on_tool_end` 那一套。`stream_mode` 的完整用法在 LangGraph 07 篇。

---

## 10. 流式中的中断

异步流可以通过 `asyncio.CancelledError` 中断：

```python
task = asyncio.create_task(consume_stream())
await asyncio.sleep(2)
task.cancel()   # 中断
```

LangChain 会尝试取消正在进行的 LLM 调用，能不能真的停下来取决于供应商 SDK 是否支持。

---

## 11. 综合 demo：astream_events 监听 RAG

完整代码：[`demos/langchain/06_streaming.py`](../../demos/langchain/06_streaming.py)

```python
# demos/langchain/06_streaming.py
import asyncio
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda

load_dotenv()
model = ChatOpenAI(model="gpt-6-luna")

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

运行后会先看到 retriever 完成的提示，接着是 LLM 逐 token 的输出。

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `stream` 没流起来，整段一起出 | chain 末端的 parser 不支持流式 | 换成 `StrOutputParser`，或自己实现流式 |
| RunnableLambda 不流式 | `RunnableLambda` 总是等输入攒完整再调用函数 | 改成接收迭代器的生成器函数，用 `RunnableGenerator` 包装 |
| `astream_events` 报 `NotImplementedError` | 传了 `version="v3"`，它目前只有 ChatModel 和 LangGraph 图实现了 | 用默认的 `version="v2"` |
| 接兼容接口时流式拿不到 usage | 设了 `base_url` 后 `stream_usage` 默认关闭 | `ChatOpenAI(stream_usage=True)` |
| agent 流式只看到整段回答 | 用了 `stream_mode="values"` / `"updates"` | 要逐 token 就用 `stream_mode="messages"` |
| WebSocket 断了，LLM 还在消耗 token | 没有取消任务 | 用 `task.cancel()` |

---

下一篇：[LangChain 07：Tools 工具系统](07-tools.md)
