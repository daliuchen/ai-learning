"""
06_hitl.py
==========
Human-in-the-loop：在工具调用前插入人工审核节点。
"""
from typing_extensions import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

load_dotenv()


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """发送邮件"""
    return f"邮件已发给 {to}"


class S(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


model = ChatOpenAI(model="gpt-4o-mini").bind_tools([send_email])


def agent(state: S):
    return {"messages": [model.invoke(state["messages"])]}


def human_approval(state: S):
    last = state["messages"][-1]
    if not last.tool_calls:
        return {}
    decision = interrupt({
        "question": "是否批准以下工具调用？",
        "tool_calls": [{"name": c["name"], "args": c["args"]} for c in last.tool_calls],
    })
    if decision == "yes":
        return {}
    return {"messages": [
        ToolMessage(content="人工拒绝", tool_call_id=c["id"]) for c in last.tool_calls
    ]}


def route(state: S):
    if not state["messages"][-1].tool_calls:
        return END
    return "review"


def after_review(state: S):
    last = state["messages"][-1]
    if isinstance(last, ToolMessage) and "拒绝" in last.content:
        return "agent"
    return "tools"


def main():
    g = StateGraph(S)
    g.add_node("agent", agent)
    g.add_node("review", human_approval)
    g.add_node("tools", ToolNode([send_email]))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route, {"review": "review", END: END})
    g.add_conditional_edges("review", after_review, {"tools": "tools", "agent": "agent"})
    g.add_edge("tools", "agent")

    memory = MemorySaver()
    app = g.compile(checkpointer=memory)
    cfg = {"configurable": {"thread_id": "demo"}}

    print("\n=== 1) 第一次发起 ===")
    out = app.invoke(
        {"messages": [("human", "给 boss@x.com 发邮件标题为'请假'，正文'明天请假'")]},
        config=cfg,
    )
    if "__interrupt__" in out:
        print("待审：", out["__interrupt__"][0].value)

    print("\n=== 2) 人工批准 ===")
    final = app.invoke(Command(resume="yes"), config=cfg)
    print(final["messages"][-1].content)

    print("\n=== 3) 另一线程，演示拒绝 ===")
    cfg2 = {"configurable": {"thread_id": "demo2"}}
    out2 = app.invoke(
        {"messages": [("human", "给 boss@x.com 发邮件请假")]}, config=cfg2,
    )
    if "__interrupt__" in out2:
        print("待审：", out2["__interrupt__"][0].value)
    final2 = app.invoke(Command(resume="no"), config=cfg2)
    print(final2["messages"][-1].content)


if __name__ == "__main__":
    main()
