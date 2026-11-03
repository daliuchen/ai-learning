# Cost / Latency 优化

> 成本和延迟的优化手段大多不复杂：选对模型、用好 prompt caching、限制输出长度、能并发就并发、压缩历史。难的是先搞清楚钱和时间花在了哪儿。这件事最好在上线初期就做，别等账单出来再补。

---

## 1. 找出"贵在哪 / 慢在哪"

先把 trace 和 metric 记下来：

```python
# 每次 run 后记录
usage = result.context_wrapper.usage       # 注意：不是 result.usage
log.info(
    "agent_run",
    user_id=user_id,
    input_tokens=usage.input_tokens,
    output_tokens=usage.output_tokens,
    requests=usage.requests,               # LLM call 次数
    per_request=[(r.input_tokens, r.output_tokens) for r in usage.request_usage_entries],   # 每次调用的明细
    duration_ms=duration,
    agent=result.last_agent.name,
)
```

统计用量时有几个细节：流式 run 的 usage 要等迭代器结束才完整；第三方 Chat Completions 端点在流式下要设 `ModelSettings(include_usage=True)`；`OpenAIResponsesCompactionSession` 自动压缩产生的用量会算进当次 run；run 抛异常时，`exc.run_data.context_wrapper.usage` 里能拿到出错前已经花掉的 token。

有了数据，再按 agent、tool、用户几个维度看 P50 / P95 / P99：哪个 agent 花钱最多，哪个 tool 最慢，哪些用户用量最大。没有这些数据就动手优化，多半是白忙。

---

## 2. 优化 1：选对模型

| 任务 | 默认 | 优化 | 节省（量级，以当前价格页为准） |
|------|------|------|------|
| 分类 / 抽取 | gpt-6.1-sol | 小模型（gpt-6-luna / 千问 flash 等） | 数倍 |
| 简单回答 | gpt-6.1-sol | 小模型 | 数倍 |
| 复杂推理 | gpt-6.1-sol | gpt-6.1-sol（保留） | - |
| 流程协调 | gpt-6.1-sol | 小模型 | 数倍 |

不写 `model` 时，SDK 从 0.20 起默认用 `gpt-5.6-luna`（`reasoning.effort="none"`，面向高并发、低成本场景），更早的版本默认模型不同。所以升级 SDK 可能悄悄换掉你的模型，成本和质量跟着变。生产环境要显式写 model，别依赖默认值。推理模型（GPT-5 系列起，包括示例用的 GPT-6 系）还可以用 `ModelSettings(reasoning=Reasoning(effort="low"))` 和 `verbosity="low"` 控制推理 token 和输出长度。

多 Agent 场景可以按角色分层：

```python
researcher = Agent(name="R", model="gpt-6-luna")  # 子任务
writer = Agent(name="W", model="gpt-6.1-sol")            # 主综合
```

---

## 3. 优化 2：Prompt Caching

OpenAI 的 prompt caching 默认隐式开启。prompt 足够长且前缀稳定时，重复请求会命中缓存，价格更低（折扣比例看 OpenAI 价格页）。

要想多命中，system prompt 保持每次一样，变化的内容放到后面；few-shot 示例也放进 system prompt。命中情况可以这样看：

```python
usage = result.context_wrapper.usage
usage.input_tokens_details.cached_tokens        # 命中多少（还有 cache_write_tokens 字段）
```

需要显式控制缓存时，用 `ModelSettings(prompt_cache_options={"mode": "explicit", "ttl": "30m"})`（GPT-5.6 及之后；`"30m"` 是默认档，表示缓存至少保留 30 分钟，Responses API 里还可以设 `"12h"`，即至少 12 小时），并在要复用的前缀结束处的内容片段上加 `"prompt_cache_breakpoint": {"mode": "explicit"}`；老模型家族用 `prompt_cache_retention="24h"`。0.14 起 SDK 也会自动给请求带一个默认的 prompt cache key。第三方 Chat Completions 端点支不支持、折扣多少，要看各家 provider。

更多细节见 [PE Production 02：Prompt Caching 实战](../../../04-prompt-engineering/docs/07-production/02-caching.md)。

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

output token 的单价通常是 input 的好几倍。推理模型要小心：`max_tokens` 会把推理 token 也算进去，设得太小，预算可能全花在推理上，一个字都没输出。在 Chat Completions 路径下，`finish_reason="length"` 且既没有文本也没有 tool call 时，SDK 会抛 `ModelBehaviorError`（它把这种情况当作预算耗尽，而不是模型拒答）。

实践中我会在 prompt 里写「100 字以内」，同时设 `max_tokens`，两边一起约束。

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

5 个子问题并发跑，总延迟接近最慢的那一个，而不是五个相加。并发上去以后，留意 rate limit。

---

## 6. 优化 5：缓存 tool 调用

如果工具调用是幂等的，结果可以缓存：

```python
_cache = {}


@function_tool
def get_weather(city: str) -> str:
    if city in _cache:
        return _cache[city]
    result = _real_call(city)
    _cache[city] = result
    return result
```

多实例部署时换成 Redis：

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

简单任务设 3-5，复杂研究设 15-20。默认是 10，`max_turns=None` 会关掉上限，慎用。调小可以防止模型走错路后反复兜圈子；撞到上限时，用 `error_handlers={"max_turns": ...}` 给一个兜底回答，别直接返回 500。

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

## 9. 优化 8：历史越长 prompt 越长

SDK 有三个现成工具：

```python
from agents import RunConfig, SessionSettings
from agents.extensions import ToolOutputTrimmer

# 1) 每次只取最近 N 条历史
RunConfig(session_settings=SessionSettings(limit=20))

# 2) 把老轮次里的大块工具输出换成预览（对话本身保留）
RunConfig(call_model_input_filter=ToolOutputTrimmer(recent_turns=2, max_output_chars=500, preview_chars=200))

# 3) 自动压缩（仅 OpenAI Responses）：OpenAIResponsesCompactionSession，或 ModelSettings(context_management=[{"type": "compaction", "compact_threshold": 200000}])
```

`ToolOutputTrimmer` 把老轮次的大块工具输出换成预览、对话本身保留，所以输入 token 不再随轮次累加，模型仍然知道老轮次里聊过什么。示意：工具每次返回一份 1～2k 字的报告，连聊 4 轮，参数 `ToolOutputTrimmer(recent_turns=1, max_output_chars=300, preview_chars=80)`。前三轮每轮的 input 都停在两千 token 上下，因为只有最近一轮的报告保留全文，更早的已经换成预览；第 4 轮不调工具、只问「刚才聊了哪几个报告」时，三份报告都只剩预览，input 降到几百 token，模型照样能答出「报告 A、B、C」。

另一种办法是定期把老历史做成摘要（见 [Sessions：会话状态自动管理](../01-basics/06-sessions.md)）。工具特别多时，用 tool search / `tool_namespace` 延迟加载工具 schema（见 [动态工具集与上下文感知工具](../02-tools/05-dynamic-tools.md)），也能省下不少输入 token。

---

## 10. 优化 9：Streaming（感知 latency）

流式不会让总耗时变短，但用户体感好很多：

```python
result = Runner.run_streamed(agent, "...")
async for event in result.stream_events():
    yield event  # 用户立刻看到 token
```

首个 token 通常几百毫秒内就能出来，和等好几秒一次性出整段相比，用户的感受差别很大。

---

## 11. 优化 10：Hosted Tool 谨慎用

`WebSearchTool` 每调用一次都要额外收费（价格看 OpenAI 价格页），调用多了会成为成本大头。可以在 instructions 里限制它的使用时机：

```python
agent = Agent(
    instructions="""...
**仅在**用户问"最新"、"今天"、"现在" 时用 web_search。
**不要**对历史性 / 常识性问题用 web_search。
""",
    tools=[WebSearchTool()],
)
```

---

## 12. 监控 dashboard

至少看这几个指标（阈值是示意，按自己的业务定）：

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
import asyncio
import time
from agents import Agent, Runner, function_tool
from agents.model_settings import ModelSettings
from agents import RunHooks


_weather_cache = {}


@function_tool
def get_weather(city: str) -> str:
    if city in _weather_cache:
        return _weather_cache[city]
    result = f"{city}: 22°C"
    _weather_cache[city] = result
    return result


# 单价是示例值（每百万 token 美元，与 gpt-6-luna 当前价格一致），换模型时一起改；
# 缓存命中的 input 更便宜，要精确就用 usage.input_tokens_details.cached_tokens 拆开算
PRICE_IN, PRICE_OUT = 0.10, 0.50


class CostTracker(RunHooks):
    """每次模型调用结束时累计 token 和费用"""

    def __init__(self):
        self.t0 = time.time()
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    async def on_llm_end(self, context, agent, response):
        self.llm_calls += 1
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens

    @property
    def cost(self) -> float:
        return (self.input_tokens * PRICE_IN + self.output_tokens * PRICE_OUT) / 1_000_000


# 模型分层 + 限制
agent = Agent(
    name="WeatherBot",
    instructions="用 get_weather 查询，回答 50 字内",
    tools=[get_weather],
    model="gpt-6-luna",  # 便宜
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
    print(f"\n=== Performance ===")
    print(f"Duration: {dt:.2f}s")
    print(f"LLM calls: {tracker.llm_calls}")
    print(f"Input tokens: {tracker.input_tokens}")
    print(f"Output tokens: {tracker.output_tokens}")
    print(f"Estimated cost: ${tracker.cost:.4f}")
    print(f"\nOutput:\n{result.final_output}")


asyncio.run(main())
```

---

## 14. 下一步

- 错误处理 → [错误处理与重试](./03-error-handling.md)
- 安全 → [安全：Prompt Injection / 越狱防御 / 滥用监控](./04-security.md)
- 评测 → [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](./05-evals.md)
