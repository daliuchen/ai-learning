# Error Handling & Retry

> **一句话**：分清"我能恢复的错"和"用户能看到的错"——Tool 错给模型让它自己处理；guardrail 错你 catch；LLM API 错按错误码分类重试。

---

## 1. 错误分层

| 层 | 例子 | 处理 |
|----|------|------|
| **Tool 内部** | DB timeout / API 4xx | tool 内 try / 返回 error info 让 LLM 处理 |
| **LLM API 错** | RateLimitError / 5xx | 框架重试 / 你重试 |
| **Guardrail** | tripwire 触发 | catch → 给用户兜底回应 |
| **业务逻辑** | MaxTurns / 类型错 | catch → 上报 / 降级 |

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

LLM 看到 `{"error": "not_found"}` → 自己跟用户解释。

**别让 tool 抛异常**（除非 critical），返回结构化 error 让模型更智能处理。

---

## 3. LLM API 错误：openai 的内置重试

OpenAI SDK 默认有重试。可以调：

```python
from openai import AsyncOpenAI
from agents import set_default_openai_client


client = AsyncOpenAI(max_retries=3, timeout=30.0)
set_default_openai_client(client)
```

错误类型：

- `RateLimitError`：自动重试（指数退避）
- `APIConnectionError`：网络错，自动重试
- `APITimeoutError`：超时，自动重试
- `BadRequestError`：你的错（schema / 参数），不重试
- `AuthenticationError`：key 错，不重试

---

## 4. 自己重试 Runner.run

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

---

## 5. Fallback agent / model

```python
primary = Agent(name="P", model="gpt-4o")
fallback = Agent(name="F", model=LitellmModel("anthropic/claude-sonnet-4-6"))


async def safe_run(query: str):
    try:
        return await asyncio.wait_for(Runner.run(primary, query), timeout=10)
    except (asyncio.TimeoutError, APIError, AgentsException):
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
except MaxTurnsExceeded:
    return "抱歉，处理这个问题花了太长时间，请简化提问。"
```

通常意味着 agent 进死循环。看 trace 确定原因（tool 一直失败 / handoff 循环）。

---

## 8. Timeout 全局

```python
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
    return JSONResponse(500, {"error": "internal"})
```

或自己写 metric：

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

不要把 stack trace 给用户看。

---

## 11. Idempotency（重试安全）

Agent 调 tool 的副作用是危险的——`create_order(...)` 一次还是多次？

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

Agent 不知道自己是不是重试，但 tool 实现确保安全。

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

熔断保护：连续失败 5 次后自动短路 60 秒。

---

## 13. 完整 demo

```python
# demos/production/03_error_handling.py
import asyncio
from openai import APIError
from agents import Agent, Runner
from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    AgentsException,
)


primary = Agent(name="Primary", model="gpt-4o", instructions="...")
fallback = Agent(name="Fallback", model="gpt-4o-mini", instructions="...")


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


asyncio.run(safe_run("你好"))
```

---

## 14. 下一步

- 📖 安全 → [04-security.md](./04-security.md)
- 📖 评测错误 → [05-evals.md](./05-evals.md)
