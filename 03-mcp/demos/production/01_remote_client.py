"""连远程 Server：两代协议、进度回调、以及两个典型故障（421 Host 白名单、缺 session）"""
from __future__ import annotations

import asyncio
import os

import httpx2

from mcp import Client

URL = f"http://127.0.0.1:{os.getenv('PORT', '8780')}/mcp"
BASE = URL.rsplit("/mcp", 1)[0]


async def main() -> None:
    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        if int(progress) % 500 == 0:
            print(f"   [progress] {int(progress)}/{int(total or 0)}")

    for mode in ("auto", "legacy"):
        async with Client(URL, mode=mode) as client:
            r = await client.call_tool("slow_compute", {"n": 1000}, progress_callback=on_progress)
            print(f"[{mode:6}] 协议 {client.protocol_version} -> {r.structured_content}")

    async with httpx2.AsyncClient() as http:
        print("healthz:", (await http.get(f"{BASE}/healthz")).json())
        # 模拟反向代理后 Host 头不在白名单里：整个请求在到达 MCP 之前就被拒绝
        r = await http.post(URL, headers={"Host": "evil.example.com", "content-type": "application/json", "accept": "application/json, text/event-stream"}, content=b"{}")
        print("Host 不在白名单:", r.status_code, r.text.strip())


if __name__ == "__main__":
    asyncio.run(main())
