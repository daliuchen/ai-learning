# Tripwire 与异常处理

> **一句话**：`tripwire_triggered=True` 让 SDK 抛 `InputGuardrailTripwireTriggered` / `OutputGuardrailTripwireTriggered`——你的业务代码 catch 后决定怎么回用户。

---

## 1. Tripwire 语义

> Tripwire = 触发线/绊网线

guardrail 函数返回 `GuardrailFunctionOutput(tripwire_triggered=True)` 就**绊住整个 run**：

- Input 阶段：Agent 还没跑就抛
- Output 阶段：Agent 跑完了，但输出不送达

两种异常都来自 `agents.exceptions` 模块。

---

## 2. 完整异常体系

```python
from agents.exceptions import (
    AgentsException,                       # 所有 SDK 异常基类
    InputGuardrailTripwireTriggered,       # 输入守卫触发
    OutputGuardrailTripwireTriggered,      # 输出守卫触发
    MaxTurnsExceeded,                      # 超过 max_turns
    UserError,                             # 用户配置错误（罕见）
    ModelBehaviorError,                    # 模型行为不符预期
)
```

---

## 3. 捕获模式

### 模式 A：分别处理

```python
try:
    result = await Runner.run(agent, user_input, context=ctx)
    return {"ok": True, "reply": result.final_output}

except InputGuardrailTripwireTriggered as e:
    return {"ok": False, "code": "input_blocked", "info": e.guardrail_result.output.output_info}

except OutputGuardrailTripwireTriggered as e:
    return {"ok": False, "code": "output_blocked", "info": e.guardrail_result.output.output_info}

except MaxTurnsExceeded:
    return {"ok": False, "code": "timeout"}

except AgentsException as e:
    return {"ok": False, "code": "internal_error", "detail": str(e)}
```

### 模式 B：统一兜底

```python
try:
    result = await Runner.run(agent, user_input)
    return result.final_output

except AgentsException as e:
    log.error("Agent failed", error=e)
    return "抱歉，请稍后再试。"
```

---

## 4. e.guardrail_result 的细节

```python
except InputGuardrailTripwireTriggered as e:
    guardrail_result = e.guardrail_result

    guardrail_result.guardrail.name   # 触发的 guardrail 函数名
    guardrail_result.output.tripwire_triggered  # True
    guardrail_result.output.output_info         # 你 return 的 dict
```

可以记录到日志：

```python
log.warn(
    "guardrail tripped",
    guardrail=guardrail_result.guardrail.name,
    info=guardrail_result.output.output_info,
    user_id=current_user.id,
)
```

---

## 5. 给用户的回应：怎么不让人感到"墙"

不要直接：

```
"您的请求被拦截了，错误码 PII_001"
```

更好：

```python
except InputGuardrailTripwireTriggered as e:
    info = e.guardrail_result.output.output_info
    reason = info.get("reason", "")

    if "pii" in str(info).lower():
        return "为了您的安全，请不要在对话中输入身份证、银行卡等敏感信息。"
    elif "off-topic" in str(info).lower():
        return "这个问题不在我能帮的范围。您可以问我 X、Y、Z 相关。"
    else:
        return "抱歉，这条消息我无法处理。"
```

按 reason 给针对性引导。

---

## 6. 部分 guardrail 报警，不阻断

guardrails 是"拦或不拦"二元的。要"警告但不拦"，**不要**在 guardrail 里 trip——改成发出 metric / 日志：

```python
@input_guardrail
async def soft_check(ctx, agent, user_input):
    if "马蜂窝" in user_input:
        # 记录但不拦
        await metrics.increment("competitor.mentioned")
    return GuardrailFunctionOutput(tripwire_triggered=False)
```

或者更优雅：用 Lifecycle Hook 做日志（详见 [05-advanced/03-lifecycle-hooks.md](../05-advanced/03-lifecycle-hooks.md)）。

---

## 7. Guardrail 内部错误处理

```python
@input_guardrail
async def llm_check(ctx, agent, user_input):
    try:
        result = await Runner.run(check_agent, user_input)
        return GuardrailFunctionOutput(
            tripwire_triggered=result.final_output.is_bad,
        )
    except Exception as e:
        # 守卫挂了 → 别误伤
        log.error("guardrail check failed", error=e)
        return GuardrailFunctionOutput(tripwire_triggered=False)
```

**fail-open**（守卫挂了放行）适合大部分业务场景；金融、医疗等高风险用 **fail-close**（守卫挂了拒绝）。

---

## 8. 把多个 tripwire 信息汇总

```python
@input_guardrail
async def combined_check(ctx, agent, user_input):
    issues = []

    if has_pii(user_input):
        issues.append("pii")
    if is_too_long(user_input):
        issues.append("too_long")
    if has_injection(user_input):
        issues.append("injection")

    return GuardrailFunctionOutput(
        tripwire_triggered=bool(issues),
        output_info={"issues": issues},
    )
```

但**通常分开写**更清晰、便于评测：

```python
input_guardrails=[pii_check, length_check, injection_check]
```

每个独立、可单独评测。

---

## 9. MaxTurnsExceeded 配合 guardrails

```python
try:
    result = await Runner.run(agent, user_input, max_turns=10)
    return result.final_output

except MaxTurnsExceeded:
    # Agent 跑太多轮还没出结果
    log.warn("max_turns exceeded", user_input=user_input)
    return "抱歉，处理这个问题超时了，请简化提问。"
```

MaxTurns 通常意味着：

- Tool 一直失败 → 模型一直重试
- 流程死循环（handoff 来回）
- 任务太复杂

不要无脑调大 max_turns——先 trace 看为啥。

---

## 10. Tripwire vs 业务异常

```python
@function_tool
def get_user_balance(user_id: str) -> str:
    if user_id == "banned":
        raise PermissionError("用户被封")
    return "100 元"
```

PermissionError → SDK 把错误信息当 tool result 给模型 → 模型可能道歉给用户。

**这跟 guardrail 不同**：

- Guardrail tripwire = SDK 抛出去你 catch
- Tool 异常 = SDK 给模型让模型处理

按业务选：

- 用户级"不让做"（注册 / 付费）→ tool 抛异常让模型解释
- 系统级"不允许"（PII / injection）→ guardrail 直接拦

---

## 11. 完整 demo：业务级 try/except

```python
# demos/guardrails/03_tripwire.py
import asyncio
from agents import Agent, Runner, input_guardrail, GuardrailFunctionOutput
from agents.exceptions import (
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    MaxTurnsExceeded,
    AgentsException,
)


@input_guardrail
async def pii_block(ctx, agent, user_input: str):
    import re
    has_pii = bool(re.search(r"\d{17}[\dXx]", user_input))
    return GuardrailFunctionOutput(
        tripwire_triggered=has_pii,
        output_info={"category": "pii"},
    )


agent = Agent(
    name="A",
    instructions="...",
    input_guardrails=[pii_block],
    model="gpt-4o-mini",
)


async def safe_chat(user_input: str) -> dict:
    try:
        result = await Runner.run(agent, user_input, max_turns=5)
        return {"ok": True, "reply": result.final_output}

    except InputGuardrailTripwireTriggered as e:
        category = e.guardrail_result.output.output_info.get("category")
        return {
            "ok": False,
            "code": "input_blocked",
            "category": category,
            "reply": "为了您的安全，请不要输入敏感信息。",
        }

    except OutputGuardrailTripwireTriggered as e:
        return {
            "ok": False,
            "code": "output_blocked",
            "reply": "抱歉，无法给出合适回答。",
        }

    except MaxTurnsExceeded:
        return {
            "ok": False,
            "code": "timeout",
            "reply": "处理超时，请简化提问。",
        }

    except AgentsException as e:
        return {
            "ok": False,
            "code": "internal_error",
            "reply": "抱歉，服务异常。",
        }


async def main():
    cases = ["你好", "我的身份证 110101199001011234"]
    for q in cases:
        result = await safe_chat(q)
        print(f"\nQ: {q}")
        print(f"R: {result}")


asyncio.run(main())
```

---

## 12. 下一步

- 📖 Tracing：guardrail 触发的 trace → [05-advanced/01-tracing.md](../05-advanced/01-tracing.md)
- 📖 安全实战 → [07-production/04-security.md](../07-production/04-security.md)
- 📖 评测 guardrail 准确率 → [07-production/05-evals.md](../07-production/05-evals.md)
