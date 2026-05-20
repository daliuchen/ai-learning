"""
04_logfire.py
=============
给客服 Agent 加 Logfire trace 的最小完整示例。

特点：
- 用 logfire.configure(console=True, send_to_logfire=False) 本地跑，不需要 token
- 自动 instrument 所有 Pydantic AI Agent
- 业务侧 / 工具侧加自定义 span
- 没 API Key 时用 TestModel 也能完整跑通

运行：
    python demos/modules/04_logfire.py

可选：设置 LOGFIRE_TOKEN 把 trace 也上传到 Logfire 云端
    export LOGFIRE_TOKEN=lf_xxx
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

import logfire
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

load_dotenv()


# =====================================================================
# 1) Logfire 初始化
# =====================================================================
logfire.configure(
    service_name="customer-service-demo",
    service_version="0.1.0",
    environment="dev",
    # 没 token 时不上报，只在 console 打 trace；有 token 自动上报
    send_to_logfire="if-token-present",
    console=logfire.ConsoleOptions(verbose=False),
)
logfire.instrument_pydantic_ai()


# =====================================================================
# 2) 构造 Agent（有 key 用 GPT-4o-mini，没 key 用 TestModel）
# =====================================================================
def build_agent() -> Agent:
    if os.getenv("OPENAI_API_KEY"):
        model: str | TestModel = "openai:gpt-4o-mini"
    else:
        print("[info] OPENAI_API_KEY 未设置，fallback 到 TestModel")
        model = TestModel(
            custom_output_text="您的订单 A001 已发货，预计明天送达。",
        )

    agent = Agent(
        model,
        system_prompt=(
            "你是电商客服小助手。用户问订单情况时，"
            "调用 lookup_order 查订单状态，然后用中文简短回复。"
        ),
    )

    # 工具里加自定义 span：模拟一次 DB 查询
    @agent.tool_plain
    def lookup_order(order_id: str) -> dict:
        """根据订单号查询订单状态"""
        with logfire.span("db.lookup_order", order_id=order_id) as span:
            # 假装查 DB
            result = {"order_id": order_id, "status": "shipped", "eta": "2026-05-21"}
            span.set_attribute("status", result["status"])
            return result

    return agent


# =====================================================================
# 3) 业务处理函数：用 span 把 Agent run 包起来
# =====================================================================
async def handle_request(agent: Agent, user_id: str, msg: str) -> str:
    with logfire.span(
        "customer_request",
        user_id=user_id,
        message_len=len(msg),
    ) as span:
        result = await agent.run(msg)
        span.set_attribute("output_len", len(result.output))
        logfire.info(
            "request_done",
            user_id=user_id,
            output_preview=result.output[:50],
        )
        return result.output


# =====================================================================
# 4) 跑几个例子
# =====================================================================
async def main() -> None:
    agent = build_agent()

    cases = [
        ("u_001", "请问我的订单 A001 现在到哪了？"),
        ("u_002", "订单 B002 什么时候发货？"),
        ("u_003", "我想取消订单 C003，可以吗？"),
    ]

    for uid, msg in cases:
        print(f"\n========== {uid} 问: {msg} ==========")
        out = await handle_request(agent, uid, msg)
        print(f"Agent 回: {out}")

    print(
        "\n[done] 没 token 时 trace 已经打在上面的 stdout 里。"
        "设置 LOGFIRE_TOKEN 后再跑一次，dashboard 会出现 customer-service-demo 服务。"
    )


if __name__ == "__main__":
    asyncio.run(main())
