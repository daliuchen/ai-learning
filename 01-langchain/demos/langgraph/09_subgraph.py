"""
09_subgraph.py
==============
团队子图：研究团队（web + paper）独立 state，主图只看 summary。
"""
from typing_extensions import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent

load_dotenv()


# ----------------- 团队子图 -----------------
class TeamState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    summary: str


@tool
def web_search(q: str) -> str:
    """搜索"""
    return f"[web] 关于 {q} 的资料：现代框架，关注状态机与持久化"


@tool
def arxiv_search(q: str) -> str:
    """论文搜索"""
    return f"[arxiv] 找到 {q} 相关论文 3 篇"


web_agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), [web_search])
paper_agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), [arxiv_search])


def web_node(state):
    out = web_agent.invoke({"messages": state["messages"]})
    return {"messages": [HumanMessage(content=out["messages"][-1].content, name="web")]}


def paper_node(state):
    out = paper_agent.invoke({"messages": state["messages"]})
    return {"messages": [HumanMessage(content=out["messages"][-1].content, name="paper")]}


def summarize(state):
    text = "\n".join(
        m.content for m in state["messages"]
        if isinstance(m, HumanMessage) and m.name in ("web", "paper")
    )
    s = ChatOpenAI(model="gpt-4o-mini").invoke(f"用一段话总结：\n{text}").content
    return {"summary": s}


team = StateGraph(TeamState)
team.add_node("web", web_node)
team.add_node("paper", paper_node)
team.add_node("summarize", summarize)
team.add_edge(START, "web")
team.add_edge(START, "paper")
team.add_edge("web", "summarize")
team.add_edge("paper", "summarize")
team.add_edge("summarize", END)
research_team = team.compile()


# ----------------- 主图 -----------------
class MainState(TypedDict):
    question: str
    research_summary: str
    answer: str


def call_team(state: MainState):
    out = research_team.invoke({"messages": [("human", state["question"])], "summary": ""})
    return {"research_summary": out["summary"]}


def write_answer(state: MainState):
    ans = ChatOpenAI(model="gpt-4o-mini").invoke(
        f"基于研究内容：\n{state['research_summary']}\n\n回答用户问题：{state['question']}"
    ).content
    return {"answer": ans}


def main():
    main_g = StateGraph(MainState)
    main_g.add_node("research", call_team)
    main_g.add_node("write", write_answer)
    main_g.add_edge(START, "research")
    main_g.add_edge("research", "write")
    main_g.add_edge("write", END)
    app = main_g.compile()

    out = app.invoke({
        "question": "请用一段话介绍 LangGraph 的优势",
        "research_summary": "",
        "answer": "",
    })
    print("\n--- 研究摘要 ---")
    print(out["research_summary"])
    print("\n--- 最终答案 ---")
    print(out["answer"])


if __name__ == "__main__":
    main()
