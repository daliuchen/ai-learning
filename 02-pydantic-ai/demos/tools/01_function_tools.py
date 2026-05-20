"""
01_function_tools.py
====================
Pydantic AI Function Tools 演示：

1) @agent.tool_plain：不需要 RunContext
2) @agent.tool：需要 RunContext，能拿 deps
3) Tool() 直接构造，复用同一个函数
4) ModelRetry：让模型重试

没有 OPENAI_API_KEY 时自动切到 TestModel，无网也能跑。

运行：
    python demos/tools/01_function_tools.py
"""
from __future__ import annotations

import os
import random
from datetime import date

from dotenv import load_dotenv
from pydantic import BaseModel

from pydantic_ai import Agent, ModelRetry, RunContext, Tool
from pydantic_ai.models.test import TestModel

load_dotenv()


# ---------- 依赖类型 ----------
class UserDeps(BaseModel):
    user_id: str
    timezone: str


# ---------- 选择模型：有 key 用真模型，没 key 用 TestModel ----------
def pick_model():
    if os.getenv('OPENAI_API_KEY'):
        return 'openai:gpt-4o-mini'
    # TestModel 会自动调用每个工具并返回模拟值，方便离线跑通
    return TestModel()


# ---------- Agent ----------
agent = Agent(
    pick_model(),
    deps_type=UserDeps,
    instructions=(
        "You are a friendly scheduling assistant. "
        "Use the tools to answer the user's questions concisely."
    ),
)


# ---------- 工具 1：plain，不需要 ctx ----------
@agent.tool_plain
def roll_dice() -> str:
    """Roll a six-sided die and return the result."""
    return str(random.randint(1, 6))


# ---------- 工具 2：需要 ctx 拿 deps ----------
@agent.tool
def list_events(ctx: RunContext[UserDeps], day: date) -> list[dict]:
    """List calendar events for a given day.

    Args:
        day: Date to query in ISO format YYYY-MM-DD.
    """
    fake_db = {
        date(2026, 5, 20): [
            {'time': '10:00', 'title': 'Standup', 'user': ctx.deps.user_id},
            {'time': '15:00', 'title': '1:1 with manager', 'user': ctx.deps.user_id},
        ],
    }
    return fake_db.get(day, [])


# ---------- 工具 3：演示 ModelRetry ----------
@agent.tool_plain(docstring_format='google', require_parameter_descriptions=True)
def get_weather(city: str, unit: str = 'c') -> dict:
    """Get current weather of a city.

    Args:
        city: City English name, e.g. "Shanghai".
        unit: Temperature unit, must be 'c' or 'f'.
    """
    if not city or len(city) < 2:
        raise ModelRetry(
            f"Invalid city {city!r}. Provide a real city name, e.g. 'Shanghai'."
        )
    if unit not in ('c', 'f'):
        raise ModelRetry(f"unit must be 'c' or 'f', got {unit!r}.")
    return {'city': city, 'temperature': 21, 'unit': unit, 'condition': 'sunny'}


# ---------- 演示：用 Tool() 直接构造、复用同一函数到第二个 Agent ----------
def echo(text: str) -> str:
    """Echo back the given text.

    Args:
        text: Anything you want to echo.
    """
    return text.upper()


echo_agent = Agent(
    pick_model(),
    tools=[Tool(echo, takes_ctx=False, name='shout', description='Shout the text in upper case.')],
    instructions='Use the shout tool to echo what the user said in upper case.',
)


def main() -> None:
    deps = UserDeps(user_id='U-001', timezone='Asia/Shanghai')

    print('===== 1. 基础调用：双工具 =====')
    result = agent.run_sync(
        "What's on my calendar for May 20, 2026, and how's the weather in Shanghai?",
        deps=deps,
    )
    print(result.output)

    print('\n===== 2. 看看模型实际调用了哪些工具 =====')
    for m in result.all_messages():
        for part in m.parts:
            cls = type(part).__name__
            if cls in ('ToolCallPart', 'ToolReturnPart'):
                print(f'  [{cls}] {getattr(part, "tool_name", "")} -> {getattr(part, "args", getattr(part, "content", ""))}')

    print('\n===== 3. Tool() 构造、复用函数 =====')
    r2 = echo_agent.run_sync('hello pydantic ai')
    print(r2.output)

    print('\n===== 4. 单独跑一下 roll_dice，验证 plain 工具 =====')
    r3 = agent.run_sync('Roll the dice for me, please.', deps=deps)
    print(r3.output)


if __name__ == '__main__':
    main()
