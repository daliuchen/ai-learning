# Tripwire 与异常处理

> 守卫返回 `tripwire_triggered=True` 时，SDK 会抛出 `InputGuardrailTripwireTriggered` 或 `OutputGuardrailTripwireTriggered`（工具守卫对应 `ToolInput/OutputGuardrailTripwireTriggered`），由业务代码捕获后决定怎么回复用户。这篇讲异常体系、捕获方式、守卫自身出错的处理，以及工具级守卫。

---

## 1. Tripwire 的含义

Tripwire 原意是绊线。guardrail 函数返回 `GuardrailFunctionOutput(output_info=None, tripwire_triggered=True)`，整个 run 就被绊住了。不同阶段的效果不一样：

- Input 阶段：默认与 Agent 并行，Agent 可能已经起步；阻塞模式（`run_in_parallel=False`）下 Agent 完全没跑
- Output 阶段：Agent 已经跑完，但输出不会送到用户那里
- Tool 阶段：每次工具调用前（input）和后（output）检查，见第 12 节

三个阶段对应的异常（input、output，以及工具级的 input / output）都在 `agents.exceptions` 模块里。

---

## 2. 完整异常体系

```python
from agents.exceptions import (
    AgentsException,                       # 所有 SDK 异常基类
    InputGuardrailTripwireTriggered,       # 输入守卫触发
    OutputGuardrailTripwireTriggered,      # 输出守卫触发
    MaxTurnsExceeded,                      # 超过 max_turns
    UserError,                             # 用户配置错误（罕见）
    ModelBehaviorError,                    # 模型行为不符预期（非法 JSON、调了不存在的工具…）
    ModelRefusalError,                     # 模型拒答（0.15 起）
    ModelTimeoutError,                     # 单次模型调用超时
    ToolTimeoutError,                      # 工具超时且 timeout_behavior="raise_exception"
)
from agents import (                       # 工具级守卫的 tripwire
    ToolInputGuardrailTripwireTriggered,
    ToolOutputGuardrailTripwireTriggered,
)
```

---

## 3. 捕获方式

### 方式 A：分别处理

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

### 方式 B：统一兜底

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

    guardrail_result.guardrail.get_name()       # 触发的 guardrail 函数名
    guardrail_result.output.tripwire_triggered  # True
    guardrail_result.output.output_info         # 你 return 的任意对象（dict / 模型实例…）
    e.run_data.input_guardrail_results          # 这次 run 里已完成的全部输入守卫结果（含触发的那个）
```

output 侧同理，看 `e.run_data.output_guardrail_results`。工具守卫的异常直接带 `e.guardrail` 和 `e.output`（触发的那条结果），`e.run_data.tool_input_guardrail_results` 里是此前已累积的结果。`MaxTurnsExceeded` 等 runner 异常同样带 `run_data`。`run_data` 在极少数没有上下文的异常里可能是 `None`，用之前先判空。

可以记到日志里：

```python
log.warn(
    "guardrail tripped",
    guardrail=guardrail_result.guardrail.name,
    info=guardrail_result.output.output_info,
    user_id=current_user.id,
)
```

---

## 5. 怎么回复用户，才不像撞墙

别直接这样回：

```
"您的请求被拦截了，错误码 PII_001"
```

这样更好：

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

根据拦截原因给出有针对性的引导。

---

## 6. 只告警，不阻断

guardrail 只有拦和不拦两种结果。想要“提醒一下但不拦”，就别在 guardrail 里触发 tripwire，改成发 metric 或写日志：

```python
@input_guardrail
async def soft_check(ctx, agent, user_input):
    if "马蜂窝" in user_input:
        # 记录但不拦
        await metrics.increment("competitor.mentioned")
    return GuardrailFunctionOutput(output_info=None, tripwire_triggered=False)
```

也可以用 Lifecycle Hook 来记日志，写起来更干净（见 [Lifecycle Hooks：生命周期钩子](../05-advanced/03-lifecycle-hooks.md)）。

---

## 7. 守卫内部出错

先纠正一个常见误解：守卫函数里抛出的异常不会被当作 tripwire。在 0.24 里，`raise RuntimeError("guard api down")` 会原样从 `Runner.run` 冒出来，整个 run 失败。所以调用外部服务的守卫要自己 try/except，并明确选择 fail-open 还是 fail-close：

```python
@input_guardrail
async def llm_check(ctx, agent, user_input):
    try:
        result = await Runner.run(check_agent, user_input)
        return GuardrailFunctionOutput(
            output_info=result.final_output,
            tripwire_triggered=result.final_output.is_bad,
        )
    except Exception as e:
        # 守卫挂了 → 别误伤
        log.error("guardrail check failed", error=e)
        return GuardrailFunctionOutput(output_info={"error": str(e)}, tripwire_triggered=False)
```

大部分业务场景适合 fail-open，守卫挂了就放行；金融、医疗这类高风险场景用 fail-close，返回 `tripwire_triggered=True`，或者直接让异常冒出去，走你自己的兜底。

---

## 8. 把多个检查合并到一个守卫

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

不过我一般还是分开写，更清楚，也方便评测：

```python
input_guardrails=[pii_check, length_check, injection_check]
```

每个守卫独立，可以单独评测。

---

## 9. MaxTurnsExceeded 和 guardrails

```python
try:
    result = await Runner.run(agent, user_input, max_turns=10)
    return result.final_output

except MaxTurnsExceeded:
    # Agent 跑太多轮还没出结果
    log.warn("max_turns exceeded", user_input=user_input)
    return "抱歉，处理这个问题超时了，请简化提问。"
```

撞到 MaxTurns，通常是工具一直失败导致模型反复重试、handoff 来回转形成死循环，或者任务本身太复杂。别一上来就调大 max_turns，先看 trace 找原因。

---

## 10. Tripwire 和业务异常的区别

```python
@function_tool
def get_user_balance(user_id: str) -> str:
    if user_id == "banned":
        raise PermissionError("用户被封")
    return "100 元"
```

这里抛的 PermissionError 会被 SDK 当成 tool result 交给模型，模型多半会向用户道歉。这和 guardrail 不一样：guardrail 触发 tripwire 时，SDK 把异常抛给你来捕获；工具异常则交给模型处理。

怎么选看业务。用户层面的“不让做”（比如要先注册、要付费）适合在工具里抛异常，让模型去解释；系统层面的“不允许”（PII、injection）用 guardrail 直接拦。

---

## 11. 完整 demo：业务级 try/except

```python
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
    model="gpt-6-luna",
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

## 12. 工具级守卫（Tool Guardrails）

Agent 级 guardrails 只管工作流的边界：input 只管第一个 agent，output 只管产出最终回答的那个 agent。中间每次函数工具调用都要检查的话（比如退款金额、参数里有没有密钥、返回里有没有手机号），就把守卫挂在工具上：

```python
import json
from agents import ToolGuardrailFunctionOutput, function_tool
from agents.decorators import tool_input_guardrail, tool_output_guardrail


@tool_input_guardrail
def block_big_refund(data):                       # 工具执行前；data.context.tool_arguments 是 JSON 字符串
    args = json.loads(data.context.tool_arguments or "{}")
    if args.get("amount", 0) > 1000:
        return ToolGuardrailFunctionOutput.raise_exception({"reason": "amount>1000"})   # → 抛 ToolInputGuardrailTripwireTriggered
    return ToolGuardrailFunctionOutput.allow()


@tool_output_guardrail
def mask_phone(data):                             # 工具执行后；data.output 是工具返回值
    if "13800000000" in str(data.output):
        return ToolGuardrailFunctionOutput.reject_content("输出含手机号，已隐藏")       # 用这句话替换给模型的结果
    return ToolGuardrailFunctionOutput.allow()


@function_tool(tool_input_guardrails=[block_big_refund])
def refund(order_id: str, amount: float) -> str: ...

@function_tool(tool_output_guardrails=[mask_phone])
def lookup_user(uid: str) -> str: ...
```

守卫有三种返回。`allow()` 放行；`reject_content("话术")` 不执行或不返回真实内容，把话术当作工具结果交给模型，放在 input 侧就等于跳过这次调用；`raise_exception(info)` 触发 tripwire，终止 run。

按上面的写法，退款 5000 元会抛 `ToolInputGuardrailTripwireTriggered`，`e.output.output_info` 就是传给 `raise_exception` 的 `{'reason': 'amount>1000'}`；退款 50 元正常放行。`reject_content` 要注意一点：模型只看到“已隐藏”这句话，可能会自己编出邮箱、地址之类的细节回给用户。所以敏感数据不能只靠告诉模型“已隐藏”，要在工具返回之前就脱敏。

还有几个细节：

- 工具守卫作用于 `FunctionTool` 管线。本地 MCP server 也可以配 `tool_input_guardrails` / `tool_output_guardrails`，会挂到它暴露的每个工具上。
- 它不作用于 handoff 调用本身、hosted tools（`WebSearchTool` 等）和内置执行工具（`ComputerTool`、`ShellTool`、`ApplyPatchTool`）。
- 工具需要审批（`needs_approval`）时，input 守卫默认在批准之后、执行之前运行。想让它在发出审批请求之前也跑一遍，设 `RunConfig(tool_execution=ToolExecutionConfig(pre_approval_tool_input_guardrails=True))`，批准后还会再跑一次。

---

## 13. 下一步

- 在 trace 里看 guardrail 触发 → [内置 Tracing 与 OpenAI Platform Dashboard](../05-advanced/01-tracing.md)
- 安全实战 → [安全：Prompt Injection / 越狱防御 / 滥用监控](../07-production/04-security.md)
- 评测 guardrail 准确率 → [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](../07-production/05-evals.md)
