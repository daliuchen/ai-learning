# 复杂多 Agent 协作

> handoff 的用法不止 Triage 一种，升级链、几个专家互相转交、agent-as-tool 和 handoff 混用都很常见。这篇整理 4 种进阶模式，以及防死循环、跨 Agent 共享上下文的做法。

---

## 1. 模式 1：升级链（Escalation）

```
User → L1 Support → L2 Support → Manager
```

```python
manager = Agent(
    name="Manager",
    instructions="处理升级问题，有最高权限",
)

l2 = Agent(
    name="L2 Support",
    instructions="""复杂问题。
解决不了或需要授权 → handoff Manager""",
    handoffs=[manager],
)

l1 = Agent(
    name="L1 Support",
    instructions="""一般问题。
- 简单的自己答
- 复杂 / 涉及权限 → handoff L2
""",
    handoffs=[l2],
)


triage = Agent(
    name="Triage",
    instructions="所有问题先转 L1",
    handoffs=[l1],
)
```

适用于客服分级、安全事件分级、医院分诊这类逐级上报的场景。

---

## 2. 模式 2：双向 handoff（专家网络）

```
Billing ↔ Support  （账单问题可能是技术原因，反之亦然）
```

```python
billing = Agent(
    name="Billing",
    instructions="""账单问题。
若发现是技术原因（比如订阅没生效是 bug），handoff Support""",
)

support = Agent(
    name="Support",
    instructions="""技术问题。
若发现是账单（比如 API 限流是欠费），handoff Billing""",
    handoffs=[billing],
)

# 先建好两个 Agent 再互相挂；用 clone 生成新对象的话，billing 指向的还是旧的 support
billing.handoffs.append(support)
```

双向转交容易死循环。可以用 `max_turns` 兜底，在 instructions 里写上“如果刚被 X 转过来，不要再转回 X”，再用 hook 监控 handoff 链的长度。

---

## 3. 模式 3：as_tool 和 handoff 混用

主 Agent 当项目经理，既可以调用专家（as_tool），也可以把对话转交出去（handoff）：

```python
researcher = Agent(name="Researcher", instructions="搜资料")
writer = Agent(name="Writer", instructions="写报告")

human_in_loop = Agent(
    name="HumanReviewer",
    instructions="需要人审核的，转给这里（实际后端拦截）",
)
# 更推荐：真要“人工点头”，不要靠 handoff 到一个假 Agent，
# 而是给敏感工具设 needs_approval=True，用 result.interruptions + RunState 暂停/恢复（见 02-tools/04-tool-choice 第 10 节）


pm = Agent(
    name="ProjectManager",
    instructions="""你协调多个专家完成研究项目。

工作流：
1. 用 research(sub_question) 子工具收集信息
2. 用 write(material) 子工具生成报告
3. 报告涉及敏感内容时 handoff HumanReviewer
""",
    tools=[
        researcher.as_tool("research", "对 sub-question 做研究"),
        writer.as_tool("write", "把素材写成报告"),
    ],
    handoffs=[human_in_loop],
)
```

区分的标准是专家要不要接手对话：不需要的用 as_tool，需要接手的特殊场景才用 handoff。

---

## 4. 模式 4：并发分支（用 asyncio）

handoff 是串行的，同一时刻只有一个 active agent。要并发，只能在外层自己写：

```python
researcher_a = Agent(name="ResearcherA", ...)
researcher_b = Agent(name="ResearcherB", ...)
synthesizer = Agent(name="Synthesizer", ...)


async def research_pipeline(question: str):
    # 并发跑两个 researcher
    r1, r2 = await asyncio.gather(
        Runner.run(researcher_a, f"角度 1: {question}"),
        Runner.run(researcher_b, f"角度 2: {question}"),
    )

    # 综合
    combined = f"研究 A:\n{r1.final_output}\n\n研究 B:\n{r2.final_output}"
    result = await Runner.run(synthesizer, combined)
    return result.final_output
```

怎么把并发的几段绑到同一个 trace 上，见 [内置 Tracing 与 OpenAI Platform Dashboard](../05-advanced/01-tracing.md)。

---

## 5. 用 Context 跨 Agent 共享数据

```python
from dataclasses import dataclass


@dataclass
class AppContext:
    user_id: str
    org_id: str
    db: object


@function_tool
async def lookup_org_info(ctx: RunContextWrapper[AppContext]) -> str:
    return f"Org: {ctx.context.org_id}"


billing = Agent(tools=[lookup_org_info])
support = Agent(tools=[lookup_org_info])
triage = Agent(handoffs=[billing, support])


ctx = AppContext(user_id="u1", org_id="org_42", db=db)
await Runner.run(triage, "...", context=ctx)
```

所有 Agent，包括被 handoff 到的，都能拿到 `ctx.context.user_id` 这些字段。Context 在 handoff 时自动传递，`as_tool` 的子 Agent 拿到的也是同一个 context。如果想把应用状态和模型决定的元数据分开，前者放 context，后者用 handoff 的 `input_type`。

---

## 6. 防死循环

```python
from agents import RunHooks


class HandoffGuard(RunHooks):
    """监控 handoff 链长度"""
    def __init__(self, max_handoffs=5):
        self.count = 0
        self.max = max_handoffs

    async def on_handoff(self, context, from_agent, to_agent):
        self.count += 1
        if self.count > self.max:
            raise RuntimeError(f"Too many handoffs: {self.count}")


await Runner.run(triage, "...", hooks=HandoffGuard(max_handoffs=5))
```

这个守卫可以用 `agents.testing.ScriptedModel` 假模型验证，不花钱：A 和 B 互相 handoff 形成死循环时，`max_handoffs=5` 的守卫会在第 6 次转交时抛出 `Too many handoffs: 6`。

更省事的办法是靠 `max_turns` 兜底。一次 handoff 大约算 1 个 turn，大多数流程 10 个 turn 以内能走完，配上 `error_handlers={"max_turns": ...}` 还可以体面地收尾。

---

## 7. 跟 LangGraph 对比

| 用例 | OpenAI Handoff | LangGraph |
|------|----------------|-----------|
| 客服 Triage | 很自然 | 需要 conditional_edges |
| 升级链 | 很自然 | 也自然，而且更显式 |
| 并发分支 | 外层 asyncio.gather | 原生支持（fanout） |
| 状态机 | 弱 | 强 |
| 长流程 / Checkpoint | `RunState` 可存盘恢复；跨天的长流程要接 Temporal / DBOS / Restate / Dapr 集成 | 强 |
| Human-in-loop | `needs_approval` + `RunState`（可序列化、跨进程恢复） | interrupt() 原生支持 |

handoff 适合“路由 + 转交”这种扁平的协作；状态机、复杂工作流和并发，我会交给 LangGraph。

---

## 8. 完整 demo：协作研究

```python
import asyncio
from agents import Agent, Runner, function_tool


@function_tool
def search_web(query: str) -> str:
    return f"web result for {query}"


researcher = Agent(
    name="Researcher",
    instructions="""对 sub-question 做研究。
- 用 search_web 找 2-3 个源
- 给 100 字摘要 + URL""",
    tools=[search_web],
    model="gpt-6-luna",
)


writer = Agent(
    name="Writer",
    instructions="""把素材写成 500 字报告。
- 引用源
- 平衡观点""",
    model="gpt-6.1-sol",
)


fact_checker = Agent(
    name="FactChecker",
    instructions="""检查报告事实准确性。
- 找 2-3 个可能问题
- 给出修改建议""",
    model="gpt-6-luna",
)


pm = Agent(
    name="PM",
    instructions="""你是研究项目协调员。

工作流：
1. 把问题拆 3-5 个 sub-questions
2. 每个 sub-question 调 research(...)
3. 用 write(...) 生成报告
4. 复杂主题 → handoff fact_checker 复核
5. 给用户最终结果

简单问题可跳过 fact_checker。
""",
    tools=[
        researcher.as_tool("research", "做单个 sub-question 研究"),
        writer.as_tool("write", "把素材写成报告"),
    ],
    handoffs=[fact_checker],
    model="gpt-6.1-sol",
)


async def main():
    result = await Runner.run(
        pm,
        "AI Agent 框架现状（OpenAI / LangChain / Pydantic AI）",
        max_turns=20,
    )
    print(result.final_output)
    print(f"\n最后处理：{result.last_agent.name}")


asyncio.run(main())
```

---

## 9. 常见坑

| 坑 | 解 |
|----|----|
| 双向 handoff 死循环 | max_turns，再在 instructions 里写“刚转过来的别转回去” |
| 升级链太长，用户等不及 | 设上限，超过 N 级直接给人工渠道 |
| Agent 之间 context 数据丢了 | 用 context 参数传，别塞进 instructions |
| 并发跑 Agent，trace 看不清 | 外面包一层 `with trace("pipeline")` |

---

## 10. 下一步

- Input/Output Guardrails → [Input Guardrails：输入守卫](../04-guardrails/01-input-guardrails.md)
- 多 Agent 的 Tracing → [内置 Tracing 与 OpenAI Platform Dashboard](../05-advanced/01-tracing.md)
- 实战：完整的 Triage 客服 → [实战 1：客服 Triage Agent](../08-practice/01-customer-triage.md)
