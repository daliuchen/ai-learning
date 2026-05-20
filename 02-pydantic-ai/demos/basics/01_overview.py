"""
01_overview.py
==============
Pydantic AI Hello World：用最少的代码体验 Agent 的三种用法。

1) 纯文本回复
2) 结构化输出（output_type=PydanticModel）
3) 带工具的 Agent

没设置 OPENAI_API_KEY 时，会自动 fallback 到 TestModel，不会 crash。

运行：
    python demos/basics/01_overview.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic import BaseModel

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_model() -> str | TestModel:
    """根据环境变量决定用真实模型还是 TestModel。"""
    if os.getenv("OPENAI_API_KEY"):
        return "openai:gpt-4o-mini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-3-5-haiku-latest"
    print("[warn] 未检测到 API key，使用 TestModel 跑测试输出。\n")
    return TestModel()


class Invoice(BaseModel):
    amount: float
    vendor: str
    date: str


def demo_text(model) -> None:
    print("===== 1) 纯文本回复 =====")
    agent = Agent(model, system_prompt="你是一位简洁的助手，一句话回答。")
    result = agent.run_sync("Python 的 GIL 是什么？")
    print(result.output)
    print()


def demo_structured(model) -> None:
    print("===== 2) 结构化输出 =====")
    agent = Agent(
        model,
        output_type=Invoice,
        system_prompt="从用户消息中抽取发票信息。",
    )
    result = agent.run_sync("发票：阿里云 2024-01-15 ¥1280")
    print(repr(result.output))
    print()


def demo_tool(model) -> None:
    print("===== 3) 带工具的 Agent =====")
    agent = Agent(model, system_prompt="你是一位天气助手，需要时调用 get_weather 工具。")

    @agent.tool_plain
    def get_weather(city: str) -> str:
        """查询城市当前天气"""
        fake_db = {"北京": "晴 26°C", "上海": "多云 24°C"}
        return fake_db.get(city, "未知")

    result = agent.run_sync("北京和上海的天气分别怎么样？")
    print(result.output)
    print("\n— usage —")
    print(result.usage())
    print()


def main() -> None:
    model = pick_model()
    demo_text(model)
    demo_structured(model)
    demo_tool(model)


if __name__ == "__main__":
    main()
