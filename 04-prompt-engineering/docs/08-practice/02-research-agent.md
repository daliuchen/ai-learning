# PE Practice 02：Research Agent 的 5 版迭代史

> **一句话**：从一个"会调搜索工具的 Agent"到生产级 research agent 的真实迭代史——5 个版本，每版讲清楚改了什么、为什么改、效果如何。读完你会建立"复杂 Agent prompt 怎么演化"的体感。

---

## 1. 项目目标

写一个 research agent：给定一个研究问题，agent 自己搜资料 → 综合 → 引用源 → 给报告。

工具：
- `search_web(query)` 搜索
- `fetch_url(url)` 读网页
- `python(code)` 计算 / 处理

目标输出：500-1000 字研究报告 + 引用源。

---

## 2. v1: 朴素 ReAct（baseline）

```python
SYSTEM_V1 = """你是研究助手。

可用工具:
- search_web(query)
- fetch_url(url)
- python(code)

任务：根据用户问题做研究，给出报告。

请按需调用工具，最后给出 500-1000 字答案。
"""
```

跑 5 个测试问题：

| 问题 | 结果 |
|------|------|
| "2024 年全球电动车销量 top 5" | 部分对，没引用源 |
| "GPT-4 论文是哪年发的" | 工具调了 3 次仍出错 |
| "斯坦福创校时间" | 1 个 search 就对 |
| "Tesla 最新季度财报营收" | 用了 outdated 数据没标 |
| "如何评估 RAG 系统" | 列了一堆通用观点，没真正搜 |

问题：
- 不引用源 → 用户不可验证
- 工具调用乱用
- 输出长度漂移
- 时间敏感问题用 stale 数据

---

## 3. v2: 加引用 + 时间约束

```diff
+约束：
+- 每条 claim 后加 [Source: <url>] 引用
+- 时间敏感问题（"最新" / "2024" / "现在"）必须用 search_web
+- 输出长度 500-1000 字
+- 引用源必须从工具结果取，不要编 URL
+
+当前日期: {now}
```

跑同样测试：

| 问题 | v1 | v2 |
|------|----|----|
| 电动车销量 | 部分对 | ✅ 有引用 |
| GPT-4 论文 | 失败 | ✅ 1 次 search 对 |
| 斯坦福创校 | 对 | ✅ 对 |
| Tesla 财报 | stale | ✅ search 最新 |
| RAG 评估 | 通用观点 | 改进，但仍不深 |

通过 5/5 但深度不够。

---

## 4. v3: 加 plan-then-execute

```diff
+流程：
+1. **先规划**：在 <plan> 标签里列出 3-5 个 sub-questions
+2. 逐个 sub-question 搜资料、整理
+3. 综合写报告
+
+不要直接搜——先 plan。
```

效果：

- 模型先列计划，分解清晰
- 搜索更聚焦
- 报告结构更好（按 sub-question 组织）
- 但 cost 涨 30%（多了 plan 步骤 + 多次 search）

---

## 5. v4: 加 self-critique

```diff
+生成报告后，必须做一次 self-review：
+1. 在 <draft> 写初版
+2. 在 <review> 列出 2-3 个改进点
+3. 在 <final> 给最终报告
+
+review 维度：
+- 准确性：是否所有 claim 都有引用？
+- 平衡性：是否单边？有 counter-view 吗？
+- 完整性：sub-questions 都答了吗？
```

效果：

- 引用率从 80% → 95%
- 平衡性显著提升（自动加 "另一方面"）
- 长度更稳定（critique 后会精简）
- cost 涨 40%（draft + critique + refine）

---

## 6. v5: 拆 sub-agent

发现 v4 在长任务（10+ search）容易跑偏：

```diff
+架构改成：
+- Main agent: 只做规划 + 整合
+- Researcher sub-agent: 每个 sub-question 独立调用
+- Writer sub-agent: 拼接 + critique
```

实现（Python）：

```python
async def research_agent(question: str):
    plan = await planner.plan(question)
    findings = []
    for sub_q in plan.sub_questions:
        finding = await researcher.research(sub_q)
        findings.append(finding)
    return await writer.compose(question, findings)
```

每个 sub-agent 有自己的 system prompt——聚焦、短、好维护。

效果：

- 复杂问题（10+ 个事实点）成功率 70% → 92%
- 总成本反而降（每个 sub-agent 用 mini，main 用 sonnet）
- 调试容易（看每个 sub-agent 的 trace）

---

## 7. 全 5 版数据汇总

| 版本 | 通过率 | 平均 cost | 平均 latency | 引用率 | 备注 |
|------|--------|-----------|--------------|--------|------|
| v1 | 40% | $0.05 | 8s | 30% | 基线，烂 |
| v2 | 65% | $0.08 | 12s | 80% | + 引用 + 时间 |
| v3 | 80% | $0.11 | 18s | 85% | + plan-then-execute |
| v4 | 88% | $0.16 | 25s | 95% | + critique |
| v5 | 92% | $0.13 | 22s | 95% | + sub-agent（cost 降！）|

---

## 8. 完整 v5 代码

```python
# demos/practice/02_research_agent.py
"""Research Agent v5 - 多 sub-agent 架构"""
from pydantic_ai import Agent, Tool
from pydantic import BaseModel


class Plan(BaseModel):
    sub_questions: list[str]


class Finding(BaseModel):
    sub_question: str
    summary: str
    sources: list[str]


class Report(BaseModel):
    answer: str
    sources_used: list[str]


# === Planner ===
planner = Agent(
    "anthropic:claude-sonnet-4-6",
    output_type=Plan,
    system_prompt="""你把研究问题拆成 3-5 个 sub-questions。
每个 sub-question 应该独立、可被搜索回答。""",
)


# === Researcher（带工具）===
def search_web(query: str) -> list[dict]:
    # 实际接搜索 API
    return [{"title": "...", "url": "...", "snippet": "..."}]

researcher = Agent(
    "anthropic:claude-haiku-4-5-20251001",
    output_type=Finding,
    tools=[Tool(search_web)],
    system_prompt="""研究一个 sub-question。

流程：
1. search_web 找资料
2. 综合 100 字内
3. 列出引用 URL

输出 Finding 对象。""",
)


# === Writer（含 critique）===
writer = Agent(
    "anthropic:claude-sonnet-4-6",
    output_type=Report,
    system_prompt="""综合 findings 写报告 500-1000 字。

约束：
- 每条 claim 加 [Source: url]
- 平衡观点（如适用，加 "另一方面"）
- 末尾列出 used sources

最后做一次 self-review，调整不准 / 单边内容。""",
)


async def research(question: str) -> Report:
    plan_result = await planner.run(question)
    
    findings = []
    for sub_q in plan_result.output.sub_questions:
        f_result = await researcher.run(sub_q)
        findings.append(f_result.output)
    
    # 整合 findings 给 writer
    findings_text = "\n\n".join(
        f"## {f.sub_question}\n{f.summary}\nSources: {f.sources}"
        for f in findings
    )
    
    final = await writer.run(
        f"问题: {question}\n\nFindings:\n{findings_text}"
    )
    return final.output
```

---

## 9. 关键设计决策回顾

| 决策 | 为啥这么决定 |
|------|--------------|
| 引用源进 prompt | 让模型可验证 |
| Plan 步骤 | 复杂问题拆 sub-question |
| Critique | 提质量 + 防单边 |
| Sub-agent 架构 | 复杂任务可观测、cost 优化 |
| Researcher 用 Haiku | 单 sub-question 不需要 Sonnet |
| Writer 用 Sonnet | 综合 + 质量优 |

---

## 10. 教训总结

1. **复杂 Agent 别一上来用单 prompt**：从 ReAct 开始，看哪里挂再拆
2. **引用 / source 强制是防幻觉的最强工具**
3. **plan-then-execute 比纯 ReAct 稳**
4. **拆 sub-agent 不只是工程化——cost / quality 反而双优**
5. **complex prompt 调到一定程度，瓶颈是 architecture 不是 prompt**

---

## 11. 下一步

- 📖 Claude Code 当 prompt 优化器 → [03-claude-code-as-optimizer.md](./03-claude-code-as-optimizer.md)
- 📖 ReAct 基础 → [04-advanced/01-react.md](../04-advanced/01-react.md)
- 📖 Self-critique → [03-techniques/08-self-critique.md](../03-techniques/08-self-critique.md)
- 📖 跨手册：LangGraph 多 agent → ../../../01-langchain/docs/03-langgraph/08-multi-agent.md
