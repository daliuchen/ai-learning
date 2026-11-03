# Pydantic AI 实战 03：多 Agent 研究助手（Researcher + Writer + Reviewer）

> 这个项目用 Pydantic Graph 编排三个 Agent：Researcher 找资料，Writer 起草，Reviewer 打分，分数不够就退回 Writer 修订，直到 Reviewer 放行。状态是带类型的 dataclass，可以自己序列化保存，每一步都能在 Logfire 里看到。

---

## 1. 项目目标

输入：一个研究主题（如 "Pydantic AI 的核心特性"）

输出：一份带引用的结构化研究报告：

```
1. 概述（150 字）
2. 核心特性（bullet）
3. 与其他框架对比
4. 适用场景
5. 参考资料（urls / 文档）
```

行为：

- Researcher 用搜索工具收集资料
- Writer 把资料组织成报告
- Reviewer 给报告打分 (0-10) + 提出修改意见
- 如果 score < 8，回到 Writer 修订，最多迭代 3 次
- 可选：在 Reviewer 前加人工审核（见 §9）

技术栈：

```
Pydantic AI    ← 三个 Agent
pydantic-graph ← 编排 + 状态机
DuckDuckGo     ← 搜索工具（不用 API key）
Logfire        ← 可观测
```

---

## 2. 架构图

```
                      ┌────────────────────────┐
                      │  ResearchState         │
                      │  (整个图的状态)         │
                      └────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        ▼                         ▼                         ▼
 ┌────────────┐           ┌────────────┐            ┌─────────────┐
 │ Research   │ ───────►  │   Write    │ ───────►   │   Review    │
 │ (调搜索)   │           │ (起草报告)  │            │ (打分 0-10) │
 └────────────┘           └────────────┘            └─────────────┘
                                  ▲                         │
                                  │                         │
                                  │   score < 8 / 未到上限  │
                                  └──────── Revise ────────┘
                                                            │
                                                            │ score >= 8
                                                            ▼
                                                       ┌────────┐
                                                       │  End   │
                                                       └────────┘
```

State：

```python
@dataclass
class ResearchState:
    topic: str
    research: str = ""            # Researcher 产物
    report: ResearchReport | None = None  # Writer 产物
    reviews: list[Review] = field(default_factory=list)  # 历次 Review
    iteration: int = 0
    max_iter: int = 3
```

---

## 3. 三个业务模型

```python
# research/models.py
from pydantic import BaseModel, Field

class ResearchReport(BaseModel):
    title: str
    overview: str = Field(description="150 字概述")
    features: list[str] = Field(description="核心特性 bullet")
    comparison: str = Field(description="与其他框架对比，<= 300 字")
    use_cases: list[str] = Field(description="适用场景")
    references: list[str] = Field(description="参考资料 url 或 文档")

class Review(BaseModel):
    score: int = Field(description="0-10 分", ge=0, le=10)
    pros: list[str] = Field(description="做得好的点", default_factory=list)
    issues: list[str] = Field(description="问题清单", default_factory=list)
    suggestions: str = Field(description="给 Writer 的修改建议")
```

---

## 4. Researcher Agent

```python
# research/researcher.py
from pydantic_ai import Agent, RunContext
from dataclasses import dataclass

@dataclass
class ResearcherDeps:
    search_max_results: int = 5

researcher = Agent[ResearcherDeps, str](
    "openai-chat:qwen-flash",
    deps_type=ResearcherDeps,
    output_type=str,   # 直接输出研究笔记
    instructions=(
        "你是一名资深研究员。给定主题，调用 search 工具收集 3-5 条最有价值的资料，"
        "然后整理成研究笔记（800 字以内）：列出核心要点和关键 url，"
        "注意识别相互矛盾的信息。"
    ),
)

@researcher.tool
async def search(ctx: RunContext[ResearcherDeps], query: str) -> list[dict]:
    """用 DuckDuckGo 搜索（无需 API key）。"""
    from ddgs import DDGS      # 包名已从 duckduckgo_search 改为 ddgs
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=ctx.deps.search_max_results))
    return [{"title": r["title"], "url": r["href"], "snippet": r["body"]}
            for r in results]
```

> 真实项目可以换 Tavily / Serper，结果质量更好。

---

## 5. Writer Agent

```python
# research/writer.py
from pydantic_ai import Agent
from research.models import ResearchReport, Review

writer = Agent(
    "openai-chat:qwen-flash",
    output_type=ResearchReport,
    instructions=(
        "你是一名技术作家。基于提供的研究笔记起草结构化报告。"
        "如果有审稿意见（Review），按建议修订；务必引用研究笔记里出现的 url。"
    ),
)
```

注意 `output_type=ResearchReport`：Writer 不需要工具，只负责把笔记重组进 schema。

---

## 6. Reviewer Agent

```python
# research/reviewer.py
from pydantic_ai import Agent
from research.models import Review

reviewer = Agent(
    "openai-chat:qwen-flash",
    output_type=Review,
    instructions=(
        "你是一名严苛的总编。对提供的报告打分（0-10），列 pros / issues，"
        "并给出可操作的修改建议。评分标准：\n"
        "  - 准确性：是否有事实错误\n"
        "  - 完整性：必备字段是否齐\n"
        "  - 引用：是否有 references\n"
        "  - 可读性：结构是否清晰\n"
        "分数 >=8 才能放行。"
    ),
)
```

---

## 7. 用 Pydantic Graph 编排

Pydantic Graph 的基本规则：

- 节点是继承 `BaseNode` 的 dataclass
- `run` 方法返回下一个节点或 `End`
- 状态在 `GraphRunContext.state` 上

### 7.1 节点定义

```python
# research/graph.py
from __future__ import annotations
from dataclasses import dataclass, field
from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext, StepContext

from research.models import ResearchReport, Review
from research.researcher import researcher, ResearcherDeps
from research.writer import writer
from research.reviewer import reviewer

@dataclass
class ResearchState:
    topic: str
    research: str = ""
    report: ResearchReport | None = None
    reviews: list[Review] = field(default_factory=list)
    iteration: int = 0
    max_iter: int = 3


# ---- 节点 1：Research ----
@dataclass
class Research(BaseNode[ResearchState]):
    async def run(self, ctx: GraphRunContext[ResearchState]) -> Write:
        result = await researcher.run(
            f"研究主题：{ctx.state.topic}",
            deps=ResearcherDeps(search_max_results=5),
        )
        ctx.state.research = result.output
        return Write()


# ---- 节点 2：Write ----
@dataclass
class Write(BaseNode[ResearchState]):
    async def run(self, ctx: GraphRunContext[ResearchState]) -> Review_:
        ctx.state.iteration += 1
        prompt = f"主题：{ctx.state.topic}\n\n研究笔记：\n{ctx.state.research}"
        if ctx.state.reviews:
            last = ctx.state.reviews[-1]
            prompt += (
                f"\n\n上一轮 Reviewer 意见（请修订）：\n"
                f"score={last.score}\nissues={last.issues}\n"
                f"建议={last.suggestions}\n\n"
                f"上一稿：{ctx.state.report.model_dump_json(indent=2) if ctx.state.report else ''}"
            )
        result = await writer.run(prompt)
        ctx.state.report = result.output
        return Review_()


# ---- 节点 3：Review ----
# 类名加下划线避免和 Review BaseModel 冲突
@dataclass
class Review_(BaseNode[ResearchState, None, ResearchReport]):
    async def run(
        self, ctx: GraphRunContext[ResearchState]
    ) -> Write | End[ResearchReport]:
        assert ctx.state.report is not None
        prompt = (
            f"请评审以下报告：\n"
            f"{ctx.state.report.model_dump_json(indent=2)}"
        )
        result = await reviewer.run(prompt)
        review = result.output
        ctx.state.reviews.append(review)
        print(f"  📝 Iteration {ctx.state.iteration}: score={review.score}")

        if review.score >= 8:
            return End(ctx.state.report)
        if ctx.state.iteration >= ctx.state.max_iter:
            print(f"  ⚠️  达到最大迭代 {ctx.state.max_iter}，强制结束")
            return End(ctx.state.report)
        return Write()


# 新版 pydantic-graph：GraphBuilder 装配 → build() 得到可执行的 Graph
g = GraphBuilder(state_type=ResearchState, input_type=None, output_type=ResearchReport)

@g.step
async def start(ctx: StepContext[ResearchState, None, None]) -> Research:
    return Research()

g.add(
    g.node(Research),
    g.node(Write),
    g.node(Review_),
    g.edge_from(g.start_node).to(start),
)
graph = g.build()
```

### 7.2 几个细节

1. 节点签名：`BaseNode[StateT, DepsT, RunEndT]`。`Review_` 是出口节点，泛型第 3 位写最终返回类型 `ResearchReport`。
2. `run` 的返回类型决定边：`Write | End[ResearchReport]` 表示既能去 Write 又能 End。
3. 状态在 `ctx.state` 上，直接 mutate，不像 LangGraph 要 `return {"key": val}`。
4. 节点是 dataclass，可以带参数（如 `Write(retry_count=0)`），构造时传入。

### 7.3 跑图

```python
# research/run.py
import asyncio
from research.graph import graph, ResearchState

async def main(topic: str):
    state = ResearchState(topic=topic, max_iter=3)
    output = await graph.run(state=state)          # 直接返回 End(...) 里的 ResearchReport
    print("\n" + "=" * 60)
    print("FINAL REPORT")
    print("=" * 60)
    print(output.model_dump_json(indent=2))
    print(f"\n经过 {state.iteration} 轮迭代，{len(state.reviews)} 次评审。")

if __name__ == "__main__":
    asyncio.run(main("Pydantic AI 的核心特性"))
```

`graph.run(state=state)` 从 builder 里连到 `start_node` 的入口 step 开始，这里入口返回 `Research()`。它的返回值就是最终输出，即 `End(...)` 里的 `ResearchReport`；运行过程中 state 被原地修改。

正常情况下 Researcher 搜资料写笔记，Writer 起草，Reviewer 打分够了就直接放行。没有 key 时三个 Agent 用 `test` 模型，Reviewer 恒给 0 分，图会循环到 `max_iter` 后强制结束。

---

## 8. 进阶 1：并行 Researcher

如果想让多个 researcher 并发查不同维度（概念、对比、案例），可以在 `Research` 节点里用 `asyncio.gather`：

```python
@dataclass
class Research(BaseNode[ResearchState]):
    async def run(self, ctx: GraphRunContext[ResearchState]) -> Write:
        topics = [
            f"{ctx.state.topic} 是什么",
            f"{ctx.state.topic} 与其他框架对比",
            f"{ctx.state.topic} 实战案例",
        ]
        results = await asyncio.gather(*[
            researcher.run(t, deps=ResearcherDeps())
            for t in topics
        ])
        ctx.state.research = "\n\n---\n\n".join(r.output for r in results)
        return Write()
```

三路并行，整体时延约等于最慢的那一路，而不是三路相加。

---

## 9. 进阶 2：人工审核节点（HITL）

在 Reviewer 之前插入一个人工审核节点：

```python
@dataclass
class HumanApprove(BaseNode[ResearchState, None, ResearchReport]):   # 能返回 End，第 3 位要写输出类型
    async def run(self, ctx: GraphRunContext[ResearchState]) -> Write | End[ResearchReport]:
        assert ctx.state.report is not None
        print("\n[等待人工审核]")
        print(ctx.state.report.model_dump_json(indent=2))
        ans = input("\n通过？(y/n/edit) > ").strip()
        if ans == "y":
            return End(ctx.state.report)
        if ans == "edit":
            # 简化：直接重新让 Writer 改
            return Write()
        return End(ctx.state.report)  # 其它输入按拒绝处理，同样结束（实际项目里在 state 里记一笔）
```

把 `Write` 节点末尾改成返回 `HumanApprove()`（返回注解也改成 `-> HumanApprove`，并用 `g.node(HumanApprove)` 登记），由人决定直接放行还是退回 Writer 修改。想保留 Reviewer，就让 `HumanApprove` 在通过时返回 `Review_()` 而不是 `End`。

Pydantic Graph 还没有 LangGraph 那种开箱即用的 `interrupt()` / `Command(resume=...)`。想把状态存下来、等用户回来再继续，需要：

1. 把状态序列化后存数据库。`ResearchState` 是 dataclass，没有 `model_dump_json()`。它里面又嵌着 Pydantic 模型（`report`、`reviews`），直接 `json.dumps(dataclasses.asdict(state))` 会报不可序列化。要先把这几个字段 `model_dump()`，写法见下一节
2. Web 端拿用户回复后反序列化继续

---

## 10. 进阶 3：状态持久化

pydantic-graph 2.x 没有内置持久化（旧的 `pydantic_graph.persistence` 已移除）。state 是普通 dataclass，自己序列化存起来就行；要做到进程崩了能自动续跑，就得上持久执行引擎（Temporal / DBOS 等）。手动保存的写法：

```python
# 暂停时
import json
serialized = json.dumps({
    "topic": state.topic,
    "research": state.research,
    "report": state.report.model_dump() if state.report else None,
    "reviews": [r.model_dump() for r in state.reviews],
    "iteration": state.iteration,
})
db.save("session-123", serialized)

# 恢复时
raw = json.loads(db.load("session-123"))
state = ResearchState(
    topic=raw["topic"],
    research=raw["research"],
    report=ResearchReport(**raw["report"]) if raw["report"] else None,
    reviews=[Review(**r) for r in raw["reviews"]],
    iteration=raw["iteration"],
)
# 从 Write 节点继续（如果上次停在 review 之前）：
# builder 的入口是固定的 start step，想从中间节点恢复，就在入口 step 里按 state 决定返回哪个节点，
# 例如 start 里 `return Write() if ctx.state.research else Research()`
output = await graph.run(state=state)
```

---

## 11. 与 LangGraph 等价对比

LangGraph 写法（核心节选）：

```python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

class State(TypedDict):
    topic: str
    research: str
    report: dict | None
    reviews: list
    iteration: int

def research_node(s):
    out = researcher.invoke({"messages": [...]})
    return {"research": out["messages"][-1].content}

def write_node(s):
    out = writer.invoke({...})
    return {"report": out, "iteration": s["iteration"] + 1}

def review_node(s):
    out = reviewer.invoke({...})
    return {"reviews": s["reviews"] + [out]}

def router(s):
    if s["reviews"][-1]["score"] >= 8 or s["iteration"] >= 3:
        return END
    return "write"

g = StateGraph(State)
g.add_node("research", research_node)
g.add_node("write", write_node)
g.add_node("review", review_node)
g.add_edge(START, "research")
g.add_edge("research", "write")
g.add_edge("write", "review")
g.add_conditional_edges("review", router, {"write": "write", END: END})
app = g.compile(checkpointer=InMemorySaver())   # from langgraph.checkpoint.memory import InMemorySaver
```

| 维度 | LangGraph | Pydantic Graph |
|------|-----------|----------------|
| State 类型 | `TypedDict` + `Annotated[..., reducer]` | `@dataclass`，直接 mutate |
| 节点 | 普通函数，返回 `dict` | 继承 `BaseNode`，返回下一节点实例 |
| 边 | `add_edge` / `add_conditional_edges` 显式 | 返回类型注解隐式确定 |
| Checkpointer | `SqliteSaver` / `PostgresSaver` 一行接入 | 需要自己实现序列化 |
| Studio 调试 | `langgraph dev` 可视化、可改 state 重跑 | 暂无对应工具 |
| HITL | `interrupt()` + `Command(resume=...)` 内置 | 自己拼 |
| 类型检查 | TypedDict 弱（list 加 reducer 麻烦） | dataclass + 泛型节点，IDE 友好 |
| 适合 | 大型生产工作流（HITL / 持久化重型） | 中型工作流 + 类型严谨 |

我的选择标准：人工介入和长会话持久化是硬需求，用 LangGraph；更看重类型安全、和 Pydantic 生态配合，用 Pydantic Graph。两者也可以一起用，LangGraph 做编排骨架，节点内部调 Pydantic AI Agent。

---

## 12. 接 Logfire

```python
import logfire
logfire.configure(service_name="research-agent")
logfire.instrument_pydantic_ai()
```

跑完一次后，Logfire 里一次图运行下面按顺序排着各节点里的 agent run（结构示意，具体 span 名以实际为准）：

```
图运行
├── Research   → researcher 的 agent run
│     ├── search(query="...")
│     └── search(query="...")
├── Write      → writer 的 agent run
├── Review_    → reviewer 的 agent run（score=7）
├── Write      → writer 的 agent run（修订）
└── Review_    → reviewer 的 agent run（score=9 → End）
```

每一步的 token、时延和错误都能看到。

---

## 13. 部署

### 13.1 CLI 工具

```python
# research/cli.py
import asyncio
import argparse
from research.graph import graph, ResearchState, Research

def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--max-iter", type=int, default=3)
    ap.add_argument("--out", default="report.json")
    args = ap.parse_args(argv)     # argv=None 时读命令行，定时任务里可以直接传列表

    state = ResearchState(topic=args.topic, max_iter=args.max_iter)
    output = asyncio.run(graph.run(state=state))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(output.model_dump_json(indent=2, exclude_none=True))
    print(f"✅ 保存到 {args.out}（{state.iteration} 轮）")

if __name__ == "__main__":
    main()
```

```bash
python -m research.cli "Pydantic AI 核心特性" --out report.json
```

### 13.2 定时任务 + 邮件分发

```python
# cron: 每周一 9:00
# 0 9 * * 1 python -m research.scheduler

import datetime
from research.cli import main as run_cli
from research.email_send import send_report

TOPICS = ["LLM 安全最新进展", "Agent 框架周报"]

for topic in TOPICS:
    out_path = f"reports/{topic}-{datetime.date.today()}.json"
    run_cli([topic, "--out", out_path])
    send_report(to="team@xxx.com", path=out_path)
```

---

## 14. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 无限循环 | Reviewer 永远不给 8 分 | 加 `max_iter` 上限 + 强制 End |
| Writer 不引用 url | instructions 没明确要求 | 强化 prompt：每条 feature 后加 `(参考: url)` |
| Researcher 直接答而不调 search | 模型跳过了工具 | 在 prompt 里写明禁止凭印象回答，并检查工具调用次数 |
| 状态字段类型不对 | dataclass 默认值用了可变对象 | 用 `field(default_factory=list)` 而不是 `[]` |
| 多个 dataclass 节点同名冲突 | Python 类名重复 | 节点类加后缀（如 `Review_`） |
| `End[T]` 类型推断不出来 | 节点泛型第 3 位没填 | `BaseNode[State, None, ReportType]` |
| DuckDuckGo 经常超时 | 公网搜索限流 | 用 Tavily / Serper / 自建 |
| Pydantic Evals 跑研究 Agent 慢 | 每条用例都跑整个图 | 评测时用 TestModel 模拟 + 只测一两个关键节点 |
| Logfire 看不到子 Agent | 没调 `instrument_pydantic_ai()` | 加上 |

---

## 15. 工程清单

- [ ] 把 DuckDuckGo 换成 Tavily / Serper（结果质量会好很多）
- [ ] 加搜索结果去重 + url 白名单
- [ ] `max_iter` 触顶时给用户发降级通知
- [ ] State 序列化进 Redis（实现跨进程恢复）
- [ ] 报告输出 Markdown / HTML 双格式
- [ ] Logfire metric：平均迭代数、平均时延、平均 token
- [ ] 每个 Agent 单独控制 model（比如 Researcher 用 `gpt-6-luna`，Reviewer 用 `claude-opus-5-5`）
- [ ] 引入 fact-check 节点（拿 references 反查 Researcher 笔记一致性）
- [ ] 单元测试：用 `TestModel` 模拟每个 Agent 的输出，跑通整图

---

## 16. 项目目录

```
research-agent/
├── research/
│   ├── __init__.py
│   ├── models.py
│   ├── researcher.py
│   ├── writer.py
│   ├── reviewer.py
│   ├── graph.py
│   ├── cli.py
│   └── scheduler.py
├── reports/                  # 输出目录
├── tests/
│   └── test_graph.py
└── requirements.txt
```

---

## 17. 完整 demo

单文件可跑版本：[`demos/practice/03_project_research.py`](../../demos/practice/03_project_research.py)。

```bash
pip install pydantic-ai pydantic-graph ddgs python-dotenv
export OPENAI_API_KEY=...
python demos/practice/03_project_research.py
```

按第 7 节的代码运行，输出大致如下（demo 文件在每个节点多打了几行进度日志，结构相同）：

```
  📝 Iteration 1: score=7
  📝 Iteration 2: score=9

============================================================
FINAL REPORT
============================================================
{
  "title": "Pydantic AI 核心特性概览",
  "overview": "...",
  "features": ["类型安全", "依赖注入", "..."],
  ...
}

经过 2 轮迭代，2 次评审。
```

---

下一篇：[Pydantic AI 实战 04：自定义 MCP 工具服务（GitHub Issue 查询）](04-project-mcp-server.md)。
