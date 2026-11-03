# Output Guardrails：输出守卫

> Output guardrail 在 Agent 给出最终输出之后做检查，发现敏感数据、格式错误或违反政策的内容，就触发 tripwire 拦下来，不让用户看到。这篇讲它和 input guardrail 的区别、拦下后 session 里会留下什么，以及几个常用的守卫写法。

---

## 1. 跟 Input Guardrails 的区别

| | Input | Output |
|---|---|---|
| 何时运行 | 默认与 Agent 并行（`run_in_parallel=False` 则先于 Agent） | Agent 产出最终输出之后（没有 `run_in_parallel` 参数） |
| 看到什么 | 用户输入（只在链上第一个 agent 运行） | Agent 最终输出（只在产出最终回答的那个 agent 运行） |
| tripwire 效果 | 终止 run | 阻止输出送给用户 |
| 触发后 | 阻塞模式下 Agent 没跑；并行模式下可能已花了 token | Agent 已经跑完（token 花了，工具也执行过了）|

两者通常一起用：input 拦明显的违规，output 拦泄露和幻觉。

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

Agent 没设 `output_type` 时，`agent_output` 是字符串；设了 `output_type=SomeModel`，它就是 SomeModel 的实例。

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

confidence 低的回答直接拦下，不给用户看。

---

## 4. 用 LLM 做评判的 output guardrail

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
    model="gpt-6-luna",
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

## 5. 捕获 OutputGuardrailTripwireTriggered

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

output guardrail 拦下时，Agent 已经跑完，token 已经花掉，**工具的副作用也已经发生**。能在 input 阶段拦的，尽量在 input 阶段拦。

拦下之后 session 里留下什么，在 0.24 里按下面的数据隔离规则处理，这会实际影响持久化的历史：

- tripwire 只拒绝候选的最终输出。已经完成的 tool call、tool output（以及重放需要的 reasoning）仍会写进 session，被拒的那条最终回答不写。流式和非流式行为一致。
- 如果被拒的输出直接来自终端函数工具（`tool_use_behavior` 让工具结果直接作为最终输出），0.22 起 SDK 会把那条 `function_call_output` 替换成固定文本 `Output withheld by an output guardrail.`，避免敏感内容留在 session、`RunState` 或流式结果里。想换文案，用 `RunConfig(output_guardrail_blocked_message="该内容已被策略拦截")`；也可以传一个同步函数，它拿到的参数里不包含被拒的输出内容。
- 守卫函数自己抛异常的话，不算 tripwire，异常会原样抛出（见 [Input Guardrails：输入守卫](./01-input-guardrails.md) 第 9 节）。

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

它们并发执行，任意一个触发 tripwire 就拦下。

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

防止 Agent 向用户许下公司兑现不了的承诺。

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

## 9. 实战：长度和格式检查

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
    model="gpt-6-luna",
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

## 11. 性能和成本

每次 Agent 跑完都要执行 output guardrails，基于 LLM 的守卫会持续消耗 token。可以这样优化：

1. 能用关键字或正则拦的，就不用 LLM
2. 评判用的 LLM 选小模型，不必用 gpt-6.1-sol
3. 抽样：1% 流量过 LLM 评判，其余走正则
4. 缓存：相同输出判过一次就把结果缓存起来（适合输出稳定的场景）

---

## 12. 跟 Pydantic AI 的对应

| OpenAI Agents | Pydantic AI |
|---|---|
| `@input_guardrail` | 没有同名概念，用 capability / hooks 自己拦（见 Pydantic AI 手册） |
| `@output_guardrail` | `@agent.output_validator` |
| Tripwire | 抛 `ModelRetry`（让模型重试，而不是终止 run） |

相比之下，OpenAI 把 guardrail 做成了独立概念，结构更清楚，也方便单独评测。

---

## 13. 下一步

- Tripwire 的更多用法 → [Tripwire 与异常处理](./03-tripwire.md)
- 生产环境里的安全防护 → [安全：Prompt Injection / 越狱防御 / 滥用监控](../07-production/04-security.md)
- 评测 guardrails 的准确率 → [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](../07-production/05-evals.md)
