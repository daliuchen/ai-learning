"""
03_state_reducers.py
====================
演示多种 reducer：add / set.union / lambda / add_messages
"""
from operator import add
from typing_extensions import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


def sum_int(l, r):
    return (l or 0) + (r or 0)


def union_set(l, r):
    return (l or set()) | (r or set())


class S(TypedDict):
    counter: Annotated[int, sum_int]
    logs: Annotated[list[str], add]
    messages: Annotated[list[BaseMessage], add_messages]
    tags: Annotated[set[str], union_set]


def n1(s):
    return {"counter": 1, "logs": ["n1"], "messages": [HumanMessage(content="hi")], "tags": {"a"}}


def n2(s):
    return {"counter": 2, "logs": ["n2"], "messages": [AIMessage(content="hello")], "tags": {"b"}}


def n3(s):
    return {"counter": 10, "logs": ["n3"], "tags": {"c"}}


def main():
    g = StateGraph(S)
    g.add_node("n1", n1)
    g.add_node("n2", n2)
    g.add_node("n3", n3)
    g.add_edge(START, "n1")
    g.add_edge("n1", "n2")
    g.add_edge("n1", "n3")
    g.add_edge("n2", END)
    g.add_edge("n3", END)
    app = g.compile()

    out = app.invoke({"counter": 0, "logs": [], "messages": [], "tags": set()})
    print(out)


if __name__ == "__main__":
    main()
