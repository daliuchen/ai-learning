"""Prompt 演示：单参数、多轮对话、嵌入 Resource（mcp 2.x）

跑法:
    python demos/server/03_prompts_travel.py             # stdio
    python demos/server/03_prompts_travel.py --selftest  # 进程内自测
"""
from __future__ import annotations

import asyncio
import sys

from mcp.server import MCPServer
from mcp.server.mcpserver import Message, UserMessage, AssistantMessage
from mcp.types import EmbeddedResource, TextResourceContents

mcp = MCPServer("travel-server")

PREFERENCES = "偏好：靠窗、晨班、最多 1 次中转。"


@mcp.resource("travel://preferences", mime_type="text/plain")
def preferences() -> str:
    return PREFERENCES


@mcp.prompt(name="plan-vacation", title="规划假期")
def plan_vacation(destination: str, days: int = 7, budget: int = 3000) -> str:
    """规划一次假期"""
    return (
        f"请帮我规划去 {destination} 的 {days} 天行程，预算 ${budget}。\n"
        f"流程：\n1. 查航班并解释为什么选某班\n2. 给酒店建议并按 budget 过滤\n3. 排个粗略 day-by-day 行程"
    )


@mcp.prompt(name="post-trip-report")
def post_trip_report(destination: str) -> list[Message]:
    """旅行结束后让模型帮你写游记（多轮开局）"""
    return [
        UserMessage(f"我刚结束 {destination} 的旅行，请帮我起标题、写开头、列 3 个值得纪念的瞬间。"),
        AssistantMessage("好的，请先告诉我整体感受关键词（比如：放松、惊喜、累、感动）。"),
    ]


@mcp.prompt(name="plan-with-preferences")
def plan_with_preferences(destination: str) -> list[Message]:
    """把偏好作为附件（EmbeddedResource）一起送给模型，模型不用再读一次资源"""
    return [
        UserMessage(
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(uri="travel://preferences", mime_type="text/plain", text=PREFERENCES),
            )
        ),
        UserMessage(f"按上面的偏好，帮我规划去 {destination} 的行程。"),
    ]


async def selftest() -> None:
    from mcp import Client

    async with Client(mcp) as client:
        for p in (await client.list_prompts()).prompts:
            print(p.name, "|", [(a.name, a.required) for a in (p.arguments or [])])
        r = await client.get_prompt("plan-vacation", {"destination": "巴塞罗那", "days": "5"})  # 参数在线上是字符串
        print(r.messages[0].role, r.messages[0].content.text.splitlines()[0])
        r = await client.get_prompt("post-trip-report", {"destination": "京都"})
        print([m.role for m in r.messages])
        r = await client.get_prompt("plan-with-preferences", {"destination": "东京"})
        print([m.content.type for m in r.messages])


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        asyncio.run(selftest())
    else:
        mcp.run()
