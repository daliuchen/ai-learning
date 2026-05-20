# Handoff Inputs & Filters：控制信息流

> **一句话**：通过 `handoff()` 函数可以要求模型 **handoff 前先填一个结构化对象**（input_type），并 **过滤要传给子 Agent 的上下文**（input_filter）——精细控制 handoff 行为。

---

## 1. 普通 handoff 的"问题"

```python
triage = Agent(handoffs=[billing])
```

转给 billing 时：

- 整段对话历史都给 billing 看
- billing 自己读历史判断要做啥
- 有时候**信息冗余 / 含敏感数据**

需要更精细控制时用 `handoff()` 函数。

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

模型转 Billing 前会先调 `transfer_to_billing(intent="refund", order_id="SO-1", reason="不满意")`——LLM 把意图结构化。

好处：

- **结构化路由信息**：方便业务系统记录
- **被迫澄清**：模型如果不知道 order_id，会先问用户
- **回调能做 side effect**：写库、计费、报警

---

## 3. input_filter：过滤历史

默认 handoff 把整段对话历史给子 Agent。要剪掉部分：

```python
from agents import handoff, handoff_filters


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

内置 filters：

- `remove_all_tools`：去掉所有 tool call / tool result
- 自定义：写函数

---

## 4. 自定义 input_filter

```python
from agents import HandoffInputData


def my_filter(data: HandoffInputData) -> HandoffInputData:
    """只保留最后 5 条消息"""
    return HandoffInputData(
        input_history=data.input_history[-5:],
        pre_handoff_items=data.pre_handoff_items,
        new_items=data.new_items,
    )


triage = Agent(
    handoffs=[
        handoff(agent=billing, input_filter=my_filter),
    ],
)
```

`HandoffInputData` 包含：

- `input_history`：转交前的对话历史
- `pre_handoff_items`：转交前主 Agent 新生成的 items
- `new_items`：handoff 触发本身

---

## 5. 把上下文压成 summary

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
    return HandoffInputData(
        input_history=new_history,
        pre_handoff_items=data.pre_handoff_items,
        new_items=data.new_items,
    )
```

---

## 6. on_handoff：触发回调

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

适合：

- 监控 / 可观测
- 升级 / 报警（敏感 handoff 触发告警）
- 业务计费（每次 handoff 收费？）

---

## 7. tool_name / tool_description 覆盖

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

适合：

- Agent 名字内部用，但给模型看的工具名要更友好
- 多语言文档

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
    return HandoffInputData(
        input_history=data.input_history[-10:],
        pre_handoff_items=[],   # 主 agent 的中间产物不给
        new_items=data.new_items,
    )


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
# demos/handoffs/03_handoff_inputs.py
import asyncio
from pydantic import BaseModel
from agents import Agent, Runner, handoff, function_tool, RunContextWrapper, handoff_filters


@function_tool
def lookup_invoice(order_id: str) -> str:
    return f"Order {order_id}: $99 paid"


@function_tool
def issue_refund(order_id: str) -> str:
    return f"Refund issued for {order_id}"


billing = Agent(
    name="Billing",
    instructions="""你看到 BillingHandoff.intent 知道要干啥。
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

跑：

```
[Audit] handoff to billing: {"intent":"refund","order_id":"SO-9911","reason":"用户对订单 SO-9911 不满意，要求退款"}

Refund issued for SO-9911。已为您处理退款。
```

---

## 10. 常见坑

| 坑 | 解 |
|----|----|
| input_type 太复杂，模型填不全 | 简化字段，留默认值 |
| input_filter 过滤太多，子 Agent 没上下文 | 至少保留最近 3 条 user message |
| on_handoff 抛异常 | 整个 run 失败——别在 handler 里做"重要副作用" |
| 多个 handoffs 用同 input_type | 没问题，但要在每个 Agent instructions 里说清何时调 |

---

## 11. 下一步

- 📖 复杂多 Agent 协作 → [04-complex-multi-agent.md](./04-complex-multi-agent.md)
- 📖 守卫体系 → [04-guardrails/01-input-guardrails.md](../04-guardrails/01-input-guardrails.md)
- 📖 监控 handoff → [05-advanced/03-lifecycle-hooks.md](../05-advanced/03-lifecycle-hooks.md)
