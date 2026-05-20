# 复杂多 Agent 协作

> **一句话**：handoff 不止 Triage 一种用法——升级链、专家协会诊、agent-as-tool 跟 handoff 混用都很常见。本篇梳理 4 种进阶模式。

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

**用例**：客服分级、安全事件分级、医院分诊。

---

## 2. 模式 2：双向 handoff（专家网络）

```
Billing ↔ Support  （账单问题可能是技术原因，反之亦然）
```

```python
support = Agent(name="Support", instructions="...")

billing = Agent(
    name="Billing",
    instructions="""账单问题。
若发现是技术原因（比如订阅没生效是 bug），handoff Support""",
    handoffs=[support],
)

support = support.clone(
    instructions="""技术问题。
若发现是账单（比如 API 限流是欠费），handoff Billing""",
    handoffs=[billing],
)
```

⚠️ 风险：双向容易死循环。控制方式：

- `max_turns` 兜底
- instructions 里加 "如果刚刚被 X 转过来，不要再转回 X"
- 用 hook 监控 handoff 链长度

---

## 3. 模式 3：协作（as_tool + handoff 混合）

主 Agent 是"项目经理"，可以**调专家**（as_tool）或**转交**（handoff）：

```python
researcher = Agent(name="Researcher", instructions="搜资料")
writer = Agent(name="Writer", instructions="写报告")

human_in_loop = Agent(
    name="HumanReviewer",
    instructions="需要人审核的，转给这里（实际后端拦截）",
)


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

**关键**：

- 不需要接手对话的专家 → as_tool
- 需要接手对话的特殊场景 → handoff

---

## 4. 模式 4：并发分支（用 asyncio）

handoff 本质是串行（一次一个 active agent）。要并发要在外层手动：

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

详见 [05-advanced/01-tracing.md](../05-advanced/01-tracing.md) 怎么把并发段绑到同一个 trace。

---

## 5. 链路上下文：Context 跨 Agent 共享

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

所有 Agent（包括被 handoff 到的）都能拿 `ctx.context.user_id` 等。Context 在 handoff 中**自动传递**。

---

## 6. 防死循环

```python
class HandoffGuard:
    """监控 handoff 链长度"""
    def __init__(self, max_handoffs=5):
        self.count = 0
        self.max = max_handoffs

    def check(self, source: str, target: str):
        self.count += 1
        if self.count > self.max:
            raise RuntimeError(f"Too many handoffs: {self.count}")


# 用 hook（详见 05-advanced/03）监控
```

或者更简单：`max_turns` 兜底。一个 handoff = 1 turn 左右，10 turn 内能完成的流程足够大多数场景。

---

## 7. 跟 LangGraph 对比

| 用例 | OpenAI Handoff | LangGraph |
|------|----------------|-----------|
| 客服 Triage | ✅ 自然 | 需要 conditional_edges |
| 升级链 | ✅ 自然 | 自然，更显式 |
| 并发分支 | 外层 asyncio.gather | ✅ 一等公民（fanout） |
| 状态机 | ❌ 弱 | ✅ 强 |
| 长流程 / Checkpoint | ❌ 弱 | ✅ 强 |
| Human-in-loop | handoff 到"人工" agent | ✅ interrupt() 一等公民 |

**结论**：handoff 适合"路由 + 转交"的扁平协作；状态机 / 复杂工作流 / 并发用 LangGraph。

---

## 8. 完整 demo：协作研究

```python
# demos/handoffs/04_complex.py
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
    model="gpt-4o-mini",
)


writer = Agent(
    name="Writer",
    instructions="""把素材写成 500 字报告。
- 引用源
- 平衡观点""",
    model="gpt-4o",
)


fact_checker = Agent(
    name="FactChecker",
    instructions="""检查报告事实准确性。
- 找 2-3 个可能问题
- 给出修改建议""",
    model="gpt-4o-mini",
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
    model="gpt-4o",
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
| 双向 handoff 死循环 | max_turns + instructions 加 "刚转过来别转回" |
| 升级链太长，用户失去耐心 | 设上限：超过 N 级直接给人工渠道 |
| 不同 Agent context 数据丢失 | 用 context 参数而不是塞 instructions |
| 并发跑 Agent trace 看不清 | 用 `with trace("pipeline")` 包一层 |

---

## 10. 下一步

- 📖 Input/Output Guardrails → [04-guardrails/01-input-guardrails.md](../04-guardrails/01-input-guardrails.md)
- 📖 Tracing 多 Agent → [05-advanced/01-tracing.md](../05-advanced/01-tracing.md)
- 📖 实战：完整 Triage 客服 → [08-practice/01-customer-triage.md](../08-practice/01-customer-triage.md)
