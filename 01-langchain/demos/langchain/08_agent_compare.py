"""
08_agent_compare.py
===================
对比旧的 AgentExecutor（现在在 langchain-classic 里）与新的 langchain.agents.create_agent

运行：
    python demos/langchain/08_agent_compare.py
"""
import os

from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

load_dotenv()
MODEL = os.environ.get("QWEN_MODEL", "qwen-flash")  # 任意 OpenAI 兼容模型；默认千问，base_url 读 OPENAI_BASE_URL


@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气"""
    return f"{city} 晴 25℃"


@tool
def calc(expr: str) -> str:
    """计算数学表达式"""
    return str(eval(expr, {"__builtins__": {}}, {}))


def old_way():
    """旧写法：AgentExecutor 已经搬到 langchain-classic（pip install langchain-classic）。"""
    from langchain_classic.agents import AgentExecutor, create_tool_calling_agent

    print("\n========== 旧 AgentExecutor（langchain-classic）==========")
    llm = ChatOpenAI(model=MODEL, temperature=0)
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
    print("\n========== 新 create_agent ==========")
    agent = create_agent(
        ChatOpenAI(model=MODEL, temperature=0),
        tools=[get_weather, calc],
        system_prompt="你是一位严谨的助手。",
    )
    out = agent.invoke({"messages": [{"role": "user", "content": "查询北京天气，再算 (3+5)*2"}]})
    print("最终：", out["messages"][-1].content)

    # 看每一步：节点叫 model / tools
    print("\n--- 流式 updates ---")
    for ev in agent.stream(
        {"messages": [{"role": "user", "content": "上海天气"}]}, stream_mode="updates"
    ):
        for node, value in ev.items():
            print(node, "->", value["messages"][-1].content or value["messages"][-1].tool_calls)


if __name__ == "__main__":
    old_way()
    new_way()
