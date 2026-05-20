"""
02_stategraph.py
================
计数循环 + reducer + 条件边
"""
from operator import add
from typing_extensions import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class S(TypedDict):
    n: int
    log: Annotated[list[str], add]


def inc(s: S):
    new = s["n"] + 1
    return {"n": new, "log": [f"inc -> {new}"]}


def router(s: S):
    return END if s["n"] >= 5 else "inc"


def main():
    g = StateGraph(S)
    g.add_node("inc", inc)
    g.add_edge(START, "inc")
    g.add_conditional_edges("inc", router, {"inc": "inc", END: END})
    app = g.compile()

    print(app.invoke({"n": 0, "log": []}))
    print()
    print(app.get_graph().draw_ascii())


if __name__ == "__main__":
    main()
