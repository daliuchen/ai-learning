# 实战 2：研究助手（Hosted Web Search + File Search + Critique）

> **一句话**：用 OpenAI 的 WebSearchTool + FileSearchTool + 多 Agent 协作搭一个研究助手——主 Agent 规划、子 Agent 搜资料、Critic 复核。

---

## 1. 需求

- 用户问研究问题（"AI Agent 框架现状"）
- 系统拆 sub-questions
- 每个 sub-question 用 web_search + 内部知识库
- 综合成 500-1000 字报告 + 引用源
- 一个 Critic Agent 复核事实和平衡

---

## 2. 架构

```
User Question
   ↓
Planner Agent          ← 拆 sub-questions
   ↓
For each sub-q:
   Researcher Agent    ← WebSearchTool + FileSearchTool
   ↓
Writer Agent           ← 综合报告
   ↓
Critic Agent (handoff) ← 复核 + 提改进
   ↓
最终 Report
```

---

## 3. 准备：Vector Store

```python
from openai import OpenAI


client = OpenAI()


# 1. 创建 Vector Store（一次性）
vs = client.vector_stores.create(name="company-research-kb")
print(f"VS ID: {vs.id}")  # 记下来 vs_xxx


# 2. 上传内部资料
for file_path in ["./internal_docs/whitepaper.pdf", "./internal_docs/api_ref.md"]:
    with open(file_path, "rb") as f:
        client.vector_stores.file_batches.upload_and_poll(
            vector_store_id=vs.id,
            files=[f],
        )
```

---

## 4. Output types

```python
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


class Critique(BaseModel):
    issues: list[str]
    suggestions: list[str]
    overall_score: int  # 1-10
```

---

## 5. Agents

```python
from agents import Agent, Runner, WebSearchTool, FileSearchTool


VS_ID = "vs_xxx"  # 你的 vector store


# 1. Planner
planner = Agent(
    name="Planner",
    instructions="""你把用户研究问题拆成 3-5 个 sub-questions。

要求：
- 每个 sub-question 独立、可被搜索回答
- 覆盖问题的不同角度
- 用 100 字以内描述每个

输出 Plan。
""",
    output_type=Plan,
    model="gpt-4o-mini",
)


# 2. Researcher
researcher = Agent(
    name="Researcher",
    instructions="""你研究一个 sub-question。

工作流：
1. 用 web_search 找 2-3 个外部源
2. 用 file_search 查内部资料（若相关）
3. 综合成 200 字 summary
4. 列出引用 URL / 文档名

输出 Finding。
""",
    tools=[
        WebSearchTool(search_context_size="medium"),
        FileSearchTool(vector_store_ids=[VS_ID], max_num_results=3),
    ],
    output_type=Finding,
    model="gpt-4o-mini",
)


# 3. Writer
writer = Agent(
    name="Writer",
    instructions="""你综合多个 Findings 写报告。

要求：
- 500-1000 字
- 每个 claim 带 [Source: ...] 引用
- 平衡观点（适合时加 "另一方面"）
- 末尾列出 sources_used
""",
    output_type=Report,
    model="gpt-4o",
)


# 4. Critic
critic = Agent(
    name="Critic",
    instructions="""你审查研究报告。

检查维度：
- 准确性：所有 claim 是否都有引用？
- 平衡：是否单边？
- 完整性：是否答了原问题？
- 时效性：sources 是否够新？

给 1-10 分，列出 2-3 个 issues 和 suggestions。
""",
    output_type=Critique,
    model="gpt-4o-mini",
)
```

---

## 6. 编排

```python
import asyncio


async def research(question: str) -> dict:
    # 1. Plan
    plan_result = await Runner.run(planner, question)
    plan: Plan = plan_result.final_output

    print(f"\n📋 Plan: {len(plan.sub_questions)} sub-questions")
    for i, q in enumerate(plan.sub_questions, 1):
        print(f"  {i}. {q}")

    # 2. Research parallel
    print("\n🔍 Researching...")
    research_tasks = [
        Runner.run(researcher, sub_q) for sub_q in plan.sub_questions
    ]
    research_results = await asyncio.gather(*research_tasks)
    findings: list[Finding] = [r.final_output for r in research_results]

    # 3. Write
    print("\n📝 Writing report...")
    findings_text = "\n\n".join(
        f"## {f.sub_question}\n{f.summary}\nSources: {f.sources}"
        for f in findings
    )
    writer_input = f"原问题: {question}\n\n研究材料:\n{findings_text}"
    writer_result = await Runner.run(writer, writer_input)
    report: Report = writer_result.final_output

    # 4. Critique
    print("\n🔬 Critiquing...")
    critic_input = f"原问题: {question}\n\n报告:\n{report.answer}\n\nSources: {report.sources_used}"
    critic_result = await Runner.run(critic, critic_input)
    critique: Critique = critic_result.final_output

    return {
        "answer": report.answer,
        "sources": report.sources_used,
        "critique": critique.model_dump(),
        "total_tokens": sum(r.usage.total_tokens for r in [plan_result, *research_results, writer_result, critic_result]),
    }
```

---

## 7. 完整 demo

```python
# demos/practice/02_research.py
import asyncio
from agents import Agent, Runner, WebSearchTool, trace
# (上面 Agent 定义略)


async def main():
    question = "AI Agent 框架的现状（2026 年）"

    with trace(
        "Research Pipeline",
        metadata={"question": question, "version": "v1"},
    ):
        result = await research(question)

    print("\n" + "=" * 60)
    print("📄 REPORT")
    print("=" * 60)
    print(result["answer"])
    print("\n🔗 SOURCES:")
    for s in result["sources"]:
        print(f"  - {s}")
    print(f"\n📊 Critique score: {result['critique']['overall_score']}/10")
    print(f"💰 Total tokens: {result['total_tokens']}")


asyncio.run(main())
```

---

## 8. 迭代版本（带 max_iter critique loop）

如果 critique 分数低，自动改进：

```python
async def research_with_iter(question: str, max_iter=2):
    # 第 1 轮
    result = await research(question)

    iter_count = 0
    while result["critique"]["overall_score"] < 7 and iter_count < max_iter:
        print(f"\n🔄 Score {result['critique']['overall_score']} 太低，迭代...")

        # 让 writer 基于 critique 改
        improvement_input = f"""原报告:
{result['answer']}

Critic 反馈:
- Issues: {result['critique']['issues']}
- Suggestions: {result['critique']['suggestions']}

请改进。
"""
        writer_result = await Runner.run(writer, improvement_input)
        improved_report: Report = writer_result.final_output

        # 重新 critique
        critic_input = f"原问题: {question}\n\n报告:\n{improved_report.answer}"
        critic_result = await Runner.run(critic, critic_input)
        new_critique: Critique = critic_result.final_output

        result = {
            "answer": improved_report.answer,
            "sources": improved_report.sources_used,
            "critique": new_critique.model_dump(),
        }
        iter_count += 1

    return result
```

---

## 9. 部署：长任务 background

研究跑 1-2 分钟，不能在 HTTP request 等：

```python
from fastapi import FastAPI, BackgroundTasks
from uuid import uuid4


app = FastAPI()
_tasks = {}


@app.post("/research")
async def start(question: str, bg: BackgroundTasks):
    task_id = str(uuid4())
    _tasks[task_id] = {"status": "running"}

    async def run():
        result = await research(question)
        _tasks[task_id] = {"status": "done", "result": result}

    bg.add_task(run)
    return {"task_id": task_id}


@app.get("/research/{task_id}")
async def check(task_id: str):
    return _tasks.get(task_id, {"status": "not_found"})
```

或用 Celery（详见 [07-production/01-deployment.md](../07-production/01-deployment.md)）。

---

## 10. 成本估算

| 阶段 | 模型 | Token | 估算 |
|------|------|-------|------|
| Plan | mini | 500 | $0.0003 |
| Research × 5 | mini + WebSearch×5 | 5000 + tool fee | $0.003 + $0.125 |
| Write | 4o | 3000 | $0.045 |
| Critique | mini | 800 | $0.0005 |
| **合计** | | | **~$0.17** |

WebSearch 是最大头。要省：用更小 `search_context_size` 或允许部分子 q 跳过 search。

---

## 11. 优化

- **缓存**：相同 sub-question 跑过的 cache 30 分钟
- **降级**：复杂问题用 4o，简单用 mini
- **跳过 Critic**：score 估计高的直接给用户
- **流式**：把每个 sub-research 完成时推给前端（"找到了 3/5 个源..."）

---

## 12. 评测

```python
evalset = [
    {
        "input": "Python async 入门",
        "expected_sources_min": 3,
        "expected_length": (400, 1200),
        "expected_keywords": ["async", "await", "asyncio"],
    },
    {
        "input": "AI Agent 框架对比",
        "expected_keywords": ["LangChain", "OpenAI", "Pydantic"],
        "expected_balance": True,  # 必须提及多家
    },
]


for case in evalset:
    result = await research(case["input"])
    checks = {
        "sources": len(result["sources"]) >= case.get("expected_sources_min", 0),
        "length": case["expected_length"][0] <= len(result["answer"]) <= case["expected_length"][1],
        "keywords": all(k.lower() in result["answer"].lower() for k in case["expected_keywords"]),
    }
```

---

## 13. 下一步

- 📖 语音助手 → [03-voice-assistant.md](./03-voice-assistant.md)
- 📖 Computer Use → [04-computer-use.md](./04-computer-use.md)
- 📖 横向对比 → [05-vs-others.md](./05-vs-others.md)
