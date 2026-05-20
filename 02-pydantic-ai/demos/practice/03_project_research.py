"""
实战 3：多 Agent 研究助手（Researcher + Writer + Reviewer，用 Pydantic Graph 编排）。

运行：
    pip install pydantic-ai pydantic-graph duckduckgo-search python-dotenv
    export OPENAI_API_KEY=...
    python demos/practice/03_project_research.py "Pydantic AI 的核心特性"

或者不带参数会用默认主题。

行为：
    1. Researcher Agent 用 DuckDuckGo 搜资料
    2. Writer Agent 起草结构化报告
    3. Reviewer Agent 打分；< 8 分回到 Writer 修订（最多 3 轮）
    4. 输出最终报告到控制台 + report.json
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_graph import BaseNode, End, Graph, GraphRunContext

load_dotenv()


# ============================================================
# 1) 业务模型
# ============================================================

class ResearchReport(BaseModel):
    title: str
    overview: str = Field(description="150 字以内概述")
    features: list[str] = Field(description="核心特性 bullet")
    comparison: str = Field(description="与其他框架对比，<= 300 字")
    use_cases: list[str] = Field(description="适用场景")
    references: list[str] = Field(
        description="参考资料 url 或 文档", default_factory=list
    )


class Review(BaseModel):
    score: int = Field(description="0-10 分", ge=0, le=10)
    pros: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    suggestions: str = Field(description="给 Writer 的修改建议")


# ============================================================
# 2) Researcher Agent
# ============================================================

@dataclass
class ResearcherDeps:
    search_max_results: int = 5


researcher = Agent[ResearcherDeps, str](
    "openai:gpt-4o-mini",
    deps_type=ResearcherDeps,
    output_type=str,
    system_prompt=(
        "你是一名资深研究员。给定主题，调用 search 工具收集 3-5 条最有价值的资料，"
        "然后整理成研究笔记（800 字以内）：列出核心要点、关键 url 和相互矛盾的信息。"
        "禁止凭印象回答，必须先调 search。"
    ),
)


@researcher.tool
async def search(ctx: RunContext[ResearcherDeps], query: str) -> list[dict]:
    """用 DuckDuckGo 搜索（无需 API key）。"""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return [{"title": "DDG 未安装", "url": "", "snippet": "pip install duckduckgo-search"}]
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=ctx.deps.search_max_results))
        return [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in results
        ]
    except Exception as e:
        # 网络限流 / 抖动 → 让 Agent 兜底
        return [{"title": "搜索失败", "url": "", "snippet": f"{type(e).__name__}: {e}"}]


# ============================================================
# 3) Writer Agent
# ============================================================

writer = Agent(
    "openai:gpt-4o-mini",
    output_type=ResearchReport,
    system_prompt=(
        "你是一名技术作家。基于提供的研究笔记起草结构化报告。"
        "如果有审稿意见（Review），严格按建议修订；务必引用研究笔记里出现的 url 到 references 字段。"
    ),
)


# ============================================================
# 4) Reviewer Agent
# ============================================================

reviewer = Agent(
    "openai:gpt-4o-mini",
    output_type=Review,
    system_prompt=(
        "你是一名严苛的总编。对提供的报告打分（0-10），列 pros / issues，"
        "并给出可操作的修改建议。评分维度：准确性、完整性、引用、可读性。"
        "score >= 8 才能放行。第一轮通常 5-7 分，修订后才给 8+。"
    ),
)


# ============================================================
# 5) Pydantic Graph 节点
# ============================================================

@dataclass
class ResearchState:
    topic: str
    research: str = ""
    report: ResearchReport | None = None
    reviews: list[Review] = field(default_factory=list)
    iteration: int = 0
    max_iter: int = 3


@dataclass
class Research(BaseNode[ResearchState]):
    async def run(self, ctx: GraphRunContext[ResearchState]) -> Write:
        print(f"[Research] 主题：{ctx.state.topic}")
        result = await researcher.run(
            f"研究主题：{ctx.state.topic}",
            deps=ResearcherDeps(search_max_results=5),
        )
        ctx.state.research = result.output
        print(f"[Research] 完成，笔记 {len(ctx.state.research)} 字符")
        return Write()


@dataclass
class Write(BaseNode[ResearchState]):
    async def run(self, ctx: GraphRunContext[ResearchState]) -> Review_:
        ctx.state.iteration += 1
        print(f"[Write] 起草第 {ctx.state.iteration} 稿…")

        prompt = f"主题：{ctx.state.topic}\n\n研究笔记：\n{ctx.state.research}"
        if ctx.state.reviews:
            last = ctx.state.reviews[-1]
            prev = (
                ctx.state.report.model_dump_json(indent=2)
                if ctx.state.report
                else ""
            )
            prompt += (
                f"\n\n上一轮 Reviewer 意见（请修订）：\n"
                f"score={last.score}\nissues={last.issues}\n"
                f"建议={last.suggestions}\n\n"
                f"上一稿：\n{prev}"
            )

        result = await writer.run(prompt)
        ctx.state.report = result.output
        return Review_()


@dataclass
class Review_(BaseNode[ResearchState, None, ResearchReport]):
    """评审节点。出口节点要在 BaseNode 第 3 位泛型写最终返回类型。"""

    async def run(
        self, ctx: GraphRunContext[ResearchState]
    ) -> Write | End[ResearchReport]:
        assert ctx.state.report is not None
        prompt = (
            f"请评审以下报告：\n{ctx.state.report.model_dump_json(indent=2)}"
        )
        result = await reviewer.run(prompt)
        review = result.output
        ctx.state.reviews.append(review)
        print(f"[Review_] 第 {ctx.state.iteration} 轮 score={review.score}")
        if review.issues:
            print(f"          issues: {review.issues[:2]}")

        if review.score >= 8:
            print(f"[Review_] 通过 → End")
            return End(ctx.state.report)

        if ctx.state.iteration >= ctx.state.max_iter:
            print(f"[Review_] 已达最大迭代 {ctx.state.max_iter}，强制 End")
            return End(ctx.state.report)

        print(f"[Review_] 分数 < 8，回到 Write 修订")
        return Write()


graph = Graph(nodes=[Research, Write, Review_])


# ============================================================
# 6) 主入口
# ============================================================

async def main(topic: str, max_iter: int = 3) -> None:
    state = ResearchState(topic=topic, max_iter=max_iter)
    result = await graph.run(Research(), state=state)

    print("\n" + "=" * 60)
    print("FINAL REPORT")
    print("=" * 60)
    if result.output:
        print(result.output.model_dump_json(indent=2, exclude_none=True))
        # 保存
        with open("report.json", "w", encoding="utf-8") as f:
            f.write(result.output.model_dump_json(indent=2, exclude_none=True))
        print(f"\n💾 已保存到 report.json")
    print(
        f"\n经过 {state.iteration} 轮迭代，"
        f"{len(state.reviews)} 次评审，"
        f"最终 score={state.reviews[-1].score if state.reviews else 'N/A'}"
    )


if __name__ == "__main__":
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ 请先在 .env 设置 OPENAI_API_KEY")
        sys.exit(1)

    topic = sys.argv[1] if len(sys.argv) > 1 else "Pydantic AI 的核心特性"
    asyncio.run(main(topic, max_iter=3))
