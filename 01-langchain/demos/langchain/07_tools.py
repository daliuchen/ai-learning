"""
07_tools.py
===========
Tool 系统综合演示：自定义工具 / bind_tools / 错误处理 / 注入参数 / 手撸 ReAct
"""
from typing_extensions import Annotated

import os
from dotenv import load_dotenv

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolArg, ToolException, tool
from langchain_openai import ChatOpenAI

load_dotenv()
MODEL = os.environ.get("QWEN_MODEL", "qwen-flash")  # 任意 OpenAI 兼容模型；默认千问，base_url 读 OPENAI_BASE_URL


@tool
def get_weather(city: str) -> str:
    """根据城市名查询天气，返回温度与天气状况。"""
    return {"北京": "晴 25℃", "上海": "雨 22℃", "广州": "多云 30℃"}.get(city, "未知")


@tool
def calc(expr: str) -> str:
    """计算数学表达式，仅支持安全的算术。"""
    return str(eval(expr, {"__builtins__": {}}, {}))


@tool
def divide(a: float, b: float) -> float:
    """除法。"""
    if b == 0:
        raise ToolException("除数不能为 0")
    return a / b


# 只有 ToolException 会被这个 handler 接住并变成给模型的文字；
# 其它异常（比如真正的 bug）仍然直接抛出
divide.handle_tool_error = lambda e: f"工具失败：{e}"


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
    model = ChatOpenAI(model=MODEL).bind_tools(tools)

    messages = [HumanMessage(content="查下北京天气；用 divide 工具算 7 除以 0；再帮我搜下我的'iPhone'订单")]

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
