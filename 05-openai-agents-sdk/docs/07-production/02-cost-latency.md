# Cost / Latency 优化

> **一句话**：用 caching、模型分层、并发、output 长度限制把 cost 砍一半、latency 降一半——不要等用户抱怨账单了才开始。

---

## 1. 找出"贵在哪 / 慢在哪"

先 trace + metric：

```python
# 每次 run 后记录
log.info(
    "agent_run",
    user_id=user_id,
    input_tokens=result.usage.input_tokens,
    output_tokens=result.usage.output_tokens,
    requests=result.usage.requests,   # LLM call 次数
    duration_ms=duration,
    agent=result.last_agent.name,
)
```

按维度看 P50 / P95 / P99：

- 哪个 agent 烧最多
- 哪个 tool 调最慢
- 哪些 user 烧最多

没数据 → 别盲优化。

---

## 2. 优化 1：选对模型

| 任务 | 默认 | 优化 | 节省 |
|------|------|------|------|
| 分类 / 抽取 | gpt-4o | gpt-4o-mini | ~5x |
| 简单回答 | gpt-4o | gpt-4o-mini | ~5x |
| 复杂推理 | gpt-4o | gpt-4o（保留） | - |
| 流程协调 | gpt-4o | gpt-4o-mini | ~5x |

模型分层（多 Agent）：

```python
researcher = Agent(name="R", model="gpt-4o-mini")  # 子任务
writer = Agent(name="W", model="gpt-4o")            # 主综合
```

---

## 3. 优化 2：Prompt Caching

OpenAI 的 prompt caching 自动启用——长 system prompt 复用时第二次便宜（50% off）。

确保：

- system prompt **稳定**（每次都一样）
- few-shot example 放 system prompt 里
- 在 trace 里看 `cache_read_tokens`

```python
# trace 查看
result.usage.input_tokens_details.cached_tokens  # 多少命中
```

详见 [04-prompt-engineering/07-production/02-caching.md](../../../04-prompt-engineering/docs/07-production/02-caching.md)。

---

## 4. 优化 3：output 长度

```python
from agents.model_settings import ModelSettings


agent = Agent(
    name="A",
    instructions="回答用 100 字内",
    model_settings=ModelSettings(max_tokens=200),
)
```

output token 比 input 贵 4-5 倍。

prompt 里加 "用 100 字以内回答" + `max_tokens` 双重控制。

---

## 5. 优化 4：并发跑 Agent

```python
async def parallel_research(question: str):
    sub_questions = await decompose(question)

    # 并发跑
    results = await asyncio.gather(*[
        Runner.run(researcher, q) for q in sub_questions
    ])

    return await Runner.run(synthesizer, str(results))
```

5 个 sub-question 并发 vs 串行：latency 5× 降。

⚠️ 注意 rate limit。

---

## 6. 优化 5：缓存 tool 调用

工具调用结果若**幂等**：

```python
from functools import lru_cache


_cache = {}


@function_tool
def get_weather(city: str) -> str:
    if city in _cache:
        return _cache[city]
    result = _real_call(city)
    _cache[city] = result
    return result
```

或用 Redis cache：

```python
@function_tool
async def get_weather(city: str) -> str:
    cached = await redis.get(f"weather:{city}")
    if cached:
        return cached.decode()

    result = await _real_call(city)
    await redis.setex(f"weather:{city}", 300, result)  # 5 min
    return result
```

---

## 7. 优化 6：减少 max_turns

```python
result = await Runner.run(agent, "...", max_turns=5)  # 别开 30
```

- 简单任务 → 3-5
- 复杂研究 → 15-20
- 默认 10

调小防"模型走错路兜兜转转"。

---

## 8. 优化 7：tool_choice + parallel_tool_calls

```python
ModelSettings(
    parallel_tool_calls=True,  # 一次让模型同时调 N 个 tool
    tool_choice="auto",        # 别 required，除非必要
)
```

```python
# ❌ 串行 6 个城市
# 模型调 get_weather → wait → 调下一个 → wait...

# ✅ parallel：一次调 6 个
ModelSettings(parallel_tool_calls=True)
```

---

## 9. 优化 8：Sessions 上限

历史越长 prompt 越长：

```python
# 限制最近 N 条
items = await session.get_items(limit=20)
```

或定期摘要老历史（详见 [01-basics/06-sessions.md](../01-basics/06-sessions.md)）。

---

## 10. 优化 9：Streaming（感知 latency）

实际 latency 没变，但用户感知好：

```python
result = Runner.run_streamed(agent, "...")
async for event in result.stream_events():
    yield event  # 用户立刻看到 token
```

用户第一个字 200ms 出现 vs 5 秒后整段——感知差别巨大。

---

## 11. 优化 10：Hosted Tool 谨慎用

`WebSearchTool` 每次调用都收费 ($0.025+)。

```python
agent = Agent(
    instructions="""...
**仅在**用户问"最新"、"今天"、"现在" 时用 web_search。
**不要**对历史性 / 常识性问题用 web_search。
""",
    tools=[WebSearchTool()],
)
```

用 instructions 约束模型调用频率。

---

## 12. 监控 dashboard

至少看这几个指标：

| 指标 | 阈值 |
|------|------|
| P95 latency | < 3s（chat）/ < 30s（research） |
| Average cost per request | < $0.01（chat）/ < $0.10（research） |
| Cache hit rate | > 50% |
| LLM error rate | < 1% |
| Tool error rate | < 5% |
| Cost / day | 设报警 |

---

## 13. 完整 demo：监控 + 优化

```python
# demos/production/02_optimized.py
import asyncio
import time
from agents import Agent, Runner, function_tool
from agents.model_settings import ModelSettings
from agents.lifecycle import RunHooks


_weather_cache = {}


@function_tool
def get_weather(city: str) -> str:
    if city in _weather_cache:
        return _weather_cache[city]
    result = f"{city}: 22°C"
    _weather_cache[city] = result
    return result


class CostTracker(RunHooks):
    def __init__(self):
        self.t0 = time.time()


# 模型分层 + 限制
agent = Agent(
    name="WeatherBot",
    instructions="用 get_weather 查询，回答 50 字内",
    tools=[get_weather],
    model="gpt-4o-mini",  # 便宜
    model_settings=ModelSettings(
        max_tokens=200,
        parallel_tool_calls=True,
    ),
)


async def main():
    tracker = CostTracker()
    result = await Runner.run(
        agent,
        "查北京、上海、广州的天气",
        max_turns=5,
        hooks=tracker,
    )

    dt = time.time() - tracker.t0
    cost = (
        result.usage.input_tokens * 0.00015 / 1000
        + result.usage.output_tokens * 0.0006 / 1000
    )

    print(f"\n=== Performance ===")
    print(f"Duration: {dt:.2f}s")
    print(f"Input tokens: {result.usage.input_tokens}")
    print(f"Output tokens: {result.usage.output_tokens}")
    print(f"Estimated cost: ${cost:.4f}")
    print(f"\nOutput:\n{result.final_output}")


asyncio.run(main())
```

---

## 14. 下一步

- 📖 Error handling → [03-error-handling.md](./03-error-handling.md)
- 📖 安全 → [04-security.md](./04-security.md)
- 📖 评测 → [05-evals.md](./05-evals.md)
