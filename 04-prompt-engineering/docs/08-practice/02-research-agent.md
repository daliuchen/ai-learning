# PE Practice 02：Research Agent 的 5 版迭代史

> 这篇记录一个 research agent 从"会调搜索工具"到能上生产的五个版本。每一版都写清楚改了什么、为什么改、效果怎样。复杂 agent 的 prompt 是怎么一步步演化的，看完这五版会有直观的感觉。

---

## 1. 项目目标

做一个 research agent：给它一个研究问题，它自己搜资料、综合、标注来源，最后交一份报告。

工具：
- `search_web(query)` 搜索
- `fetch_url(url)` 读网页
- `python(code)` 计算 / 处理

目标输出：500-1000 字的研究报告，附引用来源。

---

## 2. v1：朴素 ReAct（基线）

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

用 5 个测试问题来看 v1 会出哪些问题（下表和后面各版的对比都是示意）：

| 问题 | 结果 |
|------|------|
| "2024 年全球电动车销量 top 5" | 部分对，没有引用来源 |
| "GPT-4 论文是哪年发的" | 调了 3 次工具仍然答错 |
| "斯坦福创校时间" | 搜一次就答对 |
| "Tesla 最新季度财报营收" | 用了过时数据，也没说明 |
| "如何评估 RAG 系统" | 列了一堆泛泛的观点，其实没去搜 |

暴露出来的问题有四个：没有引用来源，用户没法核实；工具乱调；输出长度忽长忽短；时效性问题用了过时数据。

---

## 3. v2：加引用和时间约束

```diff
+约束：
+- 每条 claim 后加 [Source: <url>] 引用
+- 时间敏感问题（"最新" / "2024" / "现在"）必须用 search_web
+- 输出长度 500-1000 字
+- 引用源必须从工具结果取，不要编 URL
+
+当前日期: {now}
```

同样 5 个问题，换成 v2 来看：

| 问题 | v1 | v2 |
|------|----|----|
| 电动车销量 | 部分对 | 对，有引用 |
| GPT-4 论文 | 失败 | 对，搜一次就找到 |
| 斯坦福创校 | 对 | 对 |
| Tesla 财报 | 数据过时 | 对，搜到了最新数据 |
| RAG 评估 | 泛泛而谈 | 有改进，但仍然不深 |

5 题里 4 题答对并带上了引用；RAG 评估那题有改进，但内容仍然不深。

---

## 4. v3：先规划再执行

```diff
+流程：
+1. **先规划**：在 <plan> 标签里列出 3-5 个 sub-questions
+2. 逐个 sub-question 搜资料、整理
+3. 综合写报告
+
+不要直接搜——先 plan。
```

改完以后，模型会先列计划，问题拆得很清楚，搜索更聚焦，报告也按子问题组织，结构好了很多。代价是平均成本从 $0.08 涨到 $0.11（见第 7 节），因为多了规划这一步，搜索次数也变多了。

---

## 5. v4：加自我检查

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

- 引用率从 85% 升到 95%
- 观点平衡明显变好，模型会主动补上"另一方面"
- 长度更稳定，critique 之后会主动精简
- 平均成本从 $0.11 涨到 $0.16（初稿、审查、修改三步）

---

## 6. v5：拆成子 agent

v4 在长任务上（搜索 10 次以上）容易跑偏，于是改了架构：

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

每个子 agent 有自己的 system prompt，短小、专注，也好维护。

效果：

- 通过率从 88% 升到 92%（25 题里从 22 题到 23 题），多通过的那题是涉及 10 个以上事实点的复杂问题
- 总成本反而下降了，因为 researcher 用 Haiku，只有 planner 和 writer 用 Sonnet
- 调试容易了，可以逐个看每个子 agent 的 trace

---

## 7. 五个版本的数据

下表是示意数据，用来说明各版本之间的变化方向和量级。通过率不是前面那 5 道题的结果，而是在另一份 25 条问题的评测集上统计的（通过数 / 25）；引用率按报告里的 claim 计，指带有效来源的 claim 占比；成本和延迟是每条问题的平均值。

| 版本 | 通过数 | 通过率 | 平均成本 | 平均延迟 | 引用率 | 备注 |
|------|--------|--------|-----------|--------------|--------|------|
| v1 | 10 | 40% | $0.05 | 8s | 30% | 基线，效果很差 |
| v2 | 16 | 64% | $0.08 | 12s | 80% | + 引用 + 时间约束 |
| v3 | 20 | 80% | $0.11 | 18s | 85% | + 先规划再执行 |
| v4 | 22 | 88% | $0.16 | 25s | 95% | + 自我检查 |
| v5 | 23 | 92% | $0.13 | 22s | 95% | + 子 agent（成本下降） |

---

## 8. v5 完整代码

```python
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
    "anthropic:claude-sonnet-5-5",
    output_type=Plan,
    instructions="""你把研究问题拆成 3-5 个 sub-questions。
每个 sub-question 应该独立、可被搜索回答。""",
)


# === Researcher（带工具）===
def search_web(query: str) -> list[dict]:
    # 实际接搜索 API
    return [{"title": "...", "url": "...", "snippet": "..."}]

researcher = Agent(
    "anthropic:claude-haiku-5-5",
    output_type=Finding,
    tools=[Tool(search_web)],
    instructions="""研究一个 sub-question。

流程：
1. search_web 找资料
2. 综合 100 字内
3. 列出引用 URL

输出 Finding 对象。""",
)


# === Writer（含 critique）===
writer = Agent(
    "anthropic:claude-sonnet-5-5",
    output_type=Report,
    instructions="""综合 findings 写报告 500-1000 字。

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

## 9. 设计决策回顾

| 决策 | 原因 |
|------|--------------|
| prompt 里强制引用来源 | 让结论可以核实 |
| 加规划步骤 | 复杂问题先拆成子问题 |
| 加自我检查 | 提高质量，避免观点一边倒 |
| 拆子 agent | 复杂任务更好观察，成本也降下来 |
| Researcher 用 Haiku | 单个子问题用不着 Sonnet |
| Writer 用 Sonnet | 综合写作对质量要求高 |

---

## 10. 回头看

复杂 agent 不要一上来就写一个大 prompt。我的做法是从最朴素的 ReAct 开始，看它在哪里出问题，再针对性地拆。

这五版里收益最大的一步，是强制引用来源，它是防幻觉最有效的手段。先规划再执行也比纯 ReAct 稳定。

拆子 agent 除了让工程结构更整洁，在这组示意数据里成本和质量也同时变好。v4 之后 prompt 已经写得比较完整，v5 没有再加约束，改的是调用结构：planner、researcher、writer 各用一段短 prompt，researcher 换成更便宜的 Haiku。通过率从 88% 升到 92%，平均成本从 $0.16 降到 $0.13。

---

## 11. 下一步

- [用 Claude Code 当 Prompt 优化器](./03-claude-code-as-optimizer.md)
- ReAct 基础：[ReAct（Reasoning + Acting）](../04-advanced/01-react.md)
- [Self-Critique（自我反思）](../03-techniques/08-self-critique.md)
- 多 agent 的完整写法见 LangChain 手册：[LangGraph 08：多 Agent 编排](../../../01-langchain/docs/03-langgraph/08-multi-agent.md)
