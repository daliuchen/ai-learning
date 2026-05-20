"""
08_agent_compare.py
===================
对比老 AgentExecutor 与新 LangGraph create_react_agent
"""
from dotenv import load_dotenv

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

load_dotenv()


@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气"""
    return f"{city} 晴 25℃"


@tool
def calc(expr: str) -> str:
    """计算数学表达式"""
    return str(eval(expr, {"__builtins__": {}}, {}))


def old_way():
    print("\n========== 旧 AgentExecutor ==========")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    tools = [get_weather, calc]
    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一位严谨的助手。"),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    ex = AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=4)
    print("最终：", ex.invoke({"input": "查询北京天气，再算 (3+5)*2"})["output"])


def new_way():
    print("\n========== 新 LangGraph create_react_agent ==========")
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    graph = create_react_agent(llm, [get_weather, calc])
    out = graph.invoke({"messages": [("human", "查询北京天气，再算 (3+5)*2")]})
    print("最终：", out["messages"][-1].content)

    # 看每一步
    print("\n--- 流式 updates ---")
    for ev in graph.stream({"messages": [("human", "上海天气")]}, stream_mode="updates"):
        print(ev)


if __name__ == "__main__":
    old_way()
    new_way()
