# 与 LangSmith / Langfuse / Logfire 集成

> Agents SDK 默认把 trace 传到 OpenAI Platform，但生产环境里通常还要接一个第三方观测平台，用来做评测、跨服务关联、合规留存。官方文档列了 30 多个已集成的第三方 tracing 处理器，自己动手写之前先去名单里找找。
>
> 下面三种接法都要在对应平台注册账号、拿到 key 才能上传数据。

---

## 1. 三家对比

| | LangSmith | Langfuse | Logfire |
|---|---|---|---|
| 出身 | LangChain 团队 | 独立创业 / 开源 | Pydantic 团队 |
| 自托管 | Enterprise | 支持（开源版） | 企业版（SDK 开源，托管平台闭源） |
| 价格 | 按 trace | freemium | freemium |
| 评测工作流 | 内置 | 内置 | 通过 SQL |
| Python 集成 | 官方处理器 `OpenAIAgentsTracingProcessor` | OTel：OpenInference 插桩 | `instrument_openai_agents()` 一行 |

怎么选：Logfire 和 Pydantic AI 出自同一团队，原生集成最省事；要自托管、要开源就用 Langfuse；已经在 LangChain 生态里的，用 LangSmith。

---

## 2. 接 Logfire（最简）

```bash
pip install logfire
```

```python
import logfire
from agents import Agent, Runner


logfire.configure(token="lf_...")      # 或 send_to_logfire=False 只本地
logfire.instrument_openai_agents()     # logfire 5.4 里有这个函数

agent = Agent(name="A", instructions="...")
await Runner.run(agent, "你好")
# 自动上 Logfire dashboard
```

两行配置就够了，比写 Custom Tracer 省事得多。文档见 https://docs.pydantic.dev/logfire/

---

## 3. 接 Langfuse

Langfuse 从 v3 起整个 SDK 都建在 OpenTelemetry 上。另外两种接法不适合 Agents SDK：`from langfuse.openai import openai` 包一层 client 只能看到 LLM 调用；手写处理器调用 `Langfuse().trace(...)` 在 `langfuse 5.x` 里已经不可行，这个方法已移除。接 Agents SDK 用 OpenInference 插桩加 Langfuse 的 OTel 导出，agent、tool、handoff、guardrail 的层级结构都能保留：

```bash
pip install langfuse openinference-instrumentation-openai-agents
```

```python
import os
os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-..."
os.environ["LANGFUSE_SECRET_KEY"] = "sk-..."
os.environ["LANGFUSE_HOST"] = "https://cloud.langfuse.com"        # 或自托管

from langfuse import get_client
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor

OpenAIAgentsInstrumentor().instrument()        # Agents SDK 的 trace → OTel span → Langfuse
langfuse = get_client()

# ... 正常跑 agent ...
langfuse.flush()                                # 短脚本退出前 flush
```

插桩后，一次带工具调用的 agent 运行会产生这几类 span：`Agent workflow`(CHAIN) → agent 本身（AGENT，span 名是 agent 的 `name`）→ `turn`(CHAIN) → `generation`(LLM) 和工具调用（TOOL，span 名是工具函数名，比如 `get_weather`），每个 span 都带 OpenInference 的语义属性。`instrument()` 也接受 `tracer_provider=` 参数，可以把 span 发到自己指定的 OTel provider。

---

## 4. 接 LangSmith

```bash
pip install langsmith
```

LangSmith 现在提供了官方的 OpenAI Agents 处理器（`langsmith 0.14` 里可以导入）：

```python
import os
os.environ["LANGSMITH_TRACING"] = "true"
os.environ["LANGSMITH_API_KEY"] = "ls-..."
os.environ["LANGSMITH_PROJECT"] = "openai-agents"

from agents import set_trace_processors
from langsmith.integrations.openai_agents_sdk import OpenAIAgentsTracingProcessor

set_trace_processors([OpenAIAgentsTracingProcessor()])     # 替换默认；想同时发 OpenAI 就用 add_trace_processor
```

旧办法（`wrap_openai(AsyncOpenAI())` + `set_default_openai_client`）还能用，但只能看到 LLM 调用，没有 agent / tool / handoff 的结构。

---

## 5. 多平台并行上报

```python
# Logfire / OpenInference 这类“插桩”是各自在 SDK 里注册处理器，直接并存；自己写的处理器用 add_trace_processor
logfire.instrument_openai_agents()
add_trace_processor(MyClickHouseProcessor())
```

每个 processor 拿到的是同一份 trace 数据，各自独立处理。默认 provider 会捕获单个处理器回调里的异常，然后继续调用其他处理器，所以挂掉一个不影响别的。自己写处理器时用到的接口（`span.span_data.export()` 等）见 [自定义 Tracer：接 LangSmith / Langfuse / Logfire](../05-advanced/02-custom-tracer.md)。

---

## 6. 关掉 OpenAI 默认上报

```python
from agents.tracing import set_trace_processors

set_trace_processors([MyProcessor()])  # 替换默认（不再发往 OpenAI）
```

这里容易搞错：`OPENAI_AGENTS_DISABLE_TRACING=1` 和 `set_tracing_disabled(True)` 会让整个 tracing 都不产生 span，你接的 Logfire、Langfuse、LangSmith 也一起收不到。如果只是不想发给 OpenAI，用上面的 `set_trace_processors`。没有 OpenAI key 时，默认导出器会打出 `[non-fatal] Tracing client error 401`，不影响运行。

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

这几个观测平台都能按 metadata 筛选 trace。

---

## 8. 跟评测打通

trace 攒下来以后，可以直接当评测数据用：

```python
# 1. 从 Langfuse / LangSmith 导出最近 7 天 trace（各平台 SDK 的查询 API 不同，这里是示意）
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

具体做法见 [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](../07-production/05-evals.md)。

---

## 9. 性能开销

| 操作 | 开销 |
|------|------|
| OpenAI Platform 默认上传 | 后台异步，几乎无 |
| Logfire（OTLP） | 后台批量 |
| Langfuse Python | 异步 flush |
| 自己写 processor | 取决你怎么写 |

自己写 processor 时，别在里面做同步阻塞的操作（写本地文件、同步 HTTP），丢给后台任务去做。

---

## 10. 完整 demo：双平台上报

```python
import os
import asyncio
import logfire
from agents import Agent, Runner, trace


# 1. 配 Logfire（OpenAI Agents 原生）
logfire.configure(token=os.getenv("LOGFIRE_TOKEN"), send_to_logfire="if-token-present")
logfire.instrument_openai_agents()


# 2. 跑
agent = Agent(name="A", instructions="...", model="gpt-6-luna")


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

- 部署 FastAPI → [FastAPI / Lambda 部署](./03-fastapi-deploy.md)
- 把 trace 当评测数据源 → [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](../07-production/05-evals.md)
- LangSmith 完整指南 → [LangChain 手册的 LangSmith 部分](../../../01-langchain/docs/02-langsmith/)
