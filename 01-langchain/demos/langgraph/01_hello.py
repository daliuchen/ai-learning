"""
01_hello.py
===========
LangGraph 最小 Hello：State → upper → shout → END
"""
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict):
    text: str


def upper(s: State) -> State:
    return {"text": s["text"].upper()}


def shout(s: State) -> State:
    return {"text": s["text"] + "!!!"}


def main():
    g = StateGraph(State)
    g.add_node("upper", upper)
    g.add_node("shout", shout)
    g.add_edge(START, "upper")
    g.add_edge("upper", "shout")
    g.add_edge("shout", END)

    app = g.compile()
    print(app.invoke({"text": "hi langgraph"}))
    print()
    print(app.get_graph().draw_ascii())


if __name__ == "__main__":
    main()
