# Output Guardrails：输出守卫

> **一句话**：在 Agent 给最终输出**之后**校验——发现含敏感数据 / 错误格式 / 政策违规 → tripwire 拦下，不让用户看到。

---

## 1. 跟 Input Guardrails 区别

| | Input | Output |
|---|---|---|
| 跑在何时 | LLM 调用前 | LLM 生成最终输出后 |
| 看到啥 | 用户输入 | Agent 最终输出 |
| tripwire 效果 | 阻止 LLM 调用 | 阻止输出送给用户 |
| 触发后 | Agent 没跑 | Agent 跑完了（费 token）|

通常**同时用**：input 拦明显违规 → output 拦泄露 / 幻觉。

---

## 2. 最简示例

```python
from agents import Agent, Runner, output_guardrail, GuardrailFunctionOutput


@output_guardrail
async def no_internal_url(ctx, agent, agent_output) -> GuardrailFunctionOutput:
    """禁止输出内部 URL"""
    text = str(agent_output)
    triggered = "internal.company.com" in text
    return GuardrailFunctionOutput(
        tripwire_triggered=triggered,
        output_info={"reason": "internal URL leaked"} if triggered else {},
    )


agent = Agent(
    name="A",
    instructions="...",
    output_guardrails=[no_internal_url],
)
```

---

## 3. agent_output 的类型

- Agent 没设 `output_type` → `agent_output` 是字符串
- 设了 `output_type=SomeModel` → `agent_output` 是 SomeModel 实例

```python
class Reply(BaseModel):
    text: str
    confidence: float


@output_guardrail
async def confidence_check(ctx, agent, agent_output: Reply):
    return GuardrailFunctionOutput(
        tripwire_triggered=agent_output.confidence < 0.5,
        output_info={"confidence": agent_output.confidence},
    )


agent = Agent(
    name="A",
    output_type=Reply,
    output_guardrails=[confidence_check],
)
```

confidence 低 → 拦下不给用户看不靠谱回答。

---

## 4. LLM-as-judge output guardrail

```python
from pydantic import BaseModel


class JudgeOutput(BaseModel):
    is_safe: bool
    reason: str


judge = Agent(
    name="Judge",
    instructions="""判断 Agent 的输出是否：
- 含 PII（手机 / 邮箱 / 身份证）
- 含品牌负面内容（"该公司很烂"）
- 含虚假承诺（"100% 退款"）

输出 is_safe = False 触发拦截。
""",
    output_type=JudgeOutput,
    model="gpt-4o-mini",
)


@output_guardrail
async def llm_judge(ctx, agent, agent_output):
    text = str(agent_output)
    result = await Runner.run(judge, f"待审输出: {text}")
    judge_output: JudgeOutput = result.final_output
    return GuardrailFunctionOutput(
        tripwire_triggered=not judge_output.is_safe,
        output_info={"reason": judge_output.reason},
    )


main_agent = Agent(
    name="Main",
    instructions="...",
    output_guardrails=[llm_judge],
)
```

---

## 5. 接住 OutputGuardrailTripwireTriggered

```python
from agents.exceptions import OutputGuardrailTripwireTriggered


try:
    result = await Runner.run(main_agent, "...")
    return {"reply": result.final_output}
except OutputGuardrailTripwireTriggered as e:
    # 已经费 token 跑完了，给用户兜底
    info = e.guardrail_result.output.output_info
    return {
        "reply": "抱歉，我暂时无法回答这个问题。请联系人工。",
        "blocked": True,
        "reason": info.get("reason"),
    }
```

**重要**：output guardrail 拦下时 Agent 已经跑完，token 烧了。能在 input 阶段拦的尽量在 input 拦。

---

## 6. 多个 output guardrails

```python
agent = Agent(
    name="A",
    output_guardrails=[
        no_pii,
        no_brand_negative,
        confidence_check,
        llm_judge,
    ],
)
```

并发跑，**任意一个** tripwire 就拦。

---

## 7. 实战：防虚假承诺

```python
@output_guardrail
async def no_false_promise(ctx, agent, agent_output):
    text = str(agent_output)
    patterns = [
        "100% 退款",
        "保证一定",
        "永远免费",
    ]
    detected = [p for p in patterns if p in text]
    return GuardrailFunctionOutput(
        tripwire_triggered=bool(detected),
        output_info={"detected": detected},
    )
```

防止 Agent 给用户做出公司无法兑现的承诺。

---

## 8. 实战：防字段缺失

```python
class CustomerReply(BaseModel):
    answer: str
    next_action: str   # "wait_user" / "escalate" / "close"
    sentiment: str


@output_guardrail
async def required_fields(ctx, agent, output: CustomerReply):
    missing = []
    if not output.next_action:
        missing.append("next_action")
    if not output.sentiment:
        missing.append("sentiment")
    return GuardrailFunctionOutput(
        tripwire_triggered=bool(missing),
        output_info={"missing": missing},
    )


agent = Agent(
    name="CustomerService",
    output_type=CustomerReply,
    output_guardrails=[required_fields],
)
```

---

## 9. 实战：长度 / 格式守卫

```python
@output_guardrail
async def length_check(ctx, agent, output):
    text = str(output)
    too_long = len(text) > 2000
    too_short = len(text) < 10
    return GuardrailFunctionOutput(
        tripwire_triggered=too_long or too_short,
        output_info={"len": len(text)},
    )
```

---

## 10. 完整 demo

```python
# demos/guardrails/02_output_guardrails.py
import asyncio
import re
from pydantic import BaseModel
from agents import Agent, Runner, output_guardrail, GuardrailFunctionOutput
from agents.exceptions import OutputGuardrailTripwireTriggered


class Reply(BaseModel):
    text: str
    sources: list[str]


@output_guardrail
async def no_pii(ctx, agent, output: Reply):
    """检查 text 中是否含手机号"""
    phone = re.search(r"1[3-9]\d{9}", output.text)
    return GuardrailFunctionOutput(
        tripwire_triggered=bool(phone),
        output_info={"match": phone.group() if phone else None},
    )


@output_guardrail
async def sources_required(ctx, agent, output: Reply):
    """必须有至少 1 个 source"""
    return GuardrailFunctionOutput(
        tripwire_triggered=len(output.sources) == 0,
        output_info={"source_count": len(output.sources)},
    )


agent = Agent(
    name="Researcher",
    instructions="""回答时必须给至少 1 个引用源（sources 字段，URL 形式）。
不要在 text 里包含真实手机号。""",
    output_type=Reply,
    output_guardrails=[no_pii, sources_required],
    model="gpt-4o-mini",
)


async def main():
    try:
        result = await Runner.run(agent, "什么是 Python")
        print("✅", result.final_output)
    except OutputGuardrailTripwireTriggered as e:
        info = e.guardrail_result.output.output_info
        print(f"❌ BLOCKED: {info}")


asyncio.run(main())
```

---

## 11. 性能 / 成本提醒

每次 Agent 跑完都跑 output guardrails——LLM-based 的 guardrails 烧 token。优化方式：

1. **关键字 / 正则优先**：能 regex 拦的不用 LLM
2. **LLM judge 用 mini**：不用 gpt-4o
3. **采样**：1% 流量过 LLM judge，其它走 regex
4. **缓存**：相同输出已判过的结果缓存（适合稳定输出）

---

## 12. 跟 Pydantic AI 的对应

| OpenAI Agents | Pydantic AI |
|---|---|
| `@input_guardrail` | `@agent.input_validator` 不存在，用 hooks |
| `@output_guardrail` | `@agent.output_validator` |
| Tripwire | 抛 `ModelRetry` |

OpenAI 的 guardrails 更**结构化**——独立概念，可单独评测。

---

## 13. 下一步

- 📖 Tripwire 的更多用法 → [03-tripwire.md](./03-tripwire.md)
- 📖 Agent + guardrails 实战部署 → [07-production/04-security.md](../07-production/04-security.md)
- 📖 评测：guardrails 准确率 → [07-production/05-evals.md](../07-production/05-evals.md)
