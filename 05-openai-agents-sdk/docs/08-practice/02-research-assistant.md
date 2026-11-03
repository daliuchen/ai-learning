# 实战 2：研究助手（Hosted Web Search + File Search + Critique）

> 这个实战用 OpenAI 的 WebSearchTool、FileSearchTool 加上几个协作的 Agent 搭一个研究助手：Planner 拆问题，Researcher 并发查资料，Writer 写报告，Critic 复核。
>
> `WebSearchTool` 和 `FileSearchTool` 只能在 OpenAI Responses 模型上用，第 5 节的 hosted 版本需要 OpenAI API key。只想看编排本身的话，第 8 节有一个本地替代版：用 `function_tool` 搜一个小语料库，Planner → Researcher ×N（并发）→ Writer → Critic 整条流水线只需要一个 OpenAI 兼容端点（比如千问）。

---

## 1. 需求

- 用户提一个研究问题（比如「AI Agent 框架现状」）
- 系统拆成若干 sub-questions
- 每个 sub-question 用 web_search 加内部知识库查资料
- 综合成 500-1000 字的报告，附引用来源
- 一个 Critic Agent 复核事实和观点是否平衡

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
Critic Agent          ← 复核 + 提改进（单独一次 Runner.run，不是 handoff）
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
    model="gpt-6-luna",
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
    model="gpt-6-luna",
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
    model="gpt-6.1-sol",
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
    model="gpt-6-luna",
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
        "total_tokens": sum(r.context_wrapper.usage.total_tokens for r in [plan_result, *research_results, writer_result, critic_result]),   # 注意用 context_wrapper.usage
    }
```

---

## 7. 完整 demo

```python
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

## 8. 本地替代版（不依赖 hosted tools）

把两个 hosted 工具换成本地的 `function_tool`，其他部分（结构化 `output_type`、并发的 Researcher、Writer、Critic）保持不变：

```python
import asyncio
from pydantic import BaseModel
from agents import Agent, Runner, function_tool

CORPUS = {
    "openai agents sdk": "OpenAI Agents SDK 以 Agents / Handoffs / Guardrails / Sessions / Tracing 为核心原语。https://openai.github.io/openai-agents-python/",
    "langgraph": "LangGraph 是基于图的有状态编排框架，支持检查点与 interrupt。https://langchain-ai.github.io/langgraph/",
    "pydantic ai": "Pydantic AI 以类型安全和多 provider 为特点。https://pydantic.dev/docs/ai/",
}


@function_tool
def local_search(query: str) -> str:
    """在本地语料库里搜资料（替代 web_search / file_search）。返回匹配的片段和 URL。"""
    hits = [v for k, v in CORPUS.items() if any(w in query.lower() for w in k.split())]
    return "\n".join(hits) or "no results"


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
    overall_score: int


planner = Agent(name="Planner", instructions="把问题拆成 2-3 个 sub-questions，输出 Plan。", output_type=Plan, model=MODEL)
researcher = Agent(name="Researcher", instructions="用 local_search 研究一个 sub-question，输出 Finding（sources 放 URL）。",
                   tools=[local_search], output_type=Finding, model=MODEL)
writer = Agent(name="Writer", instructions="综合 Findings 写 200 字以内的报告，输出 Report。", output_type=Report, model=MODEL)
critic = Agent(name="Critic", instructions="审查报告，给 1-10 分，列出 issues 和 suggestions，输出 Critique。", output_type=Critique, model=MODEL)


async def research(question: str) -> dict:
    plan = (await Runner.run(planner, question)).final_output
    results = await asyncio.gather(*[Runner.run(researcher, q) for q in plan.sub_questions])   # 并发
    findings = [r.final_output for r in results]
    text = "\n\n".join(f"## {f.sub_question}\n{f.summary}\nSources: {f.sources}" for f in findings)
    writer_result = await Runner.run(writer, f"原问题: {question}\n\n研究材料:\n{text}")
    report = writer_result.final_output
    critic_result = await Runner.run(critic, f"原问题: {question}\n\n报告:\n{report.answer}")
    all_results = [*results, writer_result, critic_result]
    return {
        "answer": report.answer,
        "critique": critic_result.final_output.model_dump(),
        "total_tokens": sum(r.context_wrapper.usage.total_tokens for r in all_results),
    }


async def main():
    out = await research("OpenAI Agents SDK、LangGraph、Pydantic AI 三个框架各自适合什么场景")
    print(out["answer"]); print("score:", out["critique"]["overall_score"], "tokens:", out["total_tokens"])


asyncio.run(main())
```

`MODEL` 就是 [安装与第一个 Agent](../01-basics/02-install-hello.md) 里定义的千问 `OpenAIChatCompletionsModel`。换回 OpenAI 时，把 `local_search` 替换成第 5 节的 `WebSearchTool` / `FileSearchTool`。

---

## 9. 迭代版本（带 max_iter critique loop）

Critic 打分低时，让 Writer 根据反馈自动改：

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

## 10. 部署：长任务 background

一次研究要跑 1-2 分钟，不能让 HTTP 请求一直挂着：

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

也可以用 Celery（见 [部署形态选型](../07-production/01-deployment.md)）。

---

## 11. 成本估算

| 阶段 | 模型 | Token（示意） | 估算 |
|------|------|-------|------|
| Plan | 小模型 | 500 | 很少 |
| Research × 5 | 小模型 + WebSearch×5 | 5000 + tool fee | 模型费用不高；WebSearch 按次计费，是大头 |
| Write | 大模型 | 3000 | 模型费用里最高的一项 |
| Critique | 小模型 | 800 | 很少 |
| 合计 | | | 以 OpenAI 当前价格页为准，一般 WebSearch 占大头 |

价格一直在变，这里只给结构，不给具体美元数。WebSearch 是最大的开销，想省钱可以调小 `search_context_size`，或者允许部分 sub-question 跳过搜索。

---

## 12. 优化

还有几处可以优化：相同的 sub-question 结果缓存 30 分钟；复杂问题用大模型，简单问题用小模型；Critic 预估分数高时可以跳过，直接给用户；每个 sub-research 完成时就推一条进度给前端（「找到了 3/5 个源...」），用户等待时不至于干等。

---

## 13. 评测

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
        "length": case.get("expected_length", (0, float("inf")))[0] <= len(result["answer"]) <= case.get("expected_length", (0, float("inf")))[1],
        "keywords": all(k.lower() in result["answer"].lower() for k in case["expected_keywords"]),
        # expected_balance 留给 LLM-as-judge 判断（见 07-production/05-evals）
    }
```

---

## 14. 下一步

- 语音助手 → [实战 3：语音助手（Realtime API）](./03-voice-assistant.md)
- Computer Use → [实战 4：Computer Use Agent（操作浏览器）](./04-computer-use.md)
- 横向对比 → [横向对比：OpenAI Agents SDK vs Pydantic AI vs LangGraph](./05-vs-others.md)
