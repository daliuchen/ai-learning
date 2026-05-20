"""
08_multi_agent.py
=================
Supervisor 模式多 Agent：researcher + writer + supervisor
"""
from typing import Literal
from typing_extensions import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent

load_dotenv()


@tool
def web_search(q: str) -> str:
    """搜索网络资料"""
    return f"[搜到] {q} 是一个状态机型 Agent 编排框架，由 LangChain 团队开发"


researcher = create_react_agent(
    ChatOpenAI(model="gpt-4o-mini", temperature=0),
    [web_search],
    state_modifier="你是研究员，只搜资料并复述结果，不要进行写作。",
)
writer = create_react_agent(
    ChatOpenAI(model="gpt-4o-mini", temperature=0.4),
    [],
    state_modifier="你是文案专家，根据 messages 中的资料写一篇 200 字介绍。",
)


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next: str


def supervisor(state: State):
    sys = (
        "你是调度器。看 messages 历史，回复 researcher / writer / FINISH 之一。"
        " 没有 [researcher 输出] → researcher; "
        " 有研究但没 [writer 输出] → writer; "
        " 已有 writer 输出 → FINISH"
    )
    decision = ChatOpenAI(model="gpt-4o-mini", temperature=0).invoke(
        [("system", sys), *state["messages"]],
    )
    return {"next": decision.content.strip().upper()}


def make_call(agent, label: str):
    def _node(state):
        result = agent.invoke({"messages": state["messages"]})
        return {"messages": [
            HumanMessage(
                content=f"[{label} 输出]\n{result['messages'][-1].content}",
                name=label,
            )
        ]}
    return _node


def route(state: State) -> Literal["researcher", "writer", "__end__"]:
    n = state["next"].lower()
    if "researcher" in n:
        return "researcher"
    if "writer" in n:
        return "writer"
    return END


def build():
    g = StateGraph(State)
    g.add_node("supervisor", supervisor)
    g.add_node("researcher", make_call(researcher, "researcher"))
    g.add_node("writer", make_call(writer, "writer"))
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route)
    g.add_edge("researcher", "supervisor")
    g.add_edge("writer", "supervisor")
    return g.compile()


def main():
    app = build()
    out = app.invoke({
        "messages": [("human", "写一篇 200 字 LangGraph 介绍")],
        "next": "",
    })
    print("\n=== 最终 messages ===")
    for m in out["messages"]:
        name = getattr(m, "name", type(m).__name__)
        print(f"[{name}] {m.content[:160]}")


if __name__ == "__main__":
    main()
