# Triage Pattern：分流模式

> **一句话**：一个 "Triage Agent" 不干活，只负责把用户路由给对应专家——是 handoffs 最经典的用法，OpenAI 招牌 demo。

---

## 1. Pattern 描述

```
User
 ↓
Triage Agent（只判断主题）
 ├─ handoff → Billing Agent（专家 1）
 ├─ handoff → Support Agent（专家 2）
 └─ handoff → Sales Agent（专家 3）
```

特点：

- Triage 只懂分类，不懂业务
- 每个专家有自己的 instructions / tools
- 单一入口、多专家分流

---

## 2. 啥时候用

✅ 适合：

- 客服 / 帮助中心（典型）
- 多领域助手（写作 / 代码 / 翻译）
- 内部知识库（HR / IT / Finance）

❌ 不适合：

- 任务需要多专家**协作**（用 multi-agent 编排，详见 [04-complex-multi-agent.md](./04-complex-multi-agent.md)）
- 流程有严格步骤（用 LangGraph）

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

每轮 Triage 重新分流，session 把历史给到专家。

---

## 5. Triage 的 instruction 怎么写

通用模板：

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

## 6. Triage 的 model 选择

通常 Triage 用 **mini / haiku 级别模型**就够，因为只做分类。专家 Agent 用更强的模型。

```python
triage = Agent(name="Triage", instructions="...", model="gpt-4o-mini")
billing = Agent(name="Billing", instructions="...", model="gpt-4o")
```

省钱 + Triage 反应快。

---

## 7. Default fallback

不知道转哪 → 让 Triage 自己回答兜底：

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

## 8. Triage 也带 guardrails

入口加守卫拦 PII / 越权请求：

```python
from agents import input_guardrail, GuardrailFunctionOutput


@input_guardrail
async def block_pii(ctx, agent, user_input):
    if "身份证号" in user_input:
        return GuardrailFunctionOutput(tripwire_triggered=True)
    return GuardrailFunctionOutput()


triage = Agent(
    name="Triage",
    instructions="...",
    handoffs=[billing, support],
    input_guardrails=[block_pii],
)
```

详见 [04-guardrails/01-input-guardrails.md](../04-guardrails/01-input-guardrails.md)。

---

## 9. handoff 回 Triage？

如果专家不能处理，要回 Triage 让它重新分流？

```python
billing = Agent(
    name="Billing",
    instructions="如果用户问的不是账单，handoff 回 Triage",
    handoffs=["Triage"],  # ⚠️ 名字字符串
)
```

但**别玩**：

- 容易循环
- 用户体验差（一直转）
- 更好的做法：Triage 入口判得准 + 专家有"对不起这个我处理不了"兜底

---

## 10. 监控 / 评测分流准确率

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

事后人工 / LLM 抽样标 "应该转去哪" → 算 Triage 准确率。详见 [07-production/05-evals.md](../07-production/05-evals.md)。

---

## 11. 完整 demo

```python
# demos/handoffs/02_triage.py
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
    model="gpt-4o-mini",
)


@function_tool
def search_kb(query: str) -> str:
    return f"KB: {query} - 请尝试重启 / 清缓存 / 升级"


support = Agent(
    name="Support",
    instructions="处理技术问题，先 search_kb",
    tools=[search_kb],
    model="gpt-4o-mini",
)


triage = Agent(
    name="Triage",
    instructions="""分流：
- 账单 → Billing
- 技术 → Support
- 闲聊 → 自己用一句话回应""",
    handoffs=[billing, support],
    model="gpt-4o-mini",
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

- 📖 控制 handoff 时传啥信息 → [03-handoff-inputs.md](./03-handoff-inputs.md)
- 📖 复杂多 Agent 编排 → [04-complex-multi-agent.md](./04-complex-multi-agent.md)
- 📖 实战：完整客服 Triage → [08-practice/01-customer-triage.md](../08-practice/01-customer-triage.md)
