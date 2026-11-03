"""
01_streaming.py
===============
Pydantic AI 流式响应：演示三种粒度的流式输出。

1) stream_text(delta=True)：纯文本 token 级流式
2) stream_output()：结构化对象流（边生成边校验）
3) iter()：节点级事件流（看到工具调用过程）

没设置 API key 时自动 fallback 到 TestModel（TestModel 不真流式，但能跑通 API）。

运行：
    python demos/advanced/01_streaming.py
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

load_dotenv()


def pick_model() -> str | TestModel:
    if os.getenv("OPENAI_API_KEY"):
        return "openai-chat:qwen-flash"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic:claude-haiku-4-5"
    print("[warn] 未检测到 API key，使用 TestModel（不会真流式）。\n")
    return TestModel()


# ----------------------------------------------------------------------------
# 1) 文本流：聊天 UI 边打边显示
# ----------------------------------------------------------------------------
async def demo_text_stream(model) -> None:
    print("===== 1) stream_text(delta=True) — 文本增量流 =====")
    agent = Agent(model, instructions="你是一位简洁的助手。")
    async with agent.run_stream("用 30 字介绍 Python 的 GIL") as result:
        async for chunk in result.stream_text(delta=True):
            print(chunk, end="", flush=True)
    print(f"\n[usage] {result.usage}\n")


# ----------------------------------------------------------------------------
# 2) 结构化流：边生成边校验 Pydantic 对象
# ----------------------------------------------------------------------------
class Profile(BaseModel):
    name: str = Field(default="", description="姓名")
    age: int | None = Field(default=None, description="年龄")
    skills: list[str] = Field(default_factory=list, description="技能列表")
    bio: str = Field(default="", description="一句话简介")


async def demo_structured_stream(model) -> None:
    print("===== 2) stream_output() — 结构化对象流 =====")
    agent = Agent(
        model,
        output_type=Profile,
        instructions="根据用户描述生成个人资料卡，字段尽量完整。",
    )
    async with agent.run_stream(
        "28 岁的后端工程师小李，擅长 Python、FastAPI、PostgreSQL，喜欢极简主义"
    ) as result:
        last_snapshot = None
        async for partial in result.stream_output():
            # 只 print 变化的字段，避免刷屏
            snapshot = partial.model_dump()
            if snapshot != last_snapshot:
                changed = {
                    k: v
                    for k, v in snapshot.items()
                    if last_snapshot is None or last_snapshot.get(k) != v
                }
                print(f"  partial -> {changed}")
                last_snapshot = snapshot
        print(f"\n  final  -> {await result.get_output()}")
        print(f"[usage] {result.usage}\n")


# ----------------------------------------------------------------------------
# 3) 节点流：看到工具调用过程
# ----------------------------------------------------------------------------
async def demo_iter_nodes(model) -> None:
    print("===== 3) agent.iter() — 节点级事件 =====")
    agent = Agent(model, instructions="你是天气助手。")

    @agent.tool_plain
    def get_weather(city: str) -> str:
        """查询城市天气"""
        fake = {"北京": "晴 26°C", "上海": "多云 24°C"}
        return fake.get(city, "未知")

    async with agent.iter("北京和上海的天气分别如何？") as run:
        async for node in run:
            print(f"  node -> {type(node).__name__}")
    print(f"  final  -> {run.result.output if run.result else None}")
    if run.result:
        print(f"[usage] {run.result.usage}\n")
    else:
        print()


# ----------------------------------------------------------------------------
# 4) 取消运行（演示）
# ----------------------------------------------------------------------------
async def demo_cancel(model) -> None:
    print("===== 4) CancellationToken — 中途取消一次运行 =====")
    from pydantic_ai import CancellationToken, RunCancelled

    agent = Agent(model)
    token = CancellationToken()
    task = asyncio.create_task(
        agent.run("写一篇 1000 字的散文", cancellation_token=token)
    )
    await asyncio.sleep(0.5)  # 模拟用户点了「停止」
    token.cancel()            # 幂等、线程安全；一个 token 可以管多个运行
    try:
        await task
        print("  （模型 0.5 秒内就答完了，没来得及取消）")
    except RunCancelled as exc:
        # 取消前已完成的消息都在，可以持久化后用 message_history 续跑
        print(f"  [已取消] 取消前已完成 {len(exc.all_messages())} 条消息")
    print()


async def main() -> None:
    model = pick_model()
    await demo_text_stream(model)
    await demo_structured_stream(model)
    await demo_iter_nodes(model)
    await demo_cancel(model)


if __name__ == "__main__":
    asyncio.run(main())
