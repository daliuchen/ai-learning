"""
01_multi_agent.py
=================
Pydantic AI 多 Agent 协作四种模式 demo：
  1. Agent-as-Tool          —— 主 Agent 把翻译子 Agent 当工具调用
  2. Orchestrator (纯代码)  —— 抽取 → 校验 流水线
  3. Handoff                —— 前台分流到技术 / 账单
  4. Message Bus            —— asyncio.Queue 上挂多个 worker

没有 OPENAI_API_KEY 时自动用 TestModel 跑通流程（不联网、零费用）。

运行：
    python demos/patterns/01_multi_agent.py
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel


# 没设 key 时整个 demo 走 TestModel
USE_TEST_MODEL = not os.getenv("OPENAI_API_KEY")
MODEL_NAME = "openai:gpt-4o-mini"


# =====================================================================
# 模式 1：Agent-as-Tool
# =====================================================================
translator = Agent(
    MODEL_NAME,
    system_prompt="你是一名翻译，把任何输入翻译成英文，只输出译文。",
)

main_agent = Agent(
    MODEL_NAME,
    system_prompt="你是助手。需要把中文翻译成英文时，调用 translate 工具。",
)


@main_agent.tool
async def translate(ctx: RunContext, text: str) -> str:
    """把中文翻译成英文。

    Args:
        text: 待翻译的中文文本。
    """
    # 关键：传 usage，让子 Agent 的 token 累加到父 Agent
    result = await translator.run(text, usage=ctx.usage)
    return result.output


async def demo_agent_as_tool() -> None:
    print("\n===== 模式 1: Agent-as-Tool =====")
    if USE_TEST_MODEL:
        with main_agent.override(model=TestModel()), translator.override(model=TestModel()):
            r = await main_agent.run("把'今天天气真好'翻译成英文")
    else:
        r = await main_agent.run("把'今天天气真好'翻译成英文")
    print("输出:", r.output)
    print("总 token:", r.usage())


# =====================================================================
# 模式 2：Orchestrator（纯 Python 编排）
# =====================================================================
class ExtractedTicket(BaseModel):
    title: str = Field(description="工单标题")
    priority: Literal["low", "medium", "high"] = Field(description="优先级")
    category: str = Field(description="分类")


class ValidatedTicket(BaseModel):
    title: str
    priority: Literal["low", "medium", "high"]
    category: str
    is_duplicate: bool = Field(description="是否是重复工单")


extractor = Agent(MODEL_NAME, output_type=ExtractedTicket,
                  system_prompt="从用户描述中抽取工单结构。")
validator = Agent(MODEL_NAME, output_type=ValidatedTicket,
                  system_prompt="校验工单并判断是否重复。is_duplicate 默认 false。")


async def pipeline(raw: str) -> ValidatedTicket:
    step1 = await extractor.run(raw)
    step2 = await validator.run(
        f"校验以下工单：{step1.output.model_dump_json()}",
        usage=step1.usage(),
    )
    return step2.output


async def demo_orchestrator() -> None:
    print("\n===== 模式 2: Orchestrator =====")
    if USE_TEST_MODEL:
        with extractor.override(model=TestModel()), validator.override(model=TestModel()):
            r = await pipeline("登录页 500 错误，需要尽快修复")
    else:
        r = await pipeline("登录页 500 错误，需要尽快修复")
    print("最终工单:", r.model_dump_json(indent=2))


# =====================================================================
# 模式 3：Handoff（前台分流）
# =====================================================================
class Routing(BaseModel):
    to: Literal["tech", "billing", "self"]
    reason: str


frontdesk = Agent(
    MODEL_NAME,
    output_type=Routing,
    system_prompt=(
        "你是客服前台，判断用户问题应该转给：tech（技术）、billing（账单）"
        "或 self（你自己直接回答闲聊）。reason 写一句话说明。"
    ),
)
tech = Agent(MODEL_NAME, system_prompt="你是技术支持，简洁专业地回复。")
billing = Agent(MODEL_NAME, system_prompt="你是账单专员，回答账单相关问题。")
AGENTS = {"tech": tech, "billing": billing}


async def handle_handoff(question: str) -> str:
    routing = await frontdesk.run(question)
    target = routing.output.to
    print(f"  → 前台决定: {target} ({routing.output.reason})")
    if target == "self":
        return routing.output.reason
    agent = AGENTS[target]
    answer = await agent.run(question, usage=routing.usage())
    return answer.output


async def demo_handoff() -> None:
    print("\n===== 模式 3: Handoff =====")
    if USE_TEST_MODEL:
        # TestModel 编出 routing.to 不可控，这里给 frontdesk 指定固定输出
        ctx = (
            frontdesk.override(model=TestModel(custom_output_args={"to": "tech", "reason": "服务器报错"})),
            tech.override(model=TestModel()),
            billing.override(model=TestModel()),
        )
        with ctx[0], ctx[1], ctx[2]:
            print("用户: 我登录时报 500 错误")
            print("回复:", await handle_handoff("我登录时报 500 错误"))
    else:
        for q in ["我登录时报 500 错误", "我的发票什么时候开？"]:
            print(f"用户: {q}")
            print("回复:", await handle_handoff(q))


# =====================================================================
# 模式 4：Message Bus（asyncio.Queue + 多个 worker）
# =====================================================================
summarizer = Agent(MODEL_NAME, system_prompt="一句话总结这条日志。")
alerter = Agent(MODEL_NAME, system_prompt="判断这条日志是否需要告警，只回 yes 或 no。")


async def demo_message_bus() -> None:
    print("\n===== 模式 4: Message Bus =====")
    bus: asyncio.Queue[str | None] = asyncio.Queue()

    async def producer():
        for log in [
            "服务 A 启动完成",
            "数据库连接超时",
            "GC 触发，耗时 2s",
        ]:
            await bus.put(log)
        await bus.put(None)  # 哨兵
        await bus.put(None)

    async def consumer(name: str, agent: Agent):
        while True:
            msg = await bus.get()
            if msg is None:
                break
            if USE_TEST_MODEL:
                with agent.override(model=TestModel()):
                    r = await agent.run(msg)
            else:
                r = await agent.run(msg)
            print(f"  [{name}] {msg!r} -> {r.output}")

    await asyncio.gather(
        producer(),
        consumer("summary", summarizer),
        consumer("alert", alerter),
    )


# =====================================================================
async def main() -> None:
    if USE_TEST_MODEL:
        print("[!] 未检测到 OPENAI_API_KEY，整个 demo 使用 TestModel 离线运行。\n"
              "    要看真实输出请设置 OPENAI_API_KEY 后重跑。")
    await demo_agent_as_tool()
    await demo_orchestrator()
    await demo_handoff()
    await demo_message_bus()


if __name__ == "__main__":
    asyncio.run(main())
