"""
04_react.py
===========
两种 ReAct Agent 实现对比：
1) create_react_agent（prebuilt 5 行版）
2) 手写 StateGraph（理解原理）
"""
from typing_extensions import Annotated, TypedDict

from dotenv import load_dotenv

from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, create_react_agent, tools_condition

load_dotenv()


@tool
def get_weather(city: str) -> str:
    """查询城市天气"""
    return f"{city} 晴 25℃"


@tool
def calc(expr: str) -> str:
    """计算数学表达式"""
    return str(eval(expr, {"__builtins__": {}}, {}))


tools = [get_weather, calc]
model = ChatOpenAI(model="gpt-4o-mini", temperature=0)


def prebuilt_version():
    print("\n========== prebuilt create_react_agent ==========")
    agent = create_react_agent(
        model, tools,
        state_modifier="你是一位严谨的助手，思考清楚再调工具。",
    )
    out = agent.invoke({"messages": [("human", "北京天气？再算 (3+5)*2")]})
    print(out["messages"][-1].content)


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def manual_version():
    print("\n========== 手写 StateGraph ==========")
    by_name = {t.name: t for t in tools}
    bound = model.bind_tools(tools)

    def agent_node(state: State):
        msgs = [SystemMessage("你是助手")] + state["messages"]
        return {"messages": [bound.invoke(msgs)]}

    g = StateGraph(State)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(tools))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    app = g.compile()

    out = app.invoke({"messages": [("human", "北京天气？再算 (3+5)*2")]})
    print(out["messages"][-1].content)

    print("\n--- 流式 updates ---")
    for ev in app.stream({"messages": [("human", "上海天气")]}, stream_mode="updates"):
        print(ev)


if __name__ == "__main__":
    prebuilt_version()
    manual_version()
