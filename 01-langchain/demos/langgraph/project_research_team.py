"""
project_research_team.py
========================
实战项目 2：多 Agent 研究助手
- 顶层 Supervisor
- 研究子团队（web/doc/arxiv 并行）
- 写作 fan-out（Send，每节并行）
- 编辑润色
- 两次 HITL（research_done / draft_done）
- SqliteSaver 持久化
"""
from operator import add
from typing_extensions import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

load_dotenv()

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
    out = ChatOpenAI(model="gpt-4o-mini").invoke(f"整合以下资料成 200 字摘要：\n{text}").content
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
    ans = interrupt({"stage": "research_done", "summary": s["research"]})
    if ans != "yes":
        return {"research": "", "stage": "init"}   # 重新研究
    return {"stage": "writing"}


class SectionInput(TypedDict):
    topic: str
    research: str
    section_title: str


def section_writer(inp: SectionInput):
    prompt = (
        f"主题：{inp['topic']}\n研究资料：{inp['research']}\n"
        f"请写'{inp['section_title']}'一节，300 字以内。"
    )
    text = ChatOpenAI(model="gpt-4o-mini", temperature=0.4).invoke(prompt).content
    return {"sections": [(inp["section_title"], text)]}


def fan_out_sections(state: MainState):
    return [
        Send("section_writer", {
            "topic": state["topic"],
            "research": state["research"],
            "section_title": sec,
        })
        for sec in SECTIONS
    ]


def post_write(s: MainState):
    return {"stage": "draft_done"}


def review_draft(s: MainState):
    preview = {t: c[:60] for t, c in s["sections"]}
    ans = interrupt({"stage": "draft_done", "preview": preview})
    if ans != "yes":
        return {"sections": [], "stage": "writing"}
    return {"stage": "editing"}


def edit(s: MainState):
    ordered = sorted(s["sections"], key=lambda x: SECTIONS.index(x[0]))
    draft = "\n\n".join(f"## {t}\n{c}" for t, c in ordered)
    polished = ChatOpenAI(model="gpt-4o-mini").invoke(
        f"对以下报告整体润色，保持 markdown 标题不变：\n{draft}"
    ).content
    return {"final": polished, "stage": "done"}


def stage_router(s: MainState):
    stage = s.get("stage") or "init"
    return {
        "init": "research",
        "research_done": "review_research",
        "writing": "fan_out",
        "drafting": "post_write",
        "draft_done": "review_draft",
        "editing": "edit",
        "done": END,
    }.get(stage, END)


def post_fan_out_router(_s):
    return "post_write"


def build_main():
    g = StateGraph(MainState)
    g.add_node("research", call_research)
    g.add_node("review_research", review_research)
    g.add_node("section_writer", section_writer)
    g.add_node("post_write", post_write)
    g.add_node("review_draft", review_draft)
    g.add_node("edit", edit)

    g.add_conditional_edges(
        START, stage_router,
        {
            "research": "research",
            "review_research": "review_research",
            "fan_out": "section_writer",      # 用 fan_out 触发 Send
            "post_write": "post_write",
            "review_draft": "review_draft",
            "edit": "edit",
            END: END,
        },
    )
    g.add_edge("research", "review_research")
    # review_research 决定是否进入 fan_out
    g.add_conditional_edges(
        "review_research",
        lambda s: "fan_out" if s.get("research") else "research",
        {"fan_out": "section_writer", "research": "research"},
    )
    # section_writer 完成后 → post_write
    g.add_edge("section_writer", "post_write")
    g.add_edge("post_write", "review_draft")
    g.add_conditional_edges(
        "review_draft",
        lambda s: "edit" if s.get("sections") else "section_writer",
        {"edit": "edit", "section_writer": "section_writer"},
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
