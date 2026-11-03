# 错误处理与重试

> 处理错误之前，先分清哪些错是系统内部能恢复的，哪些会被用户看到。我的做法是：工具出错就把信息交给模型，让它自己应对；guardrail 触发由你的代码 catch；LLM API 出错按错误类型决定要不要重试。

---

## 1. 错误分层

| 层 | 例子 | 处理 |
|----|------|------|
| Tool 内部 | DB timeout / API 4xx | tool 内 try / 返回 error info 让 LLM 处理；`@tool(timeout=...)` |
| LLM API 错 | RateLimitError / 5xx / 超时 | `ModelSettings(timeout=..., retry=...)`（SDK 内置，opt-in）/ openai 客户端自带重试 / 你自己兜底 |
| Guardrail | tripwire 触发 | catch → 给用户兜底回应（守卫函数自己抛的异常不算 tripwire） |
| 业务逻辑 | MaxTurns / 模型拒答 / 输出校验失败 | `error_handlers=` 给兜底输出；或 catch → 上报 / 降级 |

---

## 2. Tool 错误：让 LLM 处理

```python
@function_tool
def get_order(order_id: str) -> str:
    try:
        order = db.get(order_id)
        return json.dumps(order)
    except OrderNotFound:
        return json.dumps({"error": "not_found", "msg": "订单不存在"})
    except DBConnectionError:
        return json.dumps({"error": "db_unavailable", "msg": "服务暂时不可用"})
```

模型看到 `{"error": "not_found"}`，会自己组织语言向用户解释。

所以除非是严重错误，工具里别直接抛异常，返回结构化的错误信息效果更好。原因是工具抛异常时，SDK 默认只给模型一句笼统的 `An error occurred while running the tool. Please try again.`，异常原文不会传给模型。想让模型看到具体原因，要写 `failure_error_function`；想让异常冒出去，设 `failure_error_function=None`，冒出来的是一个 `UserError` 包装。工具也可以单独设 `timeout=`，超时默认同样是给模型一条可读的提示（见 [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](../02-tools/04-tool-choice.md)）。

---

## 3. LLM API 错误：两层重试

第一层是 openai 客户端自带的重试，由 `max_retries` 控制，只对连接错误和 408/409/429/5xx 等做指数退避：

```python
from openai import AsyncOpenAI
from agents import set_default_openai_client

client = AsyncOpenAI(max_retries=3, timeout=30.0)
set_default_openai_client(client)
```

第二层是 0.12.0 起提供的 runner 级重试（`ModelSettings.retry`）。它需要手动开启，按你给的策略决定是否重试，还会检查重放是否安全：

```python
from agents import Agent, ModelRetrySettings, ModelSettings, retry_policies

agent = Agent(
    name="A",
    model="gpt-6.1-sol",
    model_settings=ModelSettings(
        timeout=30.0,                           # 单次模型调用超时（抛 ModelTimeoutError）
        retry=ModelRetrySettings(
            max_retries=4,
            backoff={"initial_delay": 0.5, "max_delay": 5.0, "multiplier": 2.0, "jitter": True},
            policy=retry_policies.any(
                retry_policies.provider_suggested(),
                retry_policies.retry_after(),
                retry_policies.network_error(),               # 含超时
                retry_policies.http_status([408, 409, 429, 500, 502, 503, 504]),
            ),
        ),
    ),
)
```

超时会抛 `ModelTimeoutError`。配了 `retry=ModelRetrySettings(max_retries=2, policy=...)` 且策略放行超时的话，最多再重试 2 次，用完仍然抛 `ModelTimeoutError`。

官方文档里关于这层重试的要点：

- 默认不重试。必须设 `retry=`，而且策略返回 True 才会重试。`retry` 在 RunConfig 和 Agent 的 `ModelSettings` 之间是深度合并的，Agent 可以只改 `max_retries`，沿用 runner 上的 policy。
- 内置策略有 `never() / provider_suggested() / network_error() / http_status([...]) / retry_after() / any(...) / all(...)`。`provider_suggested()` 最稳妥，因为它保留了 provider 的否决和「能否安全重放」的判断。
- 安全边界：abort 不重试；流式已经开始输出的不重试；provider 标记为不能安全重放的，默认不重试（要放行得在策略里返回 `RetryDecision(retry=True, approve_unsafe_replay=True)`，之前先看看 `context.replay_safety` 等信息）；带 `previous_response_id` / `conversation_id` 的有状态请求，在重放安全性未知时直接失败（fail closed）。
- 自定义策略时，策略函数会拿到 `RetryPolicyContext`：`attempt`、`max_retries`、`error`、`stream`、`replay_safety` 直接在上面，`status_code`、`retry_after`、`is_network_error`、`is_timeout` 这些归一化后的错误信息在 `context.normalized` 里。返回 bool 或 `RetryDecision`。

常见的错误类型和处理方式：

- `RateLimitError`：429，值得重试
- `APIConnectionError` / `APITimeoutError`：网络或超时，值得重试
- `BadRequestError`：你这边的问题（schema / 参数），不重试
- `AuthenticationError`：key 错了，不重试
- SDK 自己的：`ModelTimeoutError`（超过 `ModelSettings.timeout`）、`ModelBehaviorError`（非法 JSON、调了不存在的工具、Responses 返回 `failed`/`incomplete`，0.22 起非流式也会抛）、`ModelRefusalError`（模型拒答，0.15 起）

---

## 4. 自己重试 Runner.run

SDK 的 runner 级重试只管单次模型调用。如果要重试整个 run，就得自己权衡，比如 run 里有写操作的工具，重试会让副作用重复发生：

```python
import asyncio
from openai import APIError


async def run_with_retry(agent, query, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await Runner.run(agent, query)
        except APIError as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                raise
```

重试整个 `Runner.run` 会把之前已经成功的工具调用再执行一遍，所以工具必须幂等（见第 11 节）。

---

## 5. Fallback agent / model

```python
primary = Agent(name="P", model="gpt-6.1-sol", model_settings=ModelSettings(timeout=10.0))
fallback = Agent(name="F", model=AnyLLMModel(model="anthropic/claude-sonnet-5-5"))   # 或任意 OpenAIChatCompletionsModel；LiteLLM 的依赖冲突见 05-advanced/04


async def safe_run(query: str):
    try:
        return await Runner.run(primary, query)
    except (ModelTimeoutError, APIError):
        return await Runner.run(fallback, query)
```

---

## 6. 部分降级

```python
async def chat(query: str):
    try:
        # 走完整 agent（tools + handoffs）
        return await Runner.run(full_agent, query)
    except Exception as e:
        log.warn("full agent failed", e=e)
        # 降级到简单 agent（无 tools）
        return await Runner.run(simple_agent, query)
```

---

## 7. MaxTurnsExceeded 处理

```python
try:
    result = await Runner.run(agent, query, max_turns=10)
except MaxTurnsExceeded as e:
    log.warn("max_turns", tools=[i.type for i in e.run_data.new_items])      # run_data 里有出错前的 new_items / usage
    return "抱歉，处理这个问题花了太长时间，请简化提问。"
```

更省事的写法是 `Runner.run(..., error_handlers={"max_turns": handler})`。handler 返回字符串或 `RunErrorHandlerResult(final_output=..., include_in_history=False)`，run 就会正常返回。同样的机制还支持 `"model_refusal"` 和 `"invalid_final_output"`，后者在结构化输出校验失败时给一个兜底对象，不会重新调模型，也不会重放工具的副作用。

撞到 max_turns 通常说明 agent 陷进了循环，去 trace 里看是工具一直失败，还是 handoff 来回绕。

---

## 8. Timeout 全局

```python
# 模型调用级超时用 ModelSettings(timeout=...)，工具级用 @tool(timeout=...)；整个 run 的总时长才需要外层 wait_for
async def with_timeout(coro, seconds=30):
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        return None


result = await with_timeout(Runner.run(agent, query), 30)
if result is None:
    return {"error": "timeout"}
```

---

## 9. 错误监控 / 报警

```python
import sentry_sdk


sentry_sdk.init(dsn="https://...")


@app.exception_handler(AgentsException)
async def agent_error(req, exc):
    sentry_sdk.capture_exception(exc)
    return JSONResponse(status_code=500, content={"error": "internal"})
```

也可以自己打 metric：

```python
from prometheus_client import Counter

errors = Counter("agent_errors", "Agent errors", ["type", "agent"])


try:
    await Runner.run(agent, query)
except MaxTurnsExceeded:
    errors.labels(type="max_turns", agent=agent.name).inc()
    raise
except APIError as e:
    errors.labels(type="api_error", agent=agent.name).inc()
    raise
```

---

## 10. 用户感知的错误消息

```python
@app.post("/chat")
async def chat(req: ChatReq):
    try:
        result = await Runner.run(agent, req.message)
        return {"reply": result.final_output}
    except InputGuardrailTripwireTriggered:
        return {"reply": "抱歉，这个请求我无法处理。"}
    except MaxTurnsExceeded:
        return {"reply": "抱歉，处理超时，请简化提问。"}
    except APIError:
        return {"reply": "服务暂时繁忙，请稍后再试。"}
    except Exception:
        log.exception("unknown error")
        return {"reply": "抱歉，出了点小问题。请联系客服。"}
```

stack trace 别直接给用户看。

---

## 11. Idempotency（重试安全）

工具的副作用是重试时最容易出事的地方：`create_order(...)` 到底执行了一次还是两次？

```python
@function_tool
async def create_order(idempotency_key: str, items: list[str]) -> str:
    """创建订单。idempotency_key 用于去重。"""
    existing = db.get_order_by_key(idempotency_key)
    if existing:
        return f"Order already exists: {existing.id}"

    new_order = db.create(idempotency_key=idempotency_key, items=items)
    return f"Order {new_order.id} created"
```

Agent 自己不知道这次是不是重试，安全要靠工具实现来保证。

---

## 12. Circuit breaker

```python
from circuitbreaker import circuit


@circuit(failure_threshold=5, recovery_timeout=60)
def call_flaky_api():
    return httpx.get("https://flaky-service").json()


@function_tool
def query_external(q: str) -> str:
    try:
        return call_flaky_api()
    except CircuitBreakerError:
        return "外部服务不可用，请稍后再试"
```

连续失败 5 次后熔断，60 秒内的调用直接短路。

---

## 13. 完整 demo

```python
import asyncio
from openai import APIError
from agents import Agent, Runner
from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    AgentsException,
)


primary = Agent(name="Primary", model="gpt-6.1-sol", instructions="...")
fallback = Agent(name="Fallback", model="gpt-6-luna", instructions="...")
# 没有 OpenAI key 时：两个 Agent 都改成 model=MODEL（OpenAIChatCompletionsModel）


async def safe_run(query: str) -> dict:
    """完整错误处理示例"""
    for attempt in range(3):
        try:
            result = await asyncio.wait_for(
                Runner.run(primary, query, max_turns=8),
                timeout=20,
            )
            return {"ok": True, "reply": result.final_output, "via": "primary"}

        except (asyncio.TimeoutError, APIError) as e:
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
                continue
            # 最后一次尝试用 fallback
            try:
                result = await Runner.run(fallback, query, max_turns=5)
                return {"ok": True, "reply": result.final_output, "via": "fallback"}
            except Exception:
                return {"ok": False, "reply": "服务暂时不可用"}

        except InputGuardrailTripwireTriggered:
            return {"ok": False, "reply": "请求被拒绝"}

        except MaxTurnsExceeded:
            return {"ok": False, "reply": "处理超时"}

        except AgentsException as e:
            log.exception("agents error")
            return {"ok": False, "reply": "内部错误"}


print(asyncio.run(safe_run("你好")))
```

---

## 14. 下一步

- 安全 → [安全：Prompt Injection / 越狱防御 / 滥用监控](./04-security.md)
- 用评测发现错误 → [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](./05-evals.md)
