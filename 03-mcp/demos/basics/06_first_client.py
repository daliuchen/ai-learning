"""自己写的 Client —— 启 06_first_server 子进程并跑完整流程（mcp 2.x 的 Client）

跑法:
    python demos/basics/06_first_client.py

要点：v2 只需要一个 `Client` 对象，`async with` 进入时自动完成
「server/discover 探测 → 失败则回退 initialize 握手」，不用再手写 session.initialize()。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

SERVER_PATH = Path(__file__).parent / "06_first_server.py"


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,  # 用当前同一个 Python 解释器避免环境问题
        args=[str(SERVER_PATH)],
    )

    async with Client(params) as client:
        # ===== 1. 连接（协商已在 async with 里做完）=====
        info = client.server_info  # 2026 版 Server 的身份是可选的，可能为 None
        print(f"🤝 已连接 {info.name if info else '(匿名)'}")
        print(f"   协议版本：{client.protocol_version}")
        print(f"   能力：{client.server_capabilities.model_dump(exclude_none=True)}")

        # ===== 2. 列举 Tools / Resources / Prompts =====
        tools = await client.list_tools()
        print(f"\n📦 工具数量：{len(tools.tools)}")
        for t in tools.tools:
            print(f"   - {t.name}: {(t.description or '').splitlines()[0]}")

        templates = await client.list_resource_templates()
        print(f"\n📚 资源模板：{len(templates.resource_templates)}")
        for r in templates.resource_templates:
            print(f"   - {r.uri_template}")

        prompts = await client.list_prompts()
        print(f"\n💡 Prompt：{len(prompts.prompts)}")
        for p in prompts.prompts:
            print(f"   - {p.name}: {(p.description or '').splitlines()[0]}")

        # ===== 3. 调一个 Tool =====
        print("\n🔧 调用 add(40, 2)：")
        result = await client.call_tool("add", {"a": 40, "b": 2})
        print(f"   → {result.content[0].text}  structured={result.structured_content}")

        print("\n🔧 调用 current_time(timezone='America/New_York')：")
        result = await client.call_tool("current_time", {"timezone": "America/New_York"})
        print(f"   → {result.content[0].text}")

        # ===== 4. 读一个 Resource =====
        print("\n📖 读 hello://greeting/Claude：")
        res = await client.read_resource("hello://greeting/Claude")
        print(f"   → {res.contents[0].text}")

        # ===== 5. 拿一个 Prompt =====
        print("\n📝 拿 code-review 模板：")
        pr = await client.get_prompt("code-review", {"file": "auth.py"})
        print(f"   → 共 {len(pr.messages)} 条消息")
        for m in pr.messages:
            text = getattr(m.content, "text", str(m.content))
            snippet = text[:80].replace("\n", " ")
            print(f"      [{m.role}] {snippet}...")


if __name__ == "__main__":
    asyncio.run(main())
