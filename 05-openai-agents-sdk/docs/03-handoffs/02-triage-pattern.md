# Triage Pattern：分流模式

> Triage Agent 自己不处理业务，只负责把用户转给对应的专家。这是 handoffs 最常见的用法，也是 OpenAI 官方示例里出现最多的结构。

---

## 1. 结构

```
User
 ↓
Triage Agent（只判断主题）
 ├─ handoff → Billing Agent（专家 1）
 ├─ handoff → Support Agent（专家 2）
 └─ handoff → Sales Agent（专家 3）
```

Triage 只管分类，不懂业务；每个专家有自己的 instructions 和 tools。对用户来说只有一个入口，背后是多个专家。

---

## 2. 什么时候用

适合的场景：客服和帮助中心、写作/代码/翻译这类多领域助手、HR/IT/Finance 的内部知识库。

不适合的场景有两种。一是任务需要几个专家协作完成，这时该用多 Agent 编排（见 [复杂多 Agent 协作](./04-complex-multi-agent.md)）；二是流程有严格的步骤顺序，这种用 LangGraph 更合适。

---

## 3. 写法

```python
from agents import Agent, Runner, function_tool


# === 专家 1：账单 ===

@function_tool
def lookup_invoice(order_id: str) -> str:
    return f"Order {order_id}: paid, $99"

@function_tool
def issue_refund(order_id: str) -> str:
    return f"Refund issued for {order_id}"


billing = Agent(
    name="Billing",
    instructions="""你是账单专员。
- 用 lookup_invoice 查订单
- 用 issue_refund 退款
- 退款前先确认订单号
""",
    tools=[lookup_invoice, issue_refund],
)


# === 专家 2：技术 ===

@function_tool
def search_kb(query: str) -> str:
    return f"KB result for {query}"

@function_tool
def create_ticket(title: str, description: str) -> str:
    return f"Ticket TK-1234 created"


support = Agent(
    name="Support",
    instructions="""你是技术支持。
- 先用 search_kb 找已有方案
- 找不到 → create_ticket 开工单
""",
    tools=[search_kb, create_ticket],
)


# === 专家 3：销售 ===

sales = Agent(
    name="Sales",
    instructions="你是销售。回答产品价格、套餐对比、demo 预约。",
)


# === Triage（不带 tool，只分流）===

triage = Agent(
    name="Triage",
    instructions="""你是客服分流员。

把用户问题转给对应专家：
- Billing: 账单 / 退款 / 订阅 / 付款 / 发票
- Support: bug / 报错 / 登录 / 性能 / 集成
- Sales: 价格 / 套餐 / 试用 / 联系销售

不确定的先问一句澄清，再决定转哪边。
不要自己回答业务问题。
""",
    handoffs=[billing, support, sales],
)
```

> 给每个专家设 `handoff_description`（如 `Agent(name="Billing", handoff_description="账单、退款、订阅、发票", ...)`），SDK 会把它追加到 `transfer_to_billing` 的工具描述里，Triage 的 instructions 就能写短一些。instructions 开头再拼上 `RECOMMENDED_PROMPT_PREFIX`（在 `agents.extensions.handoff_prompt` 里）会更稳。

---

## 4. 使用 + Session 续接

```python
import asyncio
from agents import SQLiteSession


async def chat():
    session = SQLiteSession("user_42", "triage.db")

    while True:
        msg = input("> ")
        if not msg or msg in {"exit"}:
            break
        result = await Runner.run(triage, msg, session=session)
        print(f"[{result.last_agent.name}] {result.final_output}\n")


asyncio.run(chat())
```

对话示例：

```
> 想退款
[Billing] 好的，请提供订单号。

> SO-12345
[Billing] 查到 SO-12345 已支付 $99，是否确认退款？

> 是的
[Billing] 已退款。

> 另外我登录有问题
[Support] 您是登录时报什么错？...
```

每一轮都由 Triage 重新分流，session 负责把历史带给专家。如果希望用户追问时直接留在专家那里，改成 `Runner.run(last_agent, ...)` 就行，见 [Handoffs 概念：与 Tool、Sub-Agent 的区别](./01-handoffs-concept.md) 第 9 节。

---

## 5. Triage 的 instructions 怎么写

一个通用模板：

```
你是 [组织名] 的分流员。

不要自己回答业务问题，只判断主题、转给专家。

专家清单：
- {Name}: {覆盖范围}，关键词 {例子}

如果不确定：
- 主题模糊 → 问 1 个澄清问题
- 用户混合多个主题 → 优先级 X > Y > Z
- 闲聊 → 礼貌引导回业务

调用 transfer_to_<name> 转交。
```

---

## 6. Triage 用什么模型

Triage 只做分类，一般用 gpt-6-luna、claude-haiku 这一级的小模型就够了，专家再用强一些的模型。

```python
triage = Agent(name="Triage", instructions="...", model="gpt-6-luna")
billing = Agent(name="Billing", instructions="...", model="gpt-6.1-sol")
```

这样既省钱，Triage 的响应也快。

---

## 7. 兜底

判断不了该转给谁时，让 Triage 自己回答：

```python
triage = Agent(
    name="Triage",
    instructions="""...

如果以上任一都不匹配，自己用简洁回答告诉用户：
"我目前能帮您处理账单、技术支持、销售咨询。请告诉我您具体想问什么？"
""",
    handoffs=[billing, support, sales],
)
```

---

## 8. 给 Triage 加 guardrails

在入口加守卫，拦下 PII 或越权请求。input guardrail 只对链上第一个 agent 生效，Triage 正好是入口。它默认和 Triage 的第一次模型调用并行执行；如果希望被拦下的输入完全不触发模型和工具，用 `@input_guardrail(run_in_parallel=False)`：

```python
from agents import input_guardrail, GuardrailFunctionOutput


@input_guardrail
async def block_pii(ctx, agent, user_input):
    if "身份证号" in user_input:
        return GuardrailFunctionOutput(output_info=None, tripwire_triggered=True)
    return GuardrailFunctionOutput(output_info=None)


triage = Agent(
    name="Triage",
    instructions="...",
    handoffs=[billing, support],
    input_guardrails=[block_pii],
)
```

详见 [Input Guardrails：输入守卫](../04-guardrails/01-input-guardrails.md)。

---

## 9. 专家能转回 Triage 吗

专家处理不了时，可以转回 Triage 重新分流：

```python
billing = Agent(
    name="Billing",
    instructions="如果用户问的不是账单，handoff 回 Triage",
)
triage = Agent(name="Triage", instructions="分流", handoffs=[billing])
billing.handoffs.append(triage)     # 循环引用：先建好两个 Agent，再互相挂（handoffs 必须是 Agent / Handoff 对象，不能写名字字符串）
```

但我不建议这么做。这样很容易形成循环，用户也会被转来转去。更好的办法是让 Triage 在入口就判断准，同时给专家写好“这个我处理不了”的兜底回复。

---

## 10. 监控和评测分流准确率

```python
# 灰度阶段记录
async def chat_with_logging(user_id: str, msg: str):
    result = await Runner.run(triage, msg, session=session_for(user_id))

    # 落库（人工标注用）
    log = {
        "user_id": user_id,
        "input": msg,
        "routed_to": result.last_agent.name,
        "answer": result.final_output,
    }
    return log
```

事后用人工或 LLM 抽样标注“应该转去哪”，就能算出 Triage 的准确率。详见 [评测：把 Prompt Engineering 手册的评测集用到 Agent 上](../07-production/05-evals.md)。

---

## 11. 完整 demo

```python
import asyncio
from agents import Agent, Runner, function_tool, SQLiteSession


@function_tool
def lookup_invoice(order_id: str) -> str:
    return f"Order {order_id}: paid $99"

@function_tool
def issue_refund(order_id: str) -> str:
    return f"Refund issued for {order_id}"


billing = Agent(
    name="Billing",
    instructions="处理账单，需要订单号",
    tools=[lookup_invoice, issue_refund],
    model="gpt-6-luna",
)


@function_tool
def search_kb(query: str) -> str:
    return f"KB: {query} - 请尝试重启 / 清缓存 / 升级"


support = Agent(
    name="Support",
    instructions="处理技术问题，先 search_kb",
    tools=[search_kb],
    model="gpt-6-luna",
)


triage = Agent(
    name="Triage",
    instructions="""分流：
- 账单 → Billing
- 技术 → Support
- 闲聊 → 自己用一句话回应""",
    handoffs=[billing, support],
    model="gpt-6-luna",
)


async def main():
    session = SQLiteSession("demo")

    for q in [
        "你好",
        "我想退款，订单 SO-9911",
        "登录后白屏",
    ]:
        result = await Runner.run(triage, q, session=session)
        print(f"Q: {q}")
        print(f"A ({result.last_agent.name}): {result.final_output[:100]}\n")


asyncio.run(main())
```

---

## 12. 下一步

- 控制 handoff 时传哪些信息 → [Handoff Inputs & Filters：控制信息流](./03-handoff-inputs.md)
- 更复杂的多 Agent 编排 → [复杂多 Agent 协作](./04-complex-multi-agent.md)
- 实战：完整的客服 Triage → [实战 1：客服 Triage Agent](../08-practice/01-customer-triage.md)
