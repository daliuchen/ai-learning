"""LangChain 1.4+ 内置的 langchain.mcp.MCPAdapter：把 MCP Server 的工具变成 LangChain 工具，交给 create_agent

注意：langchain-mcp-adapters 0.3.x 依赖 mcp<2，装不进 mcp 2.x 的环境；
      langchain.mcp.MCPAdapter（基于独立的 fastmcp 包）与 mcp 2.x 兼容，本 demo 用它。

跑法（千问 qwen-flash，OpenAI 兼容接口；不要把 key 写进代码）:
    export OPENAI_API_KEY=...  OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1  QWEN_MODEL=qwen-flash
    export LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false
    python demos/integration/03_langchain_mcp.py
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from langchain.agents import create_agent
from langchain.mcp import MCPAdapter
from langchain_openai import ChatOpenAI

SERVER = Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py"


async def main() -> None:
    # target 可以是：http(s) URL / 本地脚本 Path（走 stdio）/ fastmcp Client / MCPConfig / 进程内 Server
    # 注意：裸字符串只能是 URL，本地脚本必须写 Path(...)，防止配置里的字符串悄悄变成「启动子进程」
    async with MCPAdapter(SERVER) as adapter:
        tools = await adapter.list_tools()
        print("加载工具:", [t.name for t in tools])

        # 显式传 base_url：langchain-openai 会优先读 OPENAI_API_BASE（可能指向别的网关），别指望 OPENAI_BASE_URL 一定生效
        model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash"), base_url=os.environ["OPENAI_BASE_URL"])
        agent = create_agent(model, tools)
        result = await agent.ainvoke({"messages": [{"role": "user", "content": "现在纽约几点？再算一下 17 加 25。"}]})
        for m in result["messages"]:
            text = m.content if isinstance(m.content, str) else str(m.content)  # 工具消息的 content 可能是内容块列表
            print(f"[{m.type}]", text[:120].replace("\n", " "), getattr(m, "tool_calls", "") or "")


if __name__ == "__main__":
    asyncio.run(main())
