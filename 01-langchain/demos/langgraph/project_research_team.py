"""
project_research_team.py
========================
实战项目 2：多 Agent 研究助手（LangGraph 1.x：StateGraph + 子图 + Send + interrupt）
- 顶层 Supervisor
- 研究子团队（web/doc/arxiv 并行）
- 写作 fan-out（Send，每节并行）
- 编辑润色
- 两次 HITL（research_done / draft_done）
- SqliteSaver 持久化
"""
from operator import add
from typing_extensions import Annotated, TypedDict

import os

from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

load_dotenv()

# OpenAI 兼容接口（如通义千问）：OPENAI_API_KEY / OPENAI_BASE_URL；模型名取 QWEN_MODEL
MODEL = os.environ.get("QWEN_MODEL", "gpt-4o-mini")

SECTIONS = ["概述", "核心特性", "与其他框架对比", "适用场景"]


# ============== 研究子团队 ==============
class ResearchState(TypedDict):
    topic: str
    web_results: str
    doc_results: str
    arxiv_results: str
    summary: str


def web_node(s: ResearchState):
    return {"web_results": f"[web] {s['topic']} 是流行的状态机型 Agent 框架，最近版本 0.2.x"}


def doc_node(s: ResearchState):
    return {"doc_results": f"[doc] 内部资料：{s['topic']} 适合复杂工作流"}


def arxiv_node(s: ResearchState):
    return {"arxiv_results": f"[arxiv] 找到 3 篇关于 {s['topic']} 的相关论文"}


def summarize_research(s: ResearchState):
    text = "\n".join([s["web_results"], s["doc_results"], s["arxiv_results"]])
    out = ChatOpenAI(model=MODEL).invoke(f"整合以下资料成 200 字摘要：\n{text}").content
    return {"summary": out}


def build_research_team():
    r = StateGraph(ResearchState)
    r.add_node("web", web_node)
    r.add_node("doc", doc_node)
    r.add_node("arxiv", arxiv_node)
    r.add_node("summarize", summarize_research)
    r.add_edge(START, "web")
    r.add_edge(START, "doc")
    r.add_edge(START, "arxiv")
    r.add_edge("web", "summarize")
    r.add_edge("doc", "summarize")
    r.add_edge("arxiv", "summarize")
    r.add_edge("summarize", END)
    return r.compile()


research_team = build_research_team()


# ============== 主图 ==============
class MainState(TypedDict):
    topic: str
    research: str
    sections: Annotated[list[tuple[str, str]], add]
    final: str
    stage: str


def call_research(s: MainState):
    out = research_team.invoke({"topic": s["topic"]})
    return {"research": out["summary"], "stage": "research_done"}


def review_research(s: MainState):
    """第一个 HITL：审研究摘要。interrupt 会暂停整张图，等 Command(resume=...) 恢复。"""
    ans = interrupt({"stage": "research_done", "summary": s["research"]})
    return {"stage": "writing" if ans == "yes" else "init"}


class SectionInput(TypedDict):
    topic: str
    research: str
    section_title: str


def section_writer(inp: SectionInput):
    prompt = (
        f"主题：{inp['topic']}\n研究资料：{inp['research']}\n"
        f"请写'{inp['section_title']}'一节，120 字以内。"
    )
    text = ChatOpenAI(model=MODEL, temperature=0.4).invoke(prompt).content
    return {"sections": [(inp["section_title"], text)]}


def after_research_review(s: MainState):
    """通过 → 用 Send 动态 fan-out，每节一个并行 worker；拒绝 → 回到 research 重做。"""
    if s["stage"] != "writing":
        return "research"
    return [
        Send("section_writer", {
            "topic": s["topic"],
            "research": s["research"],
            "section_title": sec,
        })
        for sec in SECTIONS
    ]


def review_draft(s: MainState):
    """第二个 HITL：审初稿。"""
    preview = {t: c[:60] for t, c in s["sections"]}
    ans = interrupt({"stage": "draft_done", "preview": preview})
    return {"stage": "editing" if ans == "yes" else "rejected"}


def edit(s: MainState):
    ordered = sorted(s["sections"], key=lambda x: SECTIONS.index(x[0]))
    draft = "\n\n".join(f"## {t}\n{c}" for t, c in ordered)
    polished = ChatOpenAI(model=MODEL).invoke(
        f"对以下报告整体润色，保持 markdown 标题不变：\n{draft}"
    ).content
    return {"final": polished, "stage": "done"}


def build_main():
    g = StateGraph(MainState)
    g.add_node("research", call_research)
    g.add_node("review_research", review_research)
    g.add_node("section_writer", section_writer)
    g.add_node("review_draft", review_draft)
    g.add_node("edit", edit)

    g.add_edge(START, "research")
    g.add_edge("research", "review_research")
    # 返回 Send 列表 → 并行跑多个 section_writer；返回 "research" → 重来
    g.add_conditional_edges("review_research", after_research_review, ["section_writer", "research"])
    # 所有 section_writer 都完成后（同一 superstep）才进入 review_draft
    g.add_edge("section_writer", "review_draft")
    g.add_conditional_edges(
        "review_draft",
        lambda s: "edit" if s["stage"] == "editing" else END,
        {"edit": "edit", END: END},
    )
    g.add_edge("edit", END)
    return g


def main():
    with SqliteSaver.from_conn_string(":memory:") as memory:
        app = build_main().compile(checkpointer=memory)
        cfg = {"configurable": {"thread_id": "research-001"}}

        print("\n>>> 启动研究流程")
        out = app.invoke({"topic": "LangGraph", "sections": [], "stage": "init"}, config=cfg)
        if "__interrupt__" in out:
            print("【审核 research】", out["__interrupt__"][0].value["summary"][:120])

        print("\n>>> 批准 research")
        out = app.invoke(Command(resume="yes"), config=cfg)
        if "__interrupt__" in out:
            print("【审核 draft】", out["__interrupt__"][0].value)

        print("\n>>> 批准 draft，编辑润色")
        final = app.invoke(Command(resume="yes"), config=cfg)
        print("\n=== 最终报告 ===\n")
        print(final["final"])


if __name__ == "__main__":
    main()
