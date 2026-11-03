"""同一份 Client 代码演示三种传输：stdio 子进程 / Streamable HTTP（含自定义 Header）/ 进程内（mcp 2.x）

跑法（一条命令；内部会在后台起一个本地 HTTP Server）:
    python demos/client/02_transport_demo.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx2
import uvicorn

from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

SERVER = Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py"

echo = MCPServer("echo-http")


@echo.tool()
def who_am_i(ctx: Context) -> str:
    """回显请求里的 Authorization 头（演示 ctx.headers；stdio 下为 None）。注意：头是客户端可控输入，别当身份用"""
    return (ctx.headers or {}).get("authorization", "(no header)")


async def use_stdio() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env={"DEBUG": "1"})  # env 是在白名单之上追加
    async with Client(params) as client:
        print(f"[stdio ] {client.server_info.name}  protocol={client.protocol_version}  tools={len((await client.list_tools()).tools)}")


async def use_http(url: str) -> None:
    async with Client(url) as client:  # URL 字符串 = Streamable HTTP
        print(f"[http  ] {client.server_info.name}  protocol={client.protocol_version}")
    # 要带鉴权头 / 代理 / 自定义超时：自己建 httpx2.AsyncClient，交给 streamable_http_client
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer dev-token"}, timeout=httpx2.Timeout(30.0, read=300.0)) as http_client:
        async with Client(streamable_http_client(url, http_client=http_client)) as client:
            r = await client.call_tool("who_am_i", {})
            print(f"[http+h] who_am_i -> {r.content[0].text}")
    # mode="legacy"：强制走 2025 式 initialize 握手（带 Mcp-Session-Id），同一个 Server 同时服务两代客户端
    async with Client(url, mode="legacy") as client:
        print(f"[legacy] protocol={client.protocol_version}")


async def use_in_memory() -> None:
    async with Client(echo) as client:  # 传 Server 对象：进程内，无端口
        print(f"[memory] {client.server_info.name}  protocol={client.protocol_version}")


async def main() -> None:
    await use_stdio()
    server = uvicorn.Server(uvicorn.Config(echo.streamable_http_app(), port=8787, log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        await use_http("http://127.0.0.1:8787/mcp")
    finally:
        server.should_exit = True
        await task
    await use_in_memory()


if __name__ == "__main__":
    asyncio.run(main())
