# 跟 LangSmith / Langfuse / Logfire 集成

> **一句话**：OpenAI Agents 默认上传 Tracing 到 OpenAI Platform，但生产里通常要接 LangSmith / Langfuse / Logfire 之一——做 evals、跨服务关联、合规留存。

---

## 1. 三家对比

| | LangSmith | Langfuse | Logfire |
|---|---|---|---|
| 出身 | LangChain 团队 | 独立创业 / 开源 | Pydantic 团队 |
| 自托管 | Enterprise | ✅ 开源版 | ✅ self-hosted 选项 |
| 价格 | 按 trace | freemium | freemium |
| 评测工作流 | ✅ 内置 | ✅ 内置 | 通过 SQL |
| Python 集成 | 强 | 强 | OpenAI Agents 原生支持 |

**最简推荐**：

- **Logfire**：跟 Pydantic AI / 同一团队，原生集成最丝滑
- **Langfuse**：要自托管、开源版本
- **LangSmith**：已经在 LangChain 生态里

---

## 2. 接 Logfire（最简）

```bash
pip install logfire
```

```python
import logfire
from agents import Agent, Runner


logfire.configure(token="lf_...")
logfire.instrument_openai_agents()

agent = Agent(name="A", instructions="...")
await Runner.run(agent, "你好")
# 自动上 Logfire dashboard
```

完事。比写 Custom Tracer 简单多了。

详见 Logfire 文档：https://docs.pydantic.dev/logfire/

---

## 3. 接 Langfuse

### 方式 A：用 LangFuse 的 OpenAI integration（最简）

```bash
pip install langfuse
```

```python
import os
os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-..."
os.environ["LANGFUSE_SECRET_KEY"] = "sk-..."
os.environ["LANGFUSE_HOST"] = "https://cloud.langfuse.com"

from langfuse.openai import openai
from agents import Agent, Runner, set_default_openai_client


# 包裹的 openai client
client = openai.AsyncOpenAI()  # 自动 instrument
set_default_openai_client(client)


agent = Agent(name="A", instructions="...")
await Runner.run(agent, "...")
# LLM call 自动上 Langfuse
```

只能拿 LLM call 维度，没有 agent / tool / handoff 的高层结构。

### 方式 B：写 Langfuse Processor

```python
# demos/integration/02_langfuse.py
from langfuse import Langfuse
from agents.tracing import TracingProcessor, set_trace_processors


lf = Langfuse()


class LangfuseProcessor(TracingProcessor):
    def __init__(self):
        self._traces = {}    # trace_id -> langfuse trace
        self._spans = {}     # span_id -> langfuse span

    def on_trace_start(self, trace):
        t = lf.trace(
            name=trace.name,
            metadata=trace.metadata,
        )
        self._traces[trace.id] = t

    def on_span_start(self, span):
        parent = self._traces.get(span.trace_id)
        if parent:
            s = parent.span(
                name=span.name,
                input=getattr(span, "input", None),
                metadata=span.attributes if hasattr(span, "attributes") else {},
            )
            self._spans[span.id] = s

    def on_span_end(self, span):
        s = self._spans.pop(span.id, None)
        if s:
            s.end(
                output=getattr(span, "output", None),
            )

    def on_trace_end(self, trace):
        t = self._traces.pop(trace.id, None)
        if t:
            t.update(output=trace.metadata.get("output"))

    def shutdown(self):
        lf.flush()

    def force_flush(self):
        lf.flush()


set_trace_processors([LangfuseProcessor()])
```

---

## 4. 接 LangSmith

```bash
pip install langsmith
```

### 方式 A：环境变量 + OpenAI wrapper

```python
import os
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "ls-..."
os.environ["LANGSMITH_PROJECT"] = "openai-agents"

from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI
from agents import set_default_openai_client


client = wrap_openai(AsyncOpenAI())
set_default_openai_client(client)
```

跟 Langfuse 方式 A 一样——只到 LLM 层。

### 方式 B：自己写 Processor

类似 Langfuse Processor 思路，调 LangSmith API。

---

## 5. 多平台并行上报

```python
add_trace_processor(LogfireProcessor())
add_trace_processor(LangfuseProcessor())
add_trace_processor(MyClickHouseProcessor())
```

每个 processor 独立处理同样的 trace 数据。挂一个不影响其它。

---

## 6. 关掉 OpenAI 默认上报

```python
from agents.tracing import set_trace_processors

set_trace_processors([LogfireProcessor()])  # 替换默认
```

或环境变量：

```bash
export OPENAI_AGENTS_DISABLE_TRACING=1
```

---

## 7. metadata：方便筛选

```python
from agents import trace


with trace(
    "User Chat",
    metadata={
        "user_id": "u42",
        "session_id": "s99",
        "experiment": "agent-v3",
        "tier": "pro",
    },
):
    result = await Runner.run(agent, msg)
```

观测平台都支持按 metadata 筛选 trace。

---

## 8. 跟评测打通

观测 → 评测：

```python
# 1. 从 Langfuse / LangSmith 导出最近 7 天 trace
traces = client.get_traces(start=last_week)

# 2. 抽样
import random
sample = random.sample(traces, 100)

# 3. LLM-as-judge 评分
for t in sample:
    score = await judge(t.input, t.output)
    client.score(trace_id=t.id, name="quality", value=score)

# 4. 在 dashboard 上看 score 分布、找差的样本
```

详见 [07-production/05-evals.md](../07-production/05-evals.md)。

---

## 9. 性能开销

| 操作 | 开销 |
|------|------|
| OpenAI Platform 默认上传 | 后台异步，几乎无 |
| Logfire（OTLP） | 后台批量 |
| Langfuse Python | 异步 flush |
| 自己写 processor | 取决你怎么写 |

⚠️ Processor 内**不要做同步阻塞操作**（写本地文件 / 同步 HTTP），用 background task。

---

## 10. 完整 demo：双平台上报

```python
# demos/integration/02_observability.py
import os
import asyncio
import logfire
from agents import Agent, Runner, trace


# 1. 配 Logfire（OpenAI Agents 原生）
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"))
logfire.instrument_openai_agents()


# 2. 跑
agent = Agent(name="A", instructions="...", model="gpt-4o-mini")


async def main():
    with trace(
        "Demo Chat",
        metadata={"user_id": "demo", "version": "v1"},
    ):
        result = await Runner.run(agent, "你好")
        print(result.final_output)


asyncio.run(main())
```

---

## 11. 我应该选哪个

```
单一项目，要最省事 → Logfire
开源 / 自托管要求 → Langfuse
已在 LangChain 生态 → LangSmith
要严格 OTel 标准 → 写 OTel processor 接 Datadog / Grafana
内部数据不出去 → 自己写 ClickHouse / Postgres processor
```

---

## 12. 下一步

- 📖 部署 FastAPI → [03-fastapi-deploy.md](./03-fastapi-deploy.md)
- 📖 评测：把 trace 当数据源 → [07-production/05-evals.md](../07-production/05-evals.md)
- 📖 跨手册：LangSmith 完整指南 → [01-langchain/02-langsmith](../../../01-langchain/docs/02-langsmith/)
