"""Pydantic AI 2.x 接 MCP：MCPToolset（本地 stdio 脚本）+ MCP capability（URL）+ 工具前缀 + 工具错误策略

Pydantic AI 的 MCP 客户端建立在独立的 `fastmcp` 包之上（pip install "pydantic-ai-slim[mcp,openai]"），
与官方 `mcp` 2.x SDK 可以共存（本 demo 的 Server 就是用官方 mcp 2.x 写的）。

跑法（千问 qwen-flash）:
    export OPENAI_API_KEY=...  OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1  QWEN_MODEL=qwen-flash
    python demos/integration/04_pydantic_ai_mcp.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

SERVER = Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py"


def qwen() -> OpenAIChatModel:
    return OpenAIChatModel(
        os.environ.get("QWEN_MODEL", "qwen-flash"),
        provider=OpenAIProvider(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"]),
    )


async def main() -> None:
    # 1) MCPToolset：第一个参数可以是 URL / 脚本路径 / FastMCP transport / fastmcp.Client / 进程内 FastMCP Server
    #    脚本路径会直接用 stdio 启动；要指定解释器、参数、环境变量就用 StdioTransport
    toolset = MCPToolset(StdioTransport(command=sys.executable, args=[str(SERVER)]), id="hello")
    # .prefixed("hello")：给所有工具名加前缀，避免多 Server 同名冲突 → hello_add / hello_current_time
    agent = Agent(qwen(), toolsets=[toolset.prefixed("hello")])

    async with agent:  # 打开/关闭所有 MCPToolset 的连接（stdio 会启动/停止子进程），复用同一连接
        result = await agent.run("现在纽约几点？再算一下 17 加 25。")
        print(result.output)
        # 查看这次运行里实际发生的工具调用
        for msg in result.all_messages():
            for part in getattr(msg, "parts", []):
                if part.part_kind == "tool-call":
                    print("  [tool-call]", part.tool_name, part.args)


if __name__ == "__main__":
    asyncio.run(main())
