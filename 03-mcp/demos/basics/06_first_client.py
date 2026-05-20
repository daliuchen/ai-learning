"""自己写的 Client —— 启 06_first_server 子进程并跑完整流程

跑法:
    python demos/basics/06_first_client.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER_PATH = Path(__file__).parent / "06_first_server.py"


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,  # 用当前同一个 Python 解释器避免环境问题
        args=[str(SERVER_PATH)],
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # ===== 1. 握手 =====
            init = await session.initialize()
            print(f"🤝 已连接 {init.serverInfo.name} v{init.serverInfo.version}")
            print(f"   协议版本：{init.protocolVersion}")

            # ===== 2. 列举 Tools / Resources / Prompts =====
            tools = await session.list_tools()
            print(f"\n📦 工具数量：{len(tools.tools)}")
            for t in tools.tools:
                print(f"   - {t.name}: {t.description}")

            templates = await session.list_resource_templates()
            print(f"\n📚 资源模板：{len(templates.resourceTemplates)}")
            for r in templates.resourceTemplates:
                print(f"   - {r.uriTemplate}")

            prompts = await session.list_prompts()
            print(f"\n💡 Prompt：{len(prompts.prompts)}")
            for p in prompts.prompts:
                print(f"   - {p.name}: {p.description}")

            # ===== 3. 调一个 Tool =====
            print("\n🔧 调用 add(40, 2)：")
            result = await session.call_tool("add", {"a": 40, "b": 2})
            print(f"   → {result.content[0].text}")

            print("\n🔧 调用 current_time(timezone='America/New_York')：")
            result = await session.call_tool(
                "current_time", {"timezone": "America/New_York"}
            )
            print(f"   → {result.content[0].text}")

            # ===== 4. 读一个 Resource =====
            print("\n📖 读 hello://greeting/Claude：")
            res = await session.read_resource("hello://greeting/Claude")
            print(f"   → {res.contents[0].text}")

            # ===== 5. 拿一个 Prompt =====
            print("\n📝 拿 code-review 模板：")
            pr = await session.get_prompt("code-review", {"file": "auth.py"})
            print(f"   → 共 {len(pr.messages)} 条消息")
            for m in pr.messages:
                text = getattr(m.content, "text", str(m.content))
                snippet = text[:80].replace("\n", " ")
                print(f"      [{m.role}] {snippet}...")


if __name__ == "__main__":
    asyncio.run(main())
