"""
03_first_agent.py
=================
Pydantic AI Agent 全方位演示：
1) run_sync 同步
2) await run 异步
3) run_stream 流式
4) 动态系统提示 @agent.system_prompt
5) 结构化输出
6) 工具循环

没有 API key 时自动 fallback 到 TestModel。

运行：
    python demos/basics/03_first_agent.py
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from pydantic import BaseModel

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_model():
    if os.getenv("OPENAI_API_KEY"):
        return "openai:gpt-4o-mini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-3-5-haiku-latest"
    print("[warn] 未检测到 API key，使用 TestModel\n")
    return TestModel()


MODEL = pick_model()


# ---------- 1) run_sync ----------
def demo_run_sync() -> None:
    print("===== 1) run_sync =====")
    agent = Agent(MODEL, system_prompt="你是一位简洁的助手，最多 30 字。")
    result = agent.run_sync("Python GIL 是什么？")
    print(f"output : {result.output}")
    print(f"usage  : {result.usage()}")
    print()


# ---------- 2) async run ----------
async def demo_run_async() -> None:
    print("===== 2) await run =====")
    agent = Agent(MODEL, system_prompt="一句话回答。")
    result = await agent.run("什么是协程？")
    print(f"output : {result.output}")
    print()


# ---------- 3) run_stream ----------
async def demo_run_stream() -> None:
    print("===== 3) run_stream =====")
    agent = Agent(MODEL, system_prompt="用 3-5 句讲故事。")
    async with agent.run_stream("讲一个关于程序员的小故事") as response:
        async for delta in response.stream_text(delta=True):
            print(delta, end="", flush=True)
        print()
    print()


# ---------- 4) 动态系统提示 ----------
@dataclass
class UserCtx:
    user_id: str
    name: str


def demo_dynamic_prompt() -> None:
    print("===== 4) 动态系统提示 =====")
    agent = Agent(
        MODEL,
        deps_type=UserCtx,
        system_prompt="你是一位客服。",
    )

    @agent.system_prompt
    def add_user(ctx: RunContext[UserCtx]) -> str:
        return f"当前用户：{ctx.deps.name}（ID={ctx.deps.user_id}）"

    result = agent.run_sync("我是谁？", deps=UserCtx(user_id="u-001", name="Ethan"))
    print(f"output : {result.output}")
    print()


# ---------- 5) 结构化输出 ----------
class CalcResult(BaseModel):
    expression: str
    answer: float
    explanation: str


def demo_structured() -> None:
    print("===== 5) 结构化输出 =====")
    agent = Agent(
        MODEL,
        output_type=CalcResult,
        system_prompt="解析数学表达式并返回结构化结果",
    )
    r = agent.run_sync("帮我算 (12 + 8) * 3 - 5")
    print(f"output : {r.output!r}")
    print()


# ---------- 6) 工具循环 ----------
def demo_tool_loop() -> None:
    print("===== 6) 工具循环 =====")
    agent = Agent(MODEL, system_prompt="你是一位天气助手，必要时调用 get_weather。")

    @agent.tool_plain
    def get_weather(city: str) -> str:
        """查询城市当前天气"""
        db = {"北京": "晴 26°C", "上海": "多云 24°C", "杭州": "雨 19°C"}
        return db.get(city, "未知")

    result = agent.run_sync("北京和杭州哪个更凉快？")
    print(f"output : {result.output}")
    print(f"messages : {len(result.all_messages())} 条")
    print()


async def main_async() -> None:
    demo_run_sync()
    await demo_run_async()
    await demo_run_stream()
    demo_dynamic_prompt()
    demo_structured()
    demo_tool_loop()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
