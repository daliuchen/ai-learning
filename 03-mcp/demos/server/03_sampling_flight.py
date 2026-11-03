"""Sampling（已弃用，但 2.3 里仍可用）+ 推荐的替代方案：Server 直接调 LLM

跑法（一个文件包含 Server 与演示 Client，不需要 API Key；设了 OPENAI_API_KEY 时末尾还会演示「直连 LLM」）:
    python demos/server/03_sampling_flight.py
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated

from mcp import Client, MCPError
from mcp.client import ClientRequestContext
from mcp.server import MCPServer
from mcp.server.mcpserver import Resolve, Sample
from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    CreateMessageResultWithTools,
    SamplingMessage,
    TextContent,
)

mcp = MCPServer("flight-server")

FLIGHTS = [
    {"id": "CA1981", "dep": "08:00", "arr": "11:20", "price": 1280, "stops": 0},
    {"id": "MU5102", "dep": "13:00", "arr": "16:30", "price": 1100, "stops": 0},
    {"id": "HU7191", "dep": "22:00", "arr": "01:40", "price": 980, "stops": 0},
    {"id": "CZ3115", "dep": "06:30", "arr": "11:50", "price": 1050, "stops": 1},
]


# ============ 方案 A：Sampling，用 Resolve + Sample（同一份代码同时适配 2025 与 2026 的客户端）============
def ask_host_to_pick(user_pref: str) -> Sample:
    """解析器：描述想让 Client 的模型做什么。注意：这个请求在 MRTR 重试轮次间必须逐字相同，
    所以只能由工具参数等『稳定数据』构造（不要放时间戳、随机数）。"""
    prompt = f"用户偏好: {user_pref}\n航班选项:\n{json.dumps(FLIGHTS, ensure_ascii=False)}\n只返回最推荐的航班 ID，比如 CA1981。"
    return Sample([SamplingMessage(role="user", content=TextContent(type="text", text=prompt))], max_tokens=20)


@mcp.tool()
async def find_best_flight(
    user_pref: str,
    pick: Annotated[CreateMessageResult | CreateMessageResultWithTools, Resolve(ask_host_to_pick)],
) -> str:
    """给定用户偏好，借 Host 的 LLM 挑最优航班"""
    text = pick.content.text.strip().strip("'\"") if pick.content.type == "text" else ""
    best = next((f for f in FLIGHTS if f["id"] == text), None)
    return json.dumps(best, ensure_ascii=False) if best else f"模型返回了未知 ID: {text!r}"


# ============ 方案 B（推荐）：Server 直接调自己的 LLM，不再依赖 Sampling ============
@mcp.tool()
async def find_best_flight_direct(user_pref: str) -> str:
    """Server 自己持有 Key，直接调 OpenAI 兼容接口（如千问 qwen-flash）"""
    from openai import AsyncOpenAI

    llm = AsyncOpenAI()  # 读 OPENAI_API_KEY / OPENAI_BASE_URL（Server 作者自己管 Key 和账单）
    resp = await llm.chat.completions.create(
        model=os.environ.get("QWEN_MODEL", "qwen-flash"),
        max_tokens=20,
        messages=[{"role": "user", "content": f"用户偏好: {user_pref}\n航班: {json.dumps(FLIGHTS, ensure_ascii=False)}\n只返回最推荐的航班 ID。"}],
    )
    return (resp.choices[0].message.content or "").strip()


# ============ 演示 Client：sampling_callback = 你的「Host LLM」（这里用确定性假模型）============
async def host_llm(context: ClientRequestContext, params: CreateMessageRequestParams) -> CreateMessageResult:
    # 真实 Host 在这里：弹审批 UI → 路由到某个模型 → 再让用户审阅结果。这里直接挑最便宜的
    print("  [Host] 收到 sampling 请求，max_tokens =", params.max_tokens)
    cheapest = min(FLIGHTS, key=lambda f: f["price"])["id"]
    return CreateMessageResult(role="assistant", content=TextContent(type="text", text=cheapest), model="fake-llm")


async def host_llm_real(context: ClientRequestContext, params: CreateMessageRequestParams) -> CreateMessageResult:
    """真实 Host 的 sampling 回调：把 MCP 消息转成 OpenAI 格式，调真模型（千问 qwen-flash），再包回 MCP 结果"""
    from openai import AsyncOpenAI

    msgs = [{"role": m.role, "content": m.content.text} for m in params.messages if m.content.type == "text"]
    if params.system_prompt:
        msgs.insert(0, {"role": "system", "content": params.system_prompt})
    model = os.environ.get("QWEN_MODEL", "qwen-flash")
    resp = await AsyncOpenAI().chat.completions.create(model=model, messages=msgs, max_tokens=params.max_tokens)
    return CreateMessageResult(role="assistant", content=TextContent(type="text", text=resp.choices[0].message.content or ""), model=model)


async def main() -> None:
    for mode in ("auto", "legacy"):  # auto → 2026-07-28（多轮请求 MRTR）；legacy → 2025-11-25（独立的 sampling/createMessage 请求）
        async with Client(mcp, mode=mode, sampling_callback=host_llm) as client:
            r = await client.call_tool("find_best_flight", {"user_pref": "最便宜"})
            print(f"[{mode:6}] 协议 {client.protocol_version}: {r.content[0].text}")

    async with Client(mcp) as client:  # 没注册 sampling_callback = 没声明 sampling 能力
        try:
            await client.call_tool("find_best_flight", {"user_pref": "最便宜"})
        except MCPError as e:  # Server 不会把请求发给没声明能力的 Client，而是直接 -32021
            print("[no cb ]", e.code, e)

    if os.environ.get("OPENAI_API_KEY"):
        async with Client(mcp, sampling_callback=host_llm_real) as client:  # 真模型当 Host 的 LLM
            r = await client.call_tool("find_best_flight", {"user_pref": "最便宜"})
            print("[real  ] 真模型当 Host LLM:", r.content[0].text)
        async with Client(mcp) as client:
            r = await client.call_tool("find_best_flight_direct", {"user_pref": "最便宜"})
            print("[direct] 直连 LLM:", r.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
