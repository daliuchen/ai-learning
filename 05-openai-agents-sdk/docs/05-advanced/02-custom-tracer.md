# 自定义 Tracer：接 LangSmith / Langfuse / Logfire

> 实现一个 `TracingProcessor`，就能把 trace 数据导到任何后端：LangSmith、Langfuse、Logfire、OpenTelemetry，或者自己的 ClickHouse。这篇先讲处理器拿到的 trace 和 span 实际长什么样，再逐个讲怎么接。

---

## 1. 为什么要换 tracer

OpenAI Platform Traces 基本够用，但有几个局限。同时用 OpenAI 和 Claude 时，trace 会断成两截；想做评测工作流，LangSmith 和 Langfuse 有现成的；有些公司的合规要求必须自托管；还有人希望和已有的 OpenTelemetry、Datadog 打通。

---

## 2. TracingProcessor 接口

```python
from agents import add_trace_processor, set_trace_processors
from agents.tracing import TracingProcessor


class MyProcessor(TracingProcessor):
    def on_trace_start(self, trace) -> None: ...     # trace.trace_id / trace.name / trace.export()
    def on_trace_end(self, trace) -> None: ...
    def on_span_start(self, span) -> None: ...       # span.span_id / span.trace_id / span.parent_id / span.started_at
    def on_span_end(self, span) -> None: ...         # span.ended_at / span.error / span.span_data.export()
    def shutdown(self) -> None: ...
    def force_flush(self) -> None: ...


add_trace_processor(MyProcessor())        # 追加：默认的 OpenAI 导出器仍在
set_trace_processors([MyProcessor()])     # 替换：只剩你给的（不再上传到 OpenAI）
```

> 0.24 的 trace 和 span 上没有 `span.name` / `span.input` / `span.output` / `trace.id` / `trace.metadata` 这些属性。实际结构是：
> - `trace.trace_id`、`trace.name`、`trace.export()`（dict，含 `group_id`、`metadata`）；
> - `span.span_id`、`span.trace_id`、`span.parent_id`、`span.started_at` / `span.ended_at`（ISO 字符串）、`span.error`；
> - span 的具体内容在 `span.span_data` 里：`span.span_data.export()` 返回 dict，其中 `type` 是 `agent` / `generation` / `function` / `handoff` / `guardrail` / `mcp_tools` / `custom` 之类，`generation` 带 `input` / `output` / `model` / `usage`，`function` 带 `input` / `output`，`custom` 带 `name` / `data`；
> - Task 和 Turn 是 `custom` span，`data["sdk_span_type"]` 为 `"task"` / `"turn"`，里面有 `usage`。
>
> 这些回调都是同步的，在 agent 的执行线程里调用，所以别阻塞，也别抛异常。SDK 会吞掉处理器的异常、继续调用其他处理器，但会打警告。

以一个带 MCP 工具的 Agent 为例，跑一轮工具调用，处理器收到的 span 依次是：`mcp_tools` → `generation` → `function(query_table)` → `custom(turn)` → `mcp_tools` → `generation` → `custom(turn)` → `agent(Bot)` → `custom(task)`。

---

## 3. 接 Logfire（Pydantic AI 用的那个）

Logfire 自带 OpenAI Agents SDK 集成，`logfire 5.4` 里有 `logfire.instrument_openai_agents()`：

```python
import logfire

logfire.configure(token="...")          # 不传 token / send_to_logfire=False 就只在本地
logfire.instrument_openai_agents()
```

这样就行了，之后所有 trace 都会自动进 Logfire 的 dashboard（发送到云端需要 Logfire 账号和 token）。

如果同时开着默认的 OpenAI 导出器又没有 OpenAI key，会看到 `[non-fatal] Tracing client error 401`，不影响运行。别为了消掉它去调 `set_tracing_disabled(True)`，那样整个 tracing 都不再产生 span，Logfire 也收不到数据（见第 9 节）。想只发 Logfire、不发 OpenAI，应该用 `set_trace_processors([...])` 替换默认处理器。

Pydantic AI 手册的 [实战章节](../../../02-pydantic-ai/docs/06-practice/) 也用的是 Logfire。

---

## 4. 接 LangSmith

LangSmith 现在有官方的 OpenAI Agents 处理器，`langsmith 0.14` 里可以导入 `langsmith.integrations.openai_agents_sdk.OpenAIAgentsTracingProcessor`：

```python
import os
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "ls-..."
os.environ["LANGSMITH_PROJECT"] = "agents-demo"

from agents import set_trace_processors
from langsmith.integrations.openai_agents_sdk import OpenAIAgentsTracingProcessor

set_trace_processors([OpenAIAgentsTracingProcessor()])   # 替换默认；想双发用 add_trace_processor
```

老办法 `wrap_openai(AsyncOpenAI())` + `set_default_openai_client` 还能用，但只能看到 LLM 调用这一层，看不到 agent、tool、handoff 的结构。

---

## 5. 接 Langfuse

Langfuse 从 v3 起整个 SDK 都基于 OpenTelemetry，`langfuse 5.x` 里已经没有 `Langfuse().trace(...)` / `.span(...)` 这些方法，没法再用它们手写处理器。现在接 Agents SDK 的主流做法是用 OpenInference 的 OpenAI Agents 插桩，再配 Langfuse 的 OTel 导出：

```bash
pip install langfuse openinference-instrumentation-openai-agents
```

```python
import os
os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-..."
os.environ["LANGFUSE_SECRET_KEY"] = "sk-..."
os.environ["LANGFUSE_HOST"] = "https://cloud.langfuse.com"       # 或自托管地址

from langfuse import get_client
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

OpenAIAgentsInstrumentor().instrument()       # 把 Agents SDK 的 trace 转成 OTel span
langfuse = get_client()
# ... 跑 agent ...
langfuse.flush()
```

> 想在本地先看插桩效果，可以 `OpenAIAgentsInstrumentor().instrument(tracer_provider=自己的 TracerProvider)`，再挂一个内存导出器。一次工具调用产出的 span 依次是 `Agent workflow`(CHAIN) → `W`(AGENT) → `turn`(CHAIN) → `generation`(LLM) → `get_weather`(TOOL) → `generation`(LLM)，OpenInference 的语义属性都在。Langfuse 做的只是把这些 OTel 数据导到自己的后端，这一步需要一个 Langfuse 服务（云端或自托管）。

---

## 6. 接 OpenTelemetry

Langfuse、Phoenix、Datadog、Grafana Tempo 都接受 OTel。如果想用标准 OTel、不绑定具体平台，直接用 OpenInference 的插桩，再配上你自己的 OTLP exporter：

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint="http://collector:4318/v1/traces")))
OpenAIAgentsInstrumentor().instrument(tracer_provider=provider)
```

自己写 `TracingProcessor` 转 OTel 也可以，但 `span_id` 要自己映射，父子关系靠 `span.parent_id`，注意 span 上没有 `span.name` / `span.attributes`，内容都在 `span.span_data` 里。

---

## 7. 实战：写进自己的 ClickHouse / Postgres

```python
import json
from agents.tracing import TracingProcessor


class ClickHouseProcessor(TracingProcessor):
    def __init__(self, client):
        self.client = client
        self.buffer = []

    def on_trace_start(self, trace): ...
    def on_trace_end(self, trace): self.flush()
    def on_span_start(self, span): ...

    def on_span_end(self, span):
        data = span.span_data.export()
        self.buffer.append({
            "trace_id": span.trace_id,
            "span_id": span.span_id,
            "parent_id": span.parent_id,
            "type": data.get("type"),
            "name": data.get("name"),
            "started_at": span.started_at,          # ISO 字符串
            "ended_at": span.ended_at,
            "payload": json.dumps(data, ensure_ascii=False, default=str),   # 含 input/output/usage
            "error": json.dumps(span.error, default=str) if span.error else None,
        })
        if len(self.buffer) > 100:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        self.client.insert("agents_spans", self.buffer)     # ⚠️ 同步 IO 会阻塞 agent；生产请丢给后台线程/队列
        self.buffer = []

    def force_flush(self): self.flush()
    def shutdown(self): self.flush()
```

SDK 自带的 `BatchTraceProcessor` 可以直接复用。它接收一个实现了 `export(items)` 的 `TraceExporter`，替你处理批量、定时和队列上限，比手写 buffer 可靠。

---

## 8. 多个 processor 同时用

```python
add_trace_processor(MyLangSmithProcessor())
add_trace_processor(MyClickHouseProcessor(...))
```

每个处理器都会收到同样的 trace 和 span。默认 provider 会捕获处理器回调里的异常，继续调用其他处理器，所以一个挂了不影响别的。

---

## 9. 关掉 OpenAI 默认导出

不想同时上传到 OpenAI Platform：

```python
from agents import set_trace_processors

set_trace_processors([MyProcessor()])      # 替换默认；不包含 OpenAI 的 exporter
```

`OPENAI_AGENTS_DISABLE_TRACING=1` 或 `set_tracing_disabled(True)` 的效果是整个 tracing 都不产生 span，你手动加的处理器也收不到。可以这样验证：先 `set_trace_processors([自己的处理器])`，`set_tracing_disabled(True)` 时一次 run 收到 0 个 trace，改成 `False` 后收到 1 个。这两个开关控制的是“产不产生 span”，而不是“发不发往 OpenAI”。只发给自己的后端，要用 `set_trace_processors`。所以如果你在接千问的公共模块里已经写了 `set_tracing_disabled(True)`，跑本篇示例前要先改成 `set_tracing_disabled(False)`，再 `set_trace_processors([...])`。

官方文档列出了 30 多个已经集成好的第三方处理器（W&B、Arize Phoenix、MLflow、Braintrust、LangSmith、Langfuse、Logfire、Datadog、PostHog、Laminar、Galileo、Portkey…）。动手自己写之前，先去 [Tracing 文档的 Ecosystem integrations](https://openai.github.io/openai-agents-python/tracing/#external-tracing-processors-list) 看看有没有现成的。

---

## 10. 用 trace 数据做评测

```python
# 从 trace 后端导出
traces = export_recent_traces(days=7)

# 抽样 100 条做人工 / LLM-as-judge 评测
import random
sample = random.sample(traces, 100)

for t in sample:
    # 输入输出从 generation / agent span 里取（见第 2 节的 span_data 结构），或你在 trace_metadata 里自己存
    user_input, final_output = t["input"], t["output"]
    score = judge(user_input, final_output)  # 0-1
    save_eval(t["trace_id"], score)
```

详见 [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](../07-production/05-evals.md)。

---

## 11. 完整 demo

```python
import asyncio
import json
from agents import Agent, Runner, set_trace_processors
from agents.tracing import TracingProcessor


class StdoutProcessor(TracingProcessor):
    """简单 demo：trace 打 stdout"""
    def on_trace_start(self, trace):
        print(f"[Trace start] {trace.name}")

    def on_trace_end(self, trace):
        print(f"[Trace end] {trace.name}")

    def on_span_start(self, span):
        pass

    def on_span_end(self, span):
        d = span.span_data.export()
        print(f"  [Span] {d['type']:<11} {d.get('name') or ''}")

    def shutdown(self):
        pass

    def force_flush(self):
        pass


set_trace_processors([StdoutProcessor()])      # 替换默认处理器：不上传到 OpenAI（没有 OpenAI key 时也不会 401）


agent = Agent(name="A", instructions="...")


async def main():
    result = await Runner.run(agent, "你好")
    print("\nFinal:", result.final_output)


asyncio.run(main())
```

---

## 12. 下一步

- 另一种切入点 → [Lifecycle Hooks：生命周期钩子](./03-lifecycle-hooks.md)
- 接 Langfuse 等平台的完整做法 → [与 LangSmith / Langfuse / Logfire 集成](../06-integration/02-observability.md)
- 用 trace 做评测 → [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](../07-production/05-evals.md)
