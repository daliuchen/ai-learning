"""
最小可部署 LangGraph 项目。
本地运行：
    cd demos/langgraph/deploy
    langgraph dev
然后浏览器打开 Studio。
"""
import os

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI


@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气"""
    return f"{city} 晴 25℃"


@tool
def calc(expr: str) -> str:
    """计算数学表达式"""
    return str(eval(expr, {"__builtins__": {}}, {}))


model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash"), temperature=0)
graph = create_agent(model, [get_weather, calc])  # create_agent 返回的就是编译好的图
