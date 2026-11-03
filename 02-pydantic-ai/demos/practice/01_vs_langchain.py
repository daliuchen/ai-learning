"""
对照 demo：同样的"查天气" Agent 用 Pydantic AI 和 LangChain 各写一遍。

运行：
    python demos/practice/01_vs_langchain.py

需要：
    pip install "pydantic-ai-slim[openai]" langchain langchain-openai   # LangChain 1.x
    export OPENAI_API_KEY=... OPENAI_BASE_URL=...    # 千问的 OpenAI 兼容端点

可以注释掉其中一个分支单独跑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext  # 模块级导入：工具签名里的 RunContext[Deps] 要能被解析

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

@dataclass
class Deps:
    api_key: str  # 真实场景：传给 lookup_weather 用


def run_pydantic_ai(question: str) -> WeatherReport:
    agent = Agent(
        "openai-chat:qwen-flash",   # 你也可以换 "anthropic:claude-haiku-4-5"
        deps_type=Deps,
        output_type=WeatherReport,
        instructions=(
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
# 3) LangChain 1.x 版（create_agent + response_format）
# ============================================================

def run_langchain(question: str) -> WeatherReport:
    # LangChain 1.x 之后不用再手写 ReAct 循环：create_agent 内置工具循环，
    # response_format 在循环结束时产出结构化结果（和 Pydantic AI 的 output_type 对应）。
    from langchain.agents import create_agent
    from langchain_core.tools import tool
    from langchain_openai import ChatOpenAI

    @tool
    def get_weather(city: str) -> dict:
        """查询指定城市的当前天气。"""
        return lookup_weather(city)

    agent = create_agent(
        ChatOpenAI(model="qwen-flash", temperature=0, base_url=os.getenv("OPENAI_BASE_URL")),
        tools=[get_weather],
        system_prompt="你是天气助手，必须调用 get_weather 工具拿数据，不要自己编。",
        response_format=WeatherReport,
    )
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return result["structured_response"]


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
    print("- 两边现在都是「声明工具 + 声明输出类型」，样板量已经很接近")
    print("- Pydantic AI：deps_type / RunContext 让依赖注入有类型检查；output_type 校验失败自动回喂模型重试")
    print("- LangChain 1.x：create_agent + middleware 组合，生态（LangGraph / LangSmith / 集成）更大")


if __name__ == "__main__":
    main()
