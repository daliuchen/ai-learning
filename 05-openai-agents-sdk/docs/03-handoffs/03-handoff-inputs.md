# Handoff Inputs & Filters：控制信息流

> 用 `handoff()` 函数包装之后，可以要求模型在转交前先填一个结构化对象（`input_type`），也可以筛掉不该传给子 Agent 的上下文（`input_filter`）。这篇讲这两个参数，以及 `on_handoff` 回调和工具名覆盖。

---

## 1. 默认 handoff 的问题

```python
triage = Agent(handoffs=[billing])
```

默认情况下（`nest_handoff_history` 关闭），转给 billing 时整段对话历史都会给它看，billing 自己读历史判断要做什么。这样有时会带上多余的信息，甚至敏感数据。需要更细的控制时，就要用 `handoff()` 函数。

---

## 2. input_type：要求模型先填表

```python
from pydantic import BaseModel
from agents import Agent, handoff, RunContextWrapper


class BillingHandoff(BaseModel):
    intent: str   # "refund" / "subscription" / "invoice"
    order_id: str | None = None
    reason: str


billing = Agent(name="Billing", instructions="...")


async def on_billing_handoff(ctx: RunContextWrapper, input_data: BillingHandoff):
    # handoff 触发时回调
    print(f"Handoff to Billing: intent={input_data.intent}, order={input_data.order_id}")


triage = Agent(
    name="Triage",
    instructions="转给 Billing 时填好 BillingHandoff",
    handoffs=[
        handoff(
            agent=billing,
            input_type=BillingHandoff,
            on_handoff=on_billing_handoff,
        ),
    ],
)
```

模型在转给 Billing 之前，会先调用 `transfer_to_billing(intent="refund", order_id="SO-1", reason="不满意")`，相当于把意图整理成结构化数据。

这有几个用处。路由信息是结构化的，业务系统好记录；模型如果不知道 order_id，会先去问用户，等于被逼着澄清；回调里还能顺手做写库、计费、报警这类副作用。

---

## 3. input_filter：过滤历史

默认 handoff 会把整段对话历史交给子 Agent。想剪掉一部分：

```python
from agents import handoff
from agents.extensions import handoff_filters      # 注意：不是 from agents import handoff_filters


triage = Agent(
    handoffs=[
        handoff(
            agent=billing,
            # 内置 filter：移除 tool 调用细节
            input_filter=handoff_filters.remove_all_tools,
        ),
    ],
)
```

`agents.extensions.handoff_filters` 里有内置的 filter，比如 `remove_all_tools`，它会去掉历史里所有结构化的 tool call 和 tool result 条目。注意，已经被抄进普通消息文本或嵌套历史摘要里的内容，它不会处理。其他需求就自己写函数。

也可以不给每个 `handoff()` 单独设，而是用 `RunConfig(handoff_input_filter=...)` 全局设一个；两者都设了的话，每个 handoff 自己的 filter 优先。另外，服务端托管的会话（`conversation_id` / `previous_response_id`）不支持 input filter。

---

## 4. 自定义 input_filter

```python
from agents import HandoffInputData


def my_filter(data: HandoffInputData) -> HandoffInputData:
    """只保留最后 5 条消息"""
    history = data.input_history
    if isinstance(history, tuple):          # input_history 可能是字符串或 tuple
        history = history[-5:]
    return data.clone(input_history=history)    # clone() 只改你指定的字段，其余原样保留


triage = Agent(
    handoffs=[
        handoff(agent=billing, input_filter=my_filter),
    ],
)
```

`HandoffInputData` 包含这些字段：

- `input_history`：本次 `Runner.run` 开始前的对话历史
- `pre_handoff_items`：转交前那一轮之前生成的 items
- `new_items`：本轮新生成的 items，含 handoff 调用和输出
- `input_items`（可选）：要转给下一个 Agent 的 items。设了它，模型就读它而不读 `new_items`，`new_items` 仍完整保留在 session 历史里
- `run_context`：当时的 `RunContextWrapper`

---

## 5. 把上下文压成摘要

```python
async def summary_filter(data: HandoffInputData) -> HandoffInputData:
    """把长历史摘要给子 Agent"""
    if len(data.input_history) < 5:
        return data

    # 简单做法：保留首尾 + 中间摘要（实际可调 summarizer agent）
    summary = f"[Summary of {len(data.input_history) - 4} earlier messages]"
    new_history = [
        {"role": "system", "content": summary},
        *data.input_history[-3:],
    ]
    return data.clone(input_history=tuple(new_history))
```

---

## 6. on_handoff：转交时触发回调

```python
async def on_billing_handoff(ctx, input_data):
    # 记录 / 上报 / 告警
    await metrics.increment("handoffs.billing")
    await audit_log({
        "from": "triage",
        "to": "billing",
        "intent": input_data.intent,
        "user_id": ctx.context.user_id,
    })


triage = Agent(
    handoffs=[
        handoff(
            agent=billing,
            input_type=BillingHandoff,
            on_handoff=on_billing_handoff,
        ),
    ],
)
```

常见用途是监控和可观测、敏感 handoff 触发告警，或者按 handoff 次数做业务计费。

---

## 7. 覆盖 tool_name / tool_description

```python
triage = Agent(
    handoffs=[
        handoff(
            agent=billing,
            tool_name_override="transfer_to_billing_team",
            tool_description_override="转账单团队（处理退款、订阅、发票）",
        ),
    ],
)
```

Agent 的名字是内部用的，给模型看的工具名想换个更友好的说法时，或者要做多语言描述时，可以这样覆盖。

---

## 8. 组合用法

```python
@dataclass
class AppCtx:
    user_id: str
    audit_log: object


class EscalationHandoff(BaseModel):
    severity: str   # "low" / "medium" / "high"
    summary: str


async def on_escalate(ctx, data: EscalationHandoff):
    await ctx.context.audit_log.write({
        "user": ctx.context.user_id,
        "severity": data.severity,
        "summary": data.summary,
    })
    if data.severity == "high":
        await notify_oncall(...)


def escalation_filter(data: HandoffInputData) -> HandoffInputData:
    # 只给最近 10 条 + 用户元数据
    history = data.input_history[-10:] if isinstance(data.input_history, tuple) else data.input_history
    return data.clone(input_history=history, pre_handoff_items=())   # 主 agent 的中间产物不给


manager = Agent(name="Manager", instructions="处理升级")


support = Agent(
    name="Support",
    instructions="""...
若问题严重 / 用户暴怒 / 需要权限提升，escalate 到 Manager。
""",
    handoffs=[
        handoff(
            agent=manager,
            input_type=EscalationHandoff,
            on_handoff=on_escalate,
            input_filter=escalation_filter,
            tool_name_override="escalate_to_manager",
            tool_description_override="升级问题到 Manager，需指明 severity 和 summary",
        ),
    ],
)
```

---

## 9. 完整 demo

```python
import asyncio
from pydantic import BaseModel
from agents import Agent, Runner, handoff, function_tool, RunContextWrapper
from agents.extensions import handoff_filters


@function_tool
def lookup_invoice(order_id: str) -> str:
    return f"Order {order_id}: $99 paid"


@function_tool
def issue_refund(order_id: str) -> str:
    return f"Refund issued for {order_id}"


billing = Agent(
    name="Billing",
    instructions="""你看到 BillingHandoff.intent 就知道要做什么。
- refund: 调 issue_refund
- subscription: 看 BillingHandoff.reason 回答
- invoice: 调 lookup_invoice
""",
    tools=[lookup_invoice, issue_refund],
)


class BillingHandoff(BaseModel):
    intent: str
    order_id: str | None = None
    reason: str


async def on_billing_handoff(ctx, data: BillingHandoff):
    print(f"\n[Audit] handoff to billing: {data.model_dump_json()}\n")


triage = Agent(
    name="Triage",
    instructions="""转给 Billing 前填好 BillingHandoff：
- intent: refund / subscription / invoice
- order_id: 如果用户提了
- reason: 一句话描述用户来意
""",
    handoffs=[
        handoff(
            agent=billing,
            input_type=BillingHandoff,
            on_handoff=on_billing_handoff,
            input_filter=handoff_filters.remove_all_tools,
        ),
    ],
)


async def main():
    result = await Runner.run(triage, "我对订单 SO-9911 不满意，要退款")
    print(result.final_output)


asyncio.run(main())
```

运行：

```
[Audit] handoff to billing: {"intent":"refund","order_id":"SO-9911","reason":"用户对订单 SO-9911 不满意，要求退款"}

Refund issued for SO-9911。已为您处理退款。
```

---

## 10. 常见坑

| 坑 | 解 |
|----|----|
| input_type 太复杂，模型填不全 | 精简字段，能给默认值的给默认值 |
| input_filter 过滤太多，子 Agent 缺上下文 | 至少保留最近 3 条 user message |
| on_handoff 抛异常 | 整个 run 会失败，所以别在 handler 里做重要的副作用。反过来，要拒绝转交，就在 on_handoff 开头校验并 raise（`is_enabled` 在模型填参数之前评估，判断不了参数里的值） |
| 以为 `input_type` 会决定转给谁 | 它只给已经选定的那个 handoff 附加元数据，不负责分派；多个专家就注册多个 handoff。想给专家传结构化任务又不转交对话，用 `Agent.as_tool(parameters=...)` |
| 多个 handoffs 共用一个 input_type | 可以，但要在每个 Agent 的 instructions 里写清什么时候调 |

---

## 11. 下一步

- [复杂多 Agent 协作](./04-complex-multi-agent.md)
- 守卫体系 → [Input Guardrails：输入守卫](../04-guardrails/01-input-guardrails.md)
- 监控 handoff → [Lifecycle Hooks：生命周期钩子](../05-advanced/03-lifecycle-hooks.md)
