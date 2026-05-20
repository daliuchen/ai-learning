# Handoffs 概念：跟 Tool / Sub-Agent 啥区别

> **一句话**：Handoff = "把这个对话转给另一个 Agent，从此由它接手"。它不是 tool（控制权不回主 Agent），也不是简单 sub-agent 调用（不需要主 Agent 等返回）。这是 OpenAI Agents SDK 的独门设计。

---

## 1. 来源：Swarm 项目的核心 idea

OpenAI 在 2024 年 10 月开源 Swarm 时提出的核心 idea：**多 Agent 协作不一定要图（LangGraph 那样），routes 间转交（handoff）就够用**。

类比：

```
客服分流（人类版）：
  你打 10086 → 接通"语音菜单"
  按 1 → 转到"账单专员"（从此由账单专员接你的话）
  账单专员发现是技术问题 → 转到"技术专员"
```

每次转接 = handoff。控制权 / 上下文都给下一位。

---

## 2. 三种"多 Agent 协作"模式对比

| 模式 | 控制权 | 上下文 | 接口 |
|------|--------|--------|------|
| **Tool**（@function_tool） | 调完回主 | 主决定给啥 | `tools=[...]` |
| **Agent as Tool** | 调完回主 | 主决定给啥 | `tools=[child.as_tool(...)]` |
| **Handoff** | 转给子 | 整段对话 | `handoffs=[...]` |

### 用什么挑

```
任务能"调一下拿结果就完了"吗？
  ├─ 能 → as_tool
  └─ 不能（需要接手对话） → handoff

子 Agent 需要看完整对话历史吗？
  ├─ 不需要 → as_tool（你过滤后给）
  └─ 需要 → handoff

之后用户继续问，是谁回答？
  ├─ 主 Agent → as_tool
  └─ 子 Agent（被转过去那个） → handoff
```

---

## 3. 最简示例

```python
from agents import Agent, Runner

billing = Agent(
    name="Billing",
    instructions="处理账单、退款、订阅问题。",
)

support = Agent(
    name="Support",
    instructions="处理技术问题、bug 报告。",
)

triage = Agent(
    name="Triage",
    instructions="""你是分流员。
- 账单 / 退款 / 订阅 → handoff 到 Billing
- 技术 / bug / 报错 → handoff 到 Support
- 闲聊 → 自己回答
""",
    handoffs=[billing, support],
)


result = await Runner.run(triage, "我要退款")
print(result.final_output)
print(f"由 {result.last_agent.name} 处理")  # "Billing"
```

`handoffs=[billing, support]` 意味着 Triage 能把对话转给这俩。

---

## 4. 底层是怎么实现的

SDK 自动为每个 handoff 生成一个 "transfer_to_<agent_name>" 的 tool。模型调这个 tool → SDK 切换 active agent → 继续跑。

模型看到的等价 tools：

```json
{
  "name": "transfer_to_billing",
  "description": "Handoff to Billing for billing-related questions",
  "parameters": {}
}
```

调用 = 转接。

---

## 5. handoff 的参数

简单形式：

```python
handoffs=[billing, support]
```

详细形式（用 `handoff()` 包装）：

```python
from agents import Agent, handoff

billing = Agent(name="Billing", instructions="...")

triage = Agent(
    name="Triage",
    instructions="...",
    handoffs=[
        handoff(
            agent=billing,
            tool_name_override="transfer_to_billing_team",
            tool_description_override="转账单团队",
            input_filter=...,   # 过滤要传的上下文
            on_handoff=...,     # 回调
            input_type=...,     # 要求模型先填一个结构化对象
        ),
    ],
)
```

详见 [03-handoff-inputs.md](./03-handoff-inputs.md)。

---

## 6. handoff 后还能 handoff 吗

能。Billing 也可以有自己的 `handoffs`：

```python
escalation = Agent(name="Manager", instructions="处理升级问题")

billing = Agent(
    name="Billing",
    instructions="账单问题，太复杂的转 Manager",
    handoffs=[escalation],
)
```

链路：Triage → Billing → Manager。

⚠️ 别构造**环**（Billing → Support → Billing → ...），SDK 也会被 max_turns 拦下。

---

## 7. handoff vs as_tool 用例对比

### 场景 1：翻译

```python
# as_tool 合适——翻译是独立任务，主 Agent 还要继续工作
main = Agent(tools=[translator.as_tool("translate", "...")])
```

### 场景 2：客服分流

```python
# handoff 合适——账单问题就让 Billing 接手，主 Agent 不掺和
triage = Agent(handoffs=[billing, support])
```

### 场景 3：复杂研究

```python
# as_tool 合适——主 Agent 规划 + 综合，子 Agent 是执行单元
main = Agent(tools=[researcher.as_tool("research", "...")])
```

### 场景 4：升级流程

```python
# handoff 合适——升级后 Manager 接手所有后续
billing = Agent(handoffs=[manager])
```

---

## 8. 跟 LangGraph 对比

LangGraph 的 multi-agent：

```python
graph.add_node("billing", billing_runnable)
graph.add_node("support", support_runnable)
graph.add_edge(START, "triage")
graph.add_conditional_edges("triage", router)
```

OpenAI 的 handoff：

```python
triage = Agent(handoffs=[billing, support])
```

**思路差异**：

- LangGraph：你**显式**定义图，节点/边都是 first-class
- OpenAI：你**声明**可转给谁，由 LLM 决定何时转

**何时偏 LangGraph**：

- 需要严格状态机（流程必经特定节点）
- 需要并发分支 + 合并
- 需要循环 / 重试 / 复杂条件

**何时偏 Handoff**：

- 流程"模糊"由 LLM 判断
- 主线就是路由
- 配置量少、上手快

---

## 9. handoff 的 Session 行为

```python
session = SQLiteSession("user_42")

result = await Runner.run(triage, "我要退款", session=session)
# session 现在有：用户消息 + Triage 决定 handoff + Billing 回答

# 下一轮
result2 = await Runner.run(triage, "我还想问个订阅问题", session=session)
# session 接续——内部还是从 triage 入口
```

每次 `Runner.run` 都从你传的 agent 开始（虽然历史里有 Billing 的回答）。

要让 "下一轮直接从 Billing 入" → 传 `Runner.run(billing, ...)`，但**通常你不需要**——让 Triage 重新分流更稳。

---

## 10. result.last_agent 用法

```python
result = await Runner.run(triage, "...", session=session)
print(result.last_agent.name)  # "Billing"

# 想下一轮直接 ask billing
if isinstance(result.last_agent, Agent):
    result2 = await Runner.run(result.last_agent, "...", session=session)
```

但对话层最佳实践：始终从 Triage 入，让它每次重新判断。

---

## 11. 完整 demo

```python
# demos/handoffs/01_concept.py
import asyncio
from agents import Agent, Runner


billing = Agent(
    name="Billing",
    instructions="""你处理账单、退款、订阅。
回答时先确认问题类型，再询问关键信息（订单号 / 邮箱）。""",
)


support = Agent(
    name="Support",
    instructions="""你处理技术问题、bug 报告。
回答时先确认错误信息，再给排查建议。""",
)


triage = Agent(
    name="Triage",
    instructions="""你是分流员。
- 账单 / 退款 / 订阅 / 付款 → 转 Billing
- 技术 / bug / 报错 / 登录 → 转 Support
- 模糊的先澄清，再决定转哪边
""",
    handoffs=[billing, support],
)


async def main():
    for q in ["我要退款", "登录后报 500 错误", "你们怎么收费"]:
        result = await Runner.run(triage, q)
        print(f"\nQ: {q}")
        print(f"A ({result.last_agent.name}): {result.final_output[:80]}")


asyncio.run(main())
```

输出：

```
Q: 我要退款
A (Billing): ...

Q: 登录后报 500 错误
A (Support): ...

Q: 你们怎么收费
A (Billing): ...
```

---

## 12. 下一步

- 📖 Triage Pattern 完整 → [02-triage-pattern.md](./02-triage-pattern.md)
- 📖 控制 handoff 信息流 → [03-handoff-inputs.md](./03-handoff-inputs.md)
- 📖 多层 handoff → [04-complex-multi-agent.md](./04-complex-multi-agent.md)
