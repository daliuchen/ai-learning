# 自定义 Tracer：接 LangSmith / Langfuse / Logfire

> **一句话**：实现 `TracingProcessor` 把 trace 数据导到任何后端——LangSmith / Langfuse / Logfire / OpenTelemetry / 自家 ClickHouse 都行。

---

## 1. 为啥要换 tracer

OpenAI Platform Traces 够用，但局限：

- 多模型混用（你既用 OpenAI 也用 Claude）→ trace 不连贯
- 想做 evals 工作流（LangSmith / Langfuse 内置）
- 内部合规要求自托管
- 想跟现有 OpenTelemetry / Datadog 打通

---

## 2. TracingProcessor 接口

```python
from agents.tracing import TracingProcessor, Trace, Span


class MyProcessor(TracingProcessor):
    def on_trace_start(self, trace: Trace) -> None: ...
    def on_trace_end(self, trace: Trace) -> None: ...
    def on_span_start(self, span: Span) -> None: ...
    def on_span_end(self, span: Span) -> None: ...
    def shutdown(self) -> None: ...
    def force_flush(self) -> None: ...


from agents import add_trace_processor
add_trace_processor(MyProcessor())
```

`add_trace_processor` 是**追加**——不会替换 OpenAI 默认的，可以多目的同时上报。

要替换：

```python
from agents.tracing import set_trace_processors

set_trace_processors([MyProcessor()])  # 列表覆盖
```

---

## 3. 接 Logfire（Pydantic AI 同款）

Logfire 自带 OpenAI Agents SDK 集成：

```python
import logfire


logfire.configure(token="...")
logfire.instrument_openai_agents()
```

完事。之后所有 trace 自动上 Logfire dashboard。

```python
from agents import Agent, Runner

agent = Agent(name="A", instructions="...")
await Runner.run(agent, "你好")
# 自动到 Logfire
```

跨手册关联：Pydantic AI 章节 [02-pydantic-ai/06-practice](../../../02-pydantic-ai/docs/06-practice/) 也用 Logfire。

---

## 4. 接 LangSmith

LangSmith 没有原生 OpenAI Agents 集成，但能接：

### 方式 A：用 LangSmith 的 OpenAI wrapping

```python
import os

os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "ls-..."
os.environ["LANGSMITH_PROJECT"] = "agents-demo"

from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI


# 包裹 OpenAI client
wrapped_client = wrap_openai(AsyncOpenAI())

# 把 wrapped client 喂给 SDK
from agents import set_default_openai_client
set_default_openai_client(wrapped_client)
```

LLM call 维度上报到 LangSmith。

### 方式 B：写 LangSmith Processor

```python
import langsmith
from agents.tracing import TracingProcessor


class LangSmithProcessor(TracingProcessor):
    def __init__(self):
        self.client = langsmith.Client()

    def on_trace_end(self, trace):
        self.client.create_run(
            name=trace.name,
            run_type="chain",
            inputs={"input": trace.metadata.get("user_input")},
            outputs={"output": trace.metadata.get("output")},
            extra={"spans": [s.export() for s in trace.spans]},
        )
    # ... 其它方法


from agents import add_trace_processor
add_trace_processor(LangSmithProcessor())
```

详见 LangSmith API 文档调整字段。

---

## 5. 接 Langfuse

```python
from langfuse import Langfuse
from agents.tracing import TracingProcessor


class LangfuseProcessor(TracingProcessor):
    def __init__(self):
        self.client = Langfuse()

    def on_trace_start(self, trace):
        trace.langfuse_trace = self.client.trace(
            name=trace.name,
            metadata=trace.metadata,
        )

    def on_span_end(self, span):
        parent = span.trace.langfuse_trace
        parent.span(
            name=span.name,
            input=span.input,
            output=span.output,
            start_time=span.started_at,
            end_time=span.ended_at,
            metadata=span.metadata,
        )

    def on_trace_end(self, trace):
        trace.langfuse_trace.update(
            output=trace.metadata.get("output"),
        )

    def force_flush(self):
        self.client.flush()
```

---

## 6. 接 OpenTelemetry

```python
from opentelemetry import trace as otel_trace
from agents.tracing import TracingProcessor


tracer = otel_trace.get_tracer("agents")


class OtelProcessor(TracingProcessor):
    def __init__(self):
        self._spans = {}

    def on_trace_start(self, trace):
        ctx_span = tracer.start_span(trace.name)
        self._spans[trace.id] = ctx_span

    def on_span_start(self, span):
        ctx_span = tracer.start_span(span.name, attributes=span.attributes)
        self._spans[span.id] = ctx_span

    def on_span_end(self, span):
        ctx_span = self._spans.pop(span.id, None)
        if ctx_span:
            ctx_span.end()

    def on_trace_end(self, trace):
        ctx_span = self._spans.pop(trace.id, None)
        if ctx_span:
            ctx_span.end()


add_trace_processor(OtelProcessor())
```

打通后能在 Datadog / Honeycomb / Grafana Tempo 看 trace。

---

## 7. 实战：自家 ClickHouse / Postgres

```python
import json
from agents.tracing import TracingProcessor


class ClickHouseProcessor(TracingProcessor):
    def __init__(self, client):
        self.client = client
        self.buffer = []

    def on_span_end(self, span):
        self.buffer.append({
            "trace_id": span.trace_id,
            "span_id": span.id,
            "name": span.name,
            "started_at": span.started_at,
            "ended_at": span.ended_at,
            "duration_ms": (span.ended_at - span.started_at) * 1000,
            "input": json.dumps(span.input, default=str),
            "output": json.dumps(span.output, default=str),
            "metadata": json.dumps(span.metadata),
        })
        if len(self.buffer) > 100:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        self.client.insert("agents_spans", self.buffer)
        self.buffer = []

    def shutdown(self):
        self.flush()
```

---

## 8. 多 processor 并行

```python
add_trace_processor(LogfireProcessor())
add_trace_processor(LangSmithProcessor())
add_trace_processor(ClickHouseProcessor(...))
```

每个独立处理同样的 trace 数据。

---

## 9. 关掉 OpenAI 默认 export

不想同时往 OpenAI Platform 上传：

```python
from agents.tracing import set_trace_processors

set_trace_processors([
    LogfireProcessor(),
    # 不包括 OpenAI 默认 processor
])
```

或者环境变量：

```bash
export OPENAI_AGENTS_DISABLE_TRACING=1   # 关默认 export，但你手动加的还跑
```

---

## 10. trace 数据怎么用做评测

```python
# 从 trace 后端导出
traces = export_recent_traces(days=7)

# 抽样 100 条做人工 / LLM-as-judge 评测
import random
sample = random.sample(traces, 100)

for trace in sample:
    user_input = trace.metadata.get("user_input")
    final_output = trace.metadata.get("final_output")
    score = judge(user_input, final_output)  # 0-1
    save_eval(trace.id, score)
```

详见 [07-production/05-evals.md](../07-production/05-evals.md)。

---

## 11. 完整 demo

```python
# demos/advanced/02_custom_tracer.py
import asyncio
import json
from agents import Agent, Runner, add_trace_processor
from agents.tracing import TracingProcessor


class StdoutProcessor(TracingProcessor):
    """简单 demo：trace 打 stdout"""
    def on_trace_start(self, trace):
        print(f"[Trace start] {trace.name}")

    def on_trace_end(self, trace):
        print(f"[Trace end] {trace.name}")

    def on_span_start(self, span):
        print(f"  [Span start] {span.name}")

    def on_span_end(self, span):
        print(f"  [Span end] {span.name}")

    def shutdown(self):
        pass

    def force_flush(self):
        pass


add_trace_processor(StdoutProcessor())


agent = Agent(name="A", instructions="...")


async def main():
    result = await Runner.run(agent, "你好")
    print("\nFinal:", result.final_output)


asyncio.run(main())
```

---

## 12. 下一步

- 📖 Lifecycle Hooks 是另一种切入点 → [03-lifecycle-hooks.md](./03-lifecycle-hooks.md)
- 📖 接 Langfuse 完整指南 → [06-integration/02-observability.md](../06-integration/02-observability.md)
- 📖 用 trace 做评测 → [07-production/05-evals.md](../07-production/05-evals.md)
