"""老方案：langchain-mcp-adapters 0.3.x（依赖 mcp<2，所以必须在 **单独的** 环境里装）

Server 可以仍然用 mcp 2.x 写，Client 这一侧的环境装 `langchain-mcp-adapters`（会带 mcp 1.x）：
    conda create -n lc-adapters python=3.12 -y && conda activate lc-adapters
    pip install "langchain-mcp-adapters==0.3.2" langchain langchain-openai
    # SERVER_PYTHON 指向装了 mcp 2.x 的那个环境的 python，用来启动 06_first_server.py
    SERVER_PYTHON=/path/to/mcp2-env/bin/python python demos/integration/03_langchain_mcp_adapters_legacy.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI

SERVER = Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py"


async def main() -> None:
    client = MultiServerMCPClient(
        {
            "hello": {
                "transport": "stdio",
                "command": os.environ.get("SERVER_PYTHON", sys.executable),
                "args": [str(SERVER)],
            },
            # 远程 Server：{"transport": "http", "url": "http://localhost:8000/mcp"}（0.3.x 里 streamable_http 与 http 都可）
        }
    )
    tools = await client.get_tools()  # 工具名默认不带 server 前缀；多个 Server 同名时用 tool_name_prefix=True
    print("加载工具:", [t.name for t in tools])

    model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash"), base_url=os.environ["OPENAI_BASE_URL"])
    agent = create_agent(model, tools)
    result = await agent.ainvoke({"messages": [{"role": "user", "content": "17 加 25 等于几？"}]})
    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
