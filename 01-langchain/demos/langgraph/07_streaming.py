"""
07_streaming.py
===============
五种 stream_mode 演示 + custom 事件。
"""
from typing_extensions import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_core.messages import BaseMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

load_dotenv()


@tool
def add(a: int, b: int) -> int:
    """加法"""
    return a + b


class S(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


model = ChatOpenAI(model="gpt-4o-mini").bind_tools([add])


def agent(state: S):
    writer = get_stream_writer()
    writer({"event": "agent_started"})
    msg = model.invoke(state["messages"])
    writer({"event": "agent_done", "has_tool_call": bool(msg.tool_calls)})
    return {"messages": [msg]}


def build():
    g = StateGraph(S)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode([add]))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    return g.compile()


def main():
    app = build()
    x = {"messages": [("human", "请帮我算 12+30，再告诉我答案")]}

    print("\n--- stream_mode='updates' ---")
    for ev in app.stream(x, stream_mode="updates"):
        print(ev)

    print("\n--- stream_mode='messages' (token-level) ---")
    for token, meta in app.stream(x, stream_mode="messages"):
        if token.content:
            print(token.content, end="", flush=True)
    print()

    print("\n--- stream_mode='custom' ---")
    for ev in app.stream(x, stream_mode="custom"):
        print(ev)

    print("\n--- multi mode ---")
    for mode, ev in app.stream(x, stream_mode=["updates", "custom"]):
        print(f"[{mode}]", ev)


if __name__ == "__main__":
    main()
