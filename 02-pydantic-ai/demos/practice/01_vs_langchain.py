"""
对照 demo：同样的"查天气" Agent 用 Pydantic AI 和 LangChain 各写一遍。

运行：
    python demos/practice/01_vs_langchain.py

需要：
    pip install pydantic-ai langchain-openai langchain-core
    export OPENAI_API_KEY=...

可以注释掉其中一个分支单独跑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


# ============================================================
# 1) 通用：业务模型 + 假数据
# ============================================================

class WeatherReport(BaseModel):
    city: str
    temp_c: float
    condition: str


FAKE_WEATHER = {
    "北京": (26.0, "晴"),
    "上海": (24.0, "多云"),
    "广州": (30.0, "雷阵雨"),
    "深圳": (29.0, "阵雨"),
}


def lookup_weather(city: str) -> dict:
    """共用的"假"天气查询。"""
    temp, cond = FAKE_WEATHER.get(city, (20.0, "未知"))
    return {"city": city, "temp_c": temp, "condition": cond}


# ============================================================
# 2) Pydantic AI 版（推荐）
# ============================================================

def run_pydantic_ai(question: str) -> WeatherReport:
    from pydantic_ai import Agent, RunContext

    @dataclass
    class Deps:
        api_key: str  # 真实场景：传给 lookup_weather 用

    agent = Agent(
        "openai:gpt-4o-mini",   # 你也可以换 "anthropic:claude-haiku-4-5"
        deps_type=Deps,
        output_type=WeatherReport,
        system_prompt=(
            "你是天气助手，必须调用 get_weather 工具拿数据，不要自己编。"
            "调用工具后，把结果按 WeatherReport 结构返回。"
        ),
    )

    @agent.tool
    async def get_weather(ctx: RunContext[Deps], city: str) -> dict:
        """查询指定城市的当前天气。"""
        return lookup_weather(city)

    result = agent.run_sync(question, deps=Deps(api_key="demo-key"))
    return result.output


# ============================================================
# 3) LangChain 版（手写工具循环 + 二次结构化）
# ============================================================

def run_langchain(question: str) -> WeatherReport:
    from langchain_openai import ChatOpenAI
    from langchain_core.tools import tool
    from langchain_core.messages import (
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    @tool
    def get_weather(city: str) -> dict:
        """查询指定城市的当前天气。"""
        return lookup_weather(city)

    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    model_with_tools = model.bind_tools([get_weather])

    messages = [
        SystemMessage(
            "你是天气助手，必须调用 get_weather 工具拿数据，不要自己编。"
        ),
        HumanMessage(question),
    ]

    # 手写 ReAct 循环
    while True:
        resp = model_with_tools.invoke(messages)
        messages.append(resp)
        if not resp.tool_calls:
            break
        for call in resp.tool_calls:
            tool_result = get_weather.invoke(call["args"])
            messages.append(
                ToolMessage(content=str(tool_result), tool_call_id=call["id"])
            )

    # 第二步：把工具结果转成结构化输出
    structured = model.with_structured_output(WeatherReport)
    final = structured.invoke(
        messages
        + [HumanMessage("基于上面的工具结果，输出一个 WeatherReport。")]
    )
    return final


# ============================================================
# 4) 主入口：跑两遍，对比
# ============================================================

def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print("请先在 .env 设置 OPENAI_API_KEY")
        return

    question = "北京天气怎么样？"

    print("=" * 60)
    print("Pydantic AI 版本")
    print("=" * 60)
    try:
        out = run_pydantic_ai(question)
        print(repr(out))
        print(f"city={out.city}  temp={out.temp_c}°C  condition={out.condition}")
    except Exception as e:
        print(f"[Pydantic AI 出错] {e}")

    print()
    print("=" * 60)
    print("LangChain 版本")
    print("=" * 60)
    try:
        out = run_langchain(question)
        print(repr(out))
        print(f"city={out.city}  temp={out.temp_c}°C  condition={out.condition}")
    except Exception as e:
        print(f"[LangChain 出错] {e}")

    print()
    print("=" * 60)
    print("观察：两份代码的核心差异")
    print("=" * 60)
    print("- Pydantic AI: deps + output_type + @agent.tool，22 行")
    print("- LangChain : bind_tools + 手写 while + with_structured_output，30+ 行")
    print("- 结构化输出：Pydantic AI 一次到位，LangChain 是两次模型调用")


if __name__ == "__main__":
    main()
