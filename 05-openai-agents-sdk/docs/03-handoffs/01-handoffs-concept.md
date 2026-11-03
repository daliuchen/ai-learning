# Handoffs 概念：与 Tool、Sub-Agent 的区别

> Handoff 的意思是把对话转给另一个 Agent，之后由它接着聊。它和 tool 不同，控制权不会回到原来的 Agent；也和普通的子 Agent 调用不同，主 Agent 不用等它返回。这是 OpenAI Agents SDK 里最有辨识度的设计，这篇讲清楚它和另外两种做法怎么区分。

---

## 1. 来源：Swarm 项目

OpenAI 在 2024 年 10 月开源 Swarm 时提出了一个想法：多 Agent 协作不一定要像 LangGraph 那样画一张图，Agent 之间互相转交（handoff）就能覆盖很多场景。

拿打客服电话来类比：

```
客服分流（人类版）：
  你打 10086 → 接通"语音菜单"
  按 1 → 转到"账单专员"（从此由账单专员接你的话）
  账单专员发现是技术问题 → 转到"技术专员"
```

每转接一次就是一次 handoff，控制权和上下文都交给下一位。

---

## 2. 三种多 Agent 协作方式对比

| 模式 | 控制权 | 上下文 | 接口 |
|------|--------|--------|------|
| Tool（@function_tool） | 调完回主 | 主 Agent 决定给什么 | `tools=[...]` |
| Agent as Tool | 调完回主 | 主 Agent 决定给什么 | `tools=[child.as_tool(...)]` |
| Handoff | 转给子 | 整段对话 | `handoffs=[...]` |

### 怎么挑

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

`handoffs=[billing, support]` 表示 Triage 可以把对话转给这两个 Agent。

---

## 4. 底层实现

SDK 会为每个 handoff 自动生成一个名为 `transfer_to_<agent_name>` 的 tool，名字里的空格和大写会转成 snake_case，比如 `Agent(name="Billing Team")` 生成的是 `transfer_to_billing_team`。模型调用这个 tool，SDK 就切换当前 agent，然后继续跑。

模型看到的 tool 大致是这样：

```json
{
  "name": "transfer_to_billing_team",
  "description": "Handoff to the Billing Team agent to handle the request. <该 Agent 的 handoff_description>",
  "parameters": {}
}
```

调用它就等于转接。在目标 Agent 上设置 `handoff_description="账单、退款、订阅问题"`，这段文字会追加到上面的描述后面。这样不用写完整的 `handoff()` 对象，也能告诉模型什么时候该选它。

官方还提供了一段前缀，向模型解释 handoff 是怎么回事，建议放在各 Agent 的 instructions 开头：

```python
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX, prompt_with_handoff_instructions

billing = Agent(name="Billing", instructions=RECOMMENDED_PROMPT_PREFIX + "处理账单、退款、订阅问题。")
# 或：instructions=prompt_with_handoff_instructions("处理账单……")
```

---

## 5. handoff 的参数

简单形式：

```python
handoffs=[billing, support]
```

需要更多控制时，用 `handoff()` 包一层：

```python
from agents import Agent, handoff

billing = Agent(name="Billing", instructions="...", handoff_description="账单、退款、订阅")

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
            is_enabled=True,    # 布尔 / 函数：按运行时条件开关这个 handoff
            nest_handoff_history=None,   # 覆盖 RunConfig 里的“嵌套历史”设置
        ),
    ],
)
```

各参数的用法见 [Handoff Inputs & Filters：控制信息流](./03-handoff-inputs.md)。

---

## 6. 接手之后还能再转吗

能。Billing 也可以有自己的 `handoffs`：

```python
escalation = Agent(name="Manager", instructions="处理升级问题")

billing = Agent(
    name="Billing",
    instructions="账单问题，太复杂的转 Manager",
    handoffs=[escalation],
)
```

链路就变成 Triage → Billing → Manager。

别构造成环（Billing → Support → Billing → ...），真出现了 SDK 也会在 max_turns 处拦下。

还有一点容易忽略：handoff 始终发生在同一次 run 里。所以 input guardrails 只对链上第一个 agent 生效，output guardrails 只对产出最终回答的那个 agent 生效。想在每个专家调用工具前后做检查，要用 tool guardrails（见 [Tripwire 与异常处理](../04-guardrails/03-tripwire.md)）。另外 handoff 调用本身不走函数工具那条管线，tool guardrails 管不到它。

---

## 7. handoff 和 as_tool 的场景对比

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

LangGraph 的多 Agent 写法：

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

LangGraph 要你显式定义图，节点和边都是一等对象；OpenAI 这边只声明可以转给谁，什么时候转由 LLM 决定。

所以流程必须经过特定节点（严格状态机）、需要并发分支再合并、或者有循环、重试、复杂条件时，我会选 LangGraph。反过来，如果流程本身比较模糊、要靠 LLM 判断，主线就是路由，又希望配置少、上手快，handoff 更合适。

---

## 9. handoff 和 Session

```python
session = SQLiteSession("user_42")

result = await Runner.run(triage, "我要退款", session=session)
# session 现在有：用户消息 + Triage 决定 handoff + Billing 回答

# 下一轮
result2 = await Runner.run(triage, "我还想问个订阅问题", session=session)
# session 接续——内部还是从 triage 入口
```

每次 `Runner.run` 都从你传入的 agent 开始，即使历史里已经有 Billing 的回答。

想让下一轮直接从 Billing 开始，就传 `Runner.run(result.last_agent, ...)`。两种做法各有道理：官方文档说 `last_agent` “通常是下一轮最合适的入口”，因为用户多半还在追问同一件事；每轮从 Triage 重新分流则更稳，话题换了也不会卡在某个专家那里。常见的折中是默认从 `last_agent` 继续，专家发现问题不归自己管时再 handoff 回去或转走。

再说历史怎么传。新 Agent 默认看到整段对话。0.6 曾改成把历史打包成一条 assistant 摘要消息，0.7 又把这个行为改成需要手动开启：现在默认原样传整段历史，想压缩要设 `RunConfig(nest_handoff_history=True)`，或在单个 `handoff(..., nest_handoff_history=True)` 上开。这个功能还是 beta，而且不做脱敏，工具参数和输出仍可能出现在生成的摘要里。

---

## 10. result.last_agent 用法

```python
result = await Runner.run(triage, "...", session=session)
print(result.last_agent.name)  # "Billing"

# 想下一轮直接 ask billing
if isinstance(result.last_agent, Agent):
    result2 = await Runner.run(result.last_agent, "...", session=session)
```

不过在对话层面，我更常用的做法还是每轮都从 Triage 进入，让它重新判断一次。

---

## 11. 完整 demo

```python
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

- 完整的分流写法 → [Triage Pattern：分流模式](./02-triage-pattern.md)
- 控制 handoff 传哪些信息 → [Handoff Inputs & Filters：控制信息流](./03-handoff-inputs.md)
- 多层 handoff → [复杂多 Agent 协作](./04-complex-multi-agent.md)
