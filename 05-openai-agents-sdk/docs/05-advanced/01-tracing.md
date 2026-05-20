# Tracing 内置 & OpenAI Platform Dashboard

> **一句话**：装好 SDK 就有 trace——每次 Runner.run 自动上传到 https://platform.openai.com/traces，能看 LLM call / tool call / handoff / token / 时长。

---

## 1. 默认行为：开

```python
from agents import Agent, Runner

agent = Agent(name="A", instructions="...")
await Runner.run(agent, "你好")
# 自动产生 trace，上传到 OpenAI Platform
```

去 https://platform.openai.com/traces 看到一条新 trace：

```
Run "Agent workflow" (12.3s, 1234 tokens)
├─ Agent: A
│  ├─ LLM call (gpt-4o-mini, 800 tokens)
│  └─ Final output
```

---

## 2. 关掉 Tracing

```python
from agents import set_tracing_disabled

set_tracing_disabled(True)
```

或环境变量：

```bash
export OPENAI_AGENTS_DISABLE_TRACING=1
```

适合：

- 本地 dev 不想污染 dashboard
- 生产用其它平台（LangSmith / Langfuse）替代

---

## 3. trace 的层级结构

```
Trace                ← 一次 Runner.run 顶层
  └─ Span: Agent     ← 一个 Agent 的执行段
       ├─ Span: Generation  ← 一次 LLM 调用
       ├─ Span: Function    ← 一个 function_tool 调用
       ├─ Span: Handoff     ← handoff 事件
       └─ Span: Guardrail   ← guardrail 检查
```

Span 自动嵌套，能在 dashboard 看树形结构。

---

## 4. 给 trace 加名字

```python
from agents import trace

with trace("My Custom Workflow"):
    result = await Runner.run(agent, "...")
```

Dashboard 里这条 trace 标题就是 "My Custom Workflow"。

---

## 5. 包多次 Runner.run 到一个 trace

默认每次 Runner.run = 一个 trace。要合并：

```python
from agents import trace


async def pipeline(question: str):
    with trace("Research Pipeline"):
        plan = await Runner.run(planner, question)
        research = await Runner.run(researcher, plan.final_output)
        report = await Runner.run(writer, research.final_output)
        return report.final_output
```

三次 run 合到一个 trace，方便看整个 pipeline 耗时。

---

## 6. 加自定义 metadata

```python
with trace(
    "Customer Service",
    metadata={
        "user_id": "u42",
        "session_id": "s99",
        "experiment": "v3",
    },
):
    result = await Runner.run(agent, msg)
```

Dashboard 里能按 metadata 筛选。

---

## 7. 加自定义 Span

```python
from agents.tracing import custom_span


with trace("Pipeline"):
    async with custom_span("Pre-process") as span:
        cleaned = clean_input(raw_input)
        span.set_attribute("input_len", len(raw_input))

    result = await Runner.run(agent, cleaned)

    async with custom_span("Post-process") as span:
        formatted = format_output(result.final_output)
```

适合：自己写的代码段也想在 trace 上看。

---

## 8. 看 Trace 该看啥

Dashboard 上一个 trace 可以看到：

- **总时长**：分清是 LLM 慢还是 tool 慢
- **Token usage**：哪一步烧最多
- **Tool inputs / outputs**：模型给 tool 啥参数 / tool 返了啥
- **Handoff 链**：从哪个 agent 转到哪个
- **错误**：哪一步抛了异常

---

## 9. 多 user / 多环境隔离

OpenAI Platform 没有内置 project / env 概念（在 Trace metadata 里手动加）：

```python
with trace(
    "API request",
    metadata={
        "env": "production",   # production / staging / dev
        "service": "customer-bot",
        "version": "v3.2.1",
    },
):
    ...
```

Dashboard 上按 metadata 过滤。

---

## 10. 跟 LangSmith / Langfuse 对比

| | OpenAI Traces | LangSmith | Langfuse |
|---|---|---|---|
| 开箱即用 | ✅ 默认开 | 需配置 key | 需配置 key |
| 多模型支持 | 仅 OpenAI 模型 | 全 | 全 |
| 评测 | ❌ 弱 | ✅ Evals 一等公民 | ✅ Evals |
| 项目管理 | ❌ 只有 metadata | ✅ 内置 | ✅ 内置 |
| 自托管 | ❌ | ✅ | ✅（开源） |
| 价格 | OpenAI 免费 | 按 trace 数 | freemium |

**何时切到外部平台**：

- 需要多 model（非 OpenAI 也用）
- 需要 evals 工作流
- 需要严格数据合规（内部环境）

详见 [02-custom-tracer.md](./02-custom-tracer.md)。

---

## 11. 数据隐私 / 不上传 trace 内容

`OPENAI_AGENTS_DISABLE_TRACING=1` 完全关，但有时想"保留 trace 结构但隐藏敏感内容"：

```python
from agents import set_tracing_export_api_key

# 用专门的 dev key（限流低）
set_tracing_export_api_key("sk-...")
```

或代码层：

```python
@input_guardrail
async def mask_pii(ctx, agent, user_input):
    # 这个 guardrail 在 trace 里能看到原始 input
    # 想隐藏：input 进来前自己 mask
    ...
```

---

## 12. 完整 demo

```python
# demos/advanced/01_tracing.py
import asyncio
from agents import Agent, Runner, trace, function_tool


@function_tool
def search(query: str) -> str:
    return f"results for {query}"


writer = Agent(
    name="Writer",
    instructions="把素材写成报告",
    model="gpt-4o-mini",
)


researcher = Agent(
    name="Researcher",
    instructions="先 search，再总结",
    tools=[search],
    model="gpt-4o-mini",
)


async def pipeline(question: str, user_id: str):
    with trace(
        "Research Pipeline",
        metadata={"user_id": user_id, "env": "demo"},
    ):
        research_result = await Runner.run(researcher, question)
        final_result = await Runner.run(
            writer,
            f"基于研究材料写 200 字报告:\n{research_result.final_output}",
        )
        return final_result.final_output


async def main():
    answer = await pipeline("LLM 推理速度怎么提升", user_id="u1")
    print(answer)
    print("\n去 https://platform.openai.com/traces 看 trace")


asyncio.run(main())
```

---

## 13. 下一步

- 📖 接 LangSmith / Langfuse → [02-custom-tracer.md](./02-custom-tracer.md)
- 📖 用 Lifecycle Hooks 做监控 → [03-lifecycle-hooks.md](./03-lifecycle-hooks.md)
- 📖 把 trace 数据导出做评测 → [07-production/05-evals.md](../07-production/05-evals.md)
