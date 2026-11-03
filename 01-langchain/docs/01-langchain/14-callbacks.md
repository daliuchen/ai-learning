# LangChain 14：Callbacks 回调系统

> Callbacks 是 LangChain 在执行过程中各个生命周期节点上留的钩子，日志、监控、Token 统计、错误上报都靠它。`astream_events` 可以看作它的新一代替代品，但写日志、接监控这类不关心流式数据的需求，用 Callbacks 仍然更顺手。

---

## 1. Callbacks vs astream_events 选哪个

| 需求 | 推荐 |
|------|------|
| 实时流式 UI | `astream_events` |
| 写日志 / 接 OpenTelemetry / Datadog | Callbacks |
| 上传 LangSmith trace | 默认自动完成 |
| 在每个 LLM 调用前后做计费记录 | Callbacks |
| 抓异常上报 | Callbacks |
| 多 Agent 不同 Agent 分别记录 | Callbacks（按 run_id） |

两者的区别在于：Callbacks 挂在主链旁边，只观察不改变主链的输出；events 本身就是主链吐出来的流式数据。

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

    # Agent（仅旧 AgentExecutor 会触发；create_agent 是 LangGraph 图，走 chain/tool/llm 回调）
    def on_agent_action(self, action, **kwargs): ...
    def on_agent_finish(self, finish, **kwargs): ...
```

> 1.x 里有个容易踩的坑：回调里的 `serialized` 参数现在**经常是 `None`**（LangChain 不再序列化组件），所以别写 `serialized.get("name")`。这会抛 `AttributeError`，而回调里的异常默认只是写进日志，很难发现。组件名从 `kwargs["name"]` 取，标签/元数据在 `kwargs["tags"]` / `kwargs["metadata"]`。

异步版本是 `AsyncCallbackHandler`，方法改成 `async def`：

```python
class MyAsync(AsyncCallbackHandler):
    async def on_llm_new_token(self, token, **kwargs): ...
```

---

## 3. 传入 Callback 的三种方式

### 3.1 构造模型时

```python
model = ChatOpenAI(model="gpt-6-luna", callbacks=[MyHandler()])
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

全局注册不太推荐，所有调用都会被挂上，想按场景区分就麻烦了。

---

## 4. 实战 1：Token / 费用统计

```python
from langchain_core.callbacks import BaseCallbackHandler

class CostHandler(BaseCallbackHandler):
    PRICE = {  # 示意单价（美元/1M token），按实际所用模型的价格表改
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

LangChain 还提供了 `get_openai_callback()` 这个 context manager，它在 community 包里，而且只认 OpenAI 官方模型的价格表：

```python
from langchain_community.callbacks.manager import get_openai_callback

with get_openai_callback() as cb:
    chain.invoke(x)
    print(cb.total_tokens, cb.total_cost)
```

换了别的供应商它就不管用了。1.x 有个更通用的 `UsageMetadataCallbackHandler`（`from langchain_core.callbacks import UsageMetadataCallbackHandler`），按模型名汇总 `usage_metadata`，单价自己乘就行。

---

## 5. 实战 2：日志 / 调试

```python
import logging
log = logging.getLogger(__name__)

class LogHandler(BaseCallbackHandler):
    def on_chain_start(self, serialized, inputs, **kw):
        log.info(f"chain start: {kw.get('name')} inputs={inputs}")
    def on_chain_end(self, outputs, **kw):
        log.info(f"chain end: {str(outputs)[:200]}")
    def on_chain_error(self, error, **kw):
        log.exception(f"chain error: {error}")
```

接进业务日志系统后，能按级别过滤、能检索，排查问题比到处 print 方便得多。

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

`on_llm_new_token` 只在流式调用时触发：模型设了 `streaming=True`，或者调用方用的是 `.stream()` / `.astream()` / `astream_events`。

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

挂上之后，模型、工具、链上的错误都会自动报到 Sentry。

---

## 8. 通过 metadata / tags 关联

```python
chain.invoke(x, config={
    "tags": ["prod", "v2"],
    "metadata": {"user_id": "u123", "trace_id": "t456"},
    "callbacks": [SomeHandler()],
})
```

callback 方法里能从 `kwargs["tags"]` / `kwargs["metadata"]` 读到这些值，可以按 tag 分流上报。

---

## 9. LangSmith 是基于 Callbacks 的

`LANGSMITH_TRACING=true` 时，LangChain 会默认挂一个 `LangChainTracer` callback，把所有 chain、llm、tool 事件发到 LangSmith。自己加的 callback 和它互不影响，可以同时存在。

---

## 10. 一个综合 demo

完整代码：[`demos/langchain/14_callbacks.py`](../../demos/langchain/14_callbacks.py)

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
    def _log(self, event, **kw):        # 形参别叫 name：下面会传 name=... 关键字
        self.events.append((event, kw))
    def on_chain_start(self, s, i, **kw): self._log("chain_start", inputs=i, name=kw.get("name"))
    def on_chain_end(self, o, **kw): self._log("chain_end", outputs_preview=str(o)[:80])
    def on_chat_model_start(self, s, m, **kw): self._log("model_start", messages=len(m[0]))
    def on_llm_end(self, r, **kw):
        m = getattr(r.generations[0][0], "message", None)
        usage = getattr(m, "usage_metadata", None) if m else None
        self._log("model_end", usage=usage)
    def on_tool_start(self, s, i, **kw): self._log("tool_start", name=kw.get("name"), input=i)
    def on_tool_end(self, o, **kw): self._log("tool_end", output=str(o)[:80])

@tool
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是会用工具的助手"),
    ("human", "{q}"),
])
model = ChatOpenAI(model="gpt-6-luna").bind_tools([add])
chain = prompt | model | StrOutputParser()      # 注意：模型决定调工具时 content 为空，所以最终输出可能是空串

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
| 流式调用时 Token 统计为 0 | 接兼容接口（设了 `base_url`）时 `stream_usage` 默认关闭 | `ChatOpenAI(stream_usage=True)` |
| Handler 里抛了异常却没人发现 | 回调异常默认只写日志，handler 设了 `raise_error = True` 才会让整条链失败 | 内部逻辑自己 try/except，出错时主动上报 |
| 回调里 `serialized.get(...)` 报 AttributeError（日志里一行 `Error in ... callback`） | 1.x 里 `serialized` 常为 None | 用 `kwargs["name"]` |
| `_push(name, **kw)` 之类自定义方法报 `multiple values for argument 'name'` | 回调 kwargs 里本来就带 `name` | 自定义辅助方法的形参别叫 `name` |

---

LangChain 部分到这里就结束了。接下来可以看：

- [LangSmith 01：总览与第一条 Trace](../02-langsmith/01-overview.md)
- [LangGraph 01：介绍、定位与其他框架的对比](../03-langgraph/01-introduction.md)
