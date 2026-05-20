"""
07_tools.py
===========
Tool 系统综合演示：自定义工具 / bind_tools / 错误处理 / 注入参数 / 手撸 ReAct
"""
from typing_extensions import Annotated

from dotenv import load_dotenv

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolArg, tool
from langchain_openai import ChatOpenAI

load_dotenv()


@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气，返回温度与天气状况。"""
    return {"北京": "晴 25℃", "上海": "雨 22℃", "广州": "多云 30℃"}.get(city, "未知")


@tool
def calc(expr: str) -> str:
    """计算数学表达式，仅支持安全的算术。"""
    return str(eval(expr, {"__builtins__": {}}, {}))


@tool(handle_tool_errors=lambda e: f"工具失败：{type(e).__name__}: {e}")
def divide(a: float, b: float) -> float:
    """除法。"""
    return a / b


@tool
def query_orders(
    keyword: str,
    user_id: Annotated[str, InjectedToolArg],
) -> list[dict]:
    """根据关键词搜索当前用户的订单。"""
    return [{"id": 1, "title": f"[user={user_id}] {keyword} 订单"}]


def react_loop():
    tools = [get_weather, calc, divide, query_orders]
    by_name = {t.name: t for t in tools}
    model = ChatOpenAI(model="gpt-4o-mini").bind_tools(tools)

    messages = [HumanMessage(content="查下北京天气；2/0 是多少；再帮我搜下我的'iPhone'订单")]

    for _ in range(5):
        resp = model.invoke(messages)
        messages.append(resp)
        if not resp.tool_calls:
            print("\n最终：", resp.content)
            return
        for call in resp.tool_calls:
            args = dict(call["args"])
            if call["name"] == "query_orders":
                args["user_id"] = "u_123"
            result = by_name[call["name"]].invoke(args)
            print(f"调用 {call['name']}({args}) -> {result}")
            messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))
    print("超过最大轮数")


if __name__ == "__main__":
    react_loop()
