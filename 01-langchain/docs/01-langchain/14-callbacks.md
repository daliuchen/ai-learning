# LangChain 14：Callbacks 回调系统

> **一句话**：Callbacks 是 LangChain 在 chain 执行各个生命周期点上的钩子机制，用于日志、监控、流式、Token 统计、错误捕获。`astream_events` 是现代版的"Callbacks"，但部分场景还是 Callbacks 更合适。

---

## 1. Callbacks vs astream_events 选哪个

| 需求 | 推荐 |
|------|------|
| 实时流式 UI | `astream_events` |
| 写日志 / 接 OpenTelemetry / Datadog | **Callbacks** |
| 上传 LangSmith trace | 默认已自动 |
| 在每个 LLM 调用前后做计费记录 | **Callbacks** |
| 抓异常上报 | **Callbacks** |
| 多 Agent 不同 Agent 分别记录 | Callbacks（按 run_id） |

Callbacks 是"侧切面"，不影响主链；events 是流式数据。

---

## 2. CallbackHandler 接口

```python
from langchain_core.callbacks import BaseCallbackHandler

class MyHandler(BaseCallbackHandler):
    # LLM 相关
    def on_llm_start(self, serialized, prompts, **kwargs): ...
    def on_llm_new_token(self, token: str, **kwargs): ...
    def on_llm_end(self, response, **kwargs): ...
    def on_llm_error(self, error, **kwargs): ...

    # ChatModel
    def on_chat_model_start(self, serialized, messages, **kwargs): ...

    # Chain
    def on_chain_start(self, serialized, inputs, **kwargs): ...
    def on_chain_end(self, outputs, **kwargs): ...
    def on_chain_error(self, error, **kwargs): ...

    # Tool
    def on_tool_start(self, serialized, input_str, **kwargs): ...
    def on_tool_end(self, output, **kwargs): ...
    def on_tool_error(self, error, **kwargs): ...

    # Retriever
    def on_retriever_start(self, serialized, query, **kwargs): ...
    def on_retriever_end(self, documents, **kwargs): ...
    def on_retriever_error(self, error, **kwargs): ...

    # Agent
    def on_agent_action(self, action, **kwargs): ...
    def on_agent_finish(self, finish, **kwargs): ...
```

异步版本是 `AsyncCallbackHandler`，方法名加 `async`：

```python
class MyAsync(AsyncCallbackHandler):
    async def on_llm_new_token(self, token, **kwargs): ...
```

---

## 3. 传入 Callback 的三种姿势

### 3.1 构造模型时

```python
model = ChatOpenAI(model="gpt-4o-mini", callbacks=[MyHandler()])
```

### 3.2 调用时

```python
chain.invoke(x, config={"callbacks": [MyHandler()]})
```

### 3.3 全局

```python
import langchain
from langchain_core.tracers.context import register_configure_hook
# 或
from langchain_core.callbacks.manager import CallbackManager
```

但全局不推荐，灵活性差。

---

## 4. 实战 1：Token / 费用统计

```python
from langchain_core.callbacks import BaseCallbackHandler

class CostHandler(BaseCallbackHandler):
    PRICE = {  # gpt-4o-mini 美元/1M token
        "input": 0.15, "output": 0.60,
    }
    def __init__(self):
        self.in_tok = 0; self.out_tok = 0
    def on_llm_end(self, response, **kwargs):
        for gen in response.generations:
            usage = getattr(gen[0].message, "usage_metadata", None) if hasattr(gen[0], "message") else None
            if usage:
                self.in_tok += usage["input_tokens"]
                self.out_tok += usage["output_tokens"]
    @property
    def cost_usd(self):
        return self.in_tok / 1e6 * self.PRICE["input"] + self.out_tok / 1e6 * self.PRICE["output"]

ch = CostHandler()
chain.invoke(x, config={"callbacks": [ch]})
print(f"in={ch.in_tok} out={ch.out_tok} cost=${ch.cost_usd:.6f}")
```

LangChain 还内置 `get_openai_callback()` context manager：

```python
from langchain_community.callbacks.manager import get_openai_callback

with get_openai_callback() as cb:
    chain.invoke(x)
    print(cb.total_tokens, cb.total_cost)
```

仅支持 OpenAI 系列。

---

## 5. 实战 2：日志 / 调试

```python
import logging
log = logging.getLogger(__name__)

class LogHandler(BaseCallbackHandler):
    def on_chain_start(self, serialized, inputs, **kw):
        log.info(f"chain start: {serialized.get('name')} inputs={inputs}")
    def on_chain_end(self, outputs, **kw):
        log.info(f"chain end: {str(outputs)[:200]}")
    def on_chain_error(self, error, **kw):
        log.exception(f"chain error: {error}")
```

接入业务日志系统，比 print 强 100 倍。

---

## 6. 实战 3：异步流式写到 Queue（WebSocket）

```python
import asyncio
from langchain_core.callbacks import AsyncCallbackHandler

class WSHandler(AsyncCallbackHandler):
    def __init__(self, queue: asyncio.Queue):
        self.q = queue
    async def on_chat_model_start(self, serialized, messages, **kw):
        await self.q.put({"event": "start"})
    async def on_llm_new_token(self, token, **kw):
        await self.q.put({"event": "token", "data": token})
    async def on_chain_end(self, outputs, **kw):
        await self.q.put({"event": "end"})
```

注意 `on_llm_new_token` 只在 `streaming=True` 时触发。

---

## 7. 实战 4：错误上报

```python
import sentry_sdk

class SentryHandler(BaseCallbackHandler):
    def on_llm_error(self, error, **kw):
        sentry_sdk.capture_exception(error)
    def on_tool_error(self, error, **kw):
        sentry_sdk.capture_exception(error)
    def on_chain_error(self, error, **kw):
        sentry_sdk.capture_exception(error)
```

接入后 LangChain 任何错误都自动上报 Sentry。

---

## 8. 通过 metadata / tags 关联

```python
chain.invoke(x, config={
    "tags": ["prod", "v2"],
    "metadata": {"user_id": "u123", "trace_id": "t456"},
    "callbacks": [SomeHandler()],
})
```

在 callback 方法里 `kwargs["tags"]` / `kwargs["metadata"]` 可读，做按 tag 分流上报。

---

## 9. LangSmith 是基于 Callbacks 的

`LANGSMITH_TRACING=true` 时 LangChain 默认挂一个 `LangChainTracer` callback，把所有 chain/llm/tool 事件发到 LangSmith。

你自己加的 callback 和 LangSmith 互不影响，可以并存。

---

## 10. 一个综合 demo

```python
# demos/langchain/14_callbacks.py
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool

load_dotenv()

class Auditor(BaseCallbackHandler):
    def __init__(self):
        self.events = []
    def _log(self, name, **kw):
        self.events.append((name, kw))
    def on_chain_start(self, s, i, **kw): self._log("chain_start", inputs=i, name=s.get("name"))
    def on_chain_end(self, o, **kw): self._log("chain_end", outputs_preview=str(o)[:80])
    def on_chat_model_start(self, s, m, **kw): self._log("model_start", messages=len(m[0]))
    def on_llm_end(self, r, **kw):
        m = getattr(r.generations[0][0], "message", None)
        usage = getattr(m, "usage_metadata", None) if m else None
        self._log("model_end", usage=usage)
    def on_tool_start(self, s, i, **kw): self._log("tool_start", name=s.get("name"), input=i)
    def on_tool_end(self, o, **kw): self._log("tool_end", output=str(o)[:80])

@tool
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是会用工具的助手"),
    ("human", "{q}"),
])
model = ChatOpenAI(model="gpt-4o-mini").bind_tools([add])
chain = prompt | model | StrOutputParser()

auditor = Auditor()
chain.invoke({"q": "请帮我算 12 加 30"}, config={"callbacks": [auditor]})

for ev, data in auditor.events:
    print(ev, data)
```

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `on_llm_new_token` 不触发 | model 没开 streaming | `ChatOpenAI(streaming=True)` 或调用 `.stream()` |
| 异步链同步 callback 拿不到 | 同步/异步混用 | 用 `AsyncCallbackHandler` |
| 多 chain 嵌套 callback 调用很多次 | LangChain 把 callback 透传到子 Runnable | 用 `metadata`/`tags` 过滤 |
| Token 统计为 0 | 流式没开 `stream_usage=True` | `ChatOpenAI(stream_usage=True)` |
| Handler 抛异常导致 chain 失败 | callback 错误也会传播 | try/except 包内部逻辑 |

---

## 12. 本章 demo

[`demos/langchain/14_callbacks.py`](../../demos/langchain/14_callbacks.py)

至此 LangChain 主体 14 篇全部完成。接下来：

- [LangSmith 01](../02-langsmith/01-overview.md)
- [LangGraph 01](../03-langgraph/01-introduction.md)
