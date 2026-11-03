"""把 MCP Server 的工具喂给大模型，做一个最小 ReAct Agent（mcp 2.x Client + OpenAI 兼容接口）

默认用阿里云百炼千问 qwen-flash，任何 OpenAI 兼容服务都行：
    export OPENAI_API_KEY=...                  # 不要写进代码或提交
    export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    export QWEN_MODEL=qwen-flash
    python demos/client/01_minimal_agent.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from openai import AsyncOpenAI

from mcp import Client, StdioServerParameters
from mcp.types import TextContent

SERVER_PATH = Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py"
MODEL = os.environ.get("QWEN_MODEL", "qwen-flash")


async def main(question: str) -> None:
    llm = AsyncOpenAI()  # 读 OPENAI_API_KEY / OPENAI_BASE_URL
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER_PATH)])

    async with Client(params) as mcp_client:
        # 1. 从 MCP 拉所有工具，转成 OpenAI 的 tools 格式（input_schema 原样就是 JSON Schema）
        mcp_tools = (await mcp_client.list_tools()).tools
        tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema}}
            for t in mcp_tools
        ]

        messages: list[dict] = [{"role": "user", "content": question}]
        for _ in range(6):  # 最多 6 轮，防死循环
            resp = await llm.chat.completions.create(model=MODEL, messages=messages, tools=tools)
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                print(f"\n[模型]: {msg.content}")
                return

            # 2. 执行所有 tool_call，把结果塞回对话
            for call in msg.tool_calls:
                args = json.loads(call.function.arguments or "{}")
                print(f"[Tool] {call.function.name}({args})")
                result = await mcp_client.call_tool(call.function.name, args)
                text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
                if result.is_error:
                    text = f"[工具报错] {text}"  # 让模型看到并自我纠正
                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})


if __name__ == "__main__":
    asyncio.run(main("现在纽约几点？再算一下 7 加 8。"))
