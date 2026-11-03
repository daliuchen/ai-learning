"""进度 / 取消 / 日志（标准库 logging）综合 demo —— mcp 2.x

跑法（一个文件同时是 Server 和 Client，Client 会自动起 stdio 子进程）:
    python demos/server/06_progress_cancel.py
"""
from __future__ import annotations

import asyncio
import logging
import sys

import anyio

from mcp import Client, MCPError, StdioServerParameters
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

logger = logging.getLogger("crawler")  # 日志给「运维」看，写 stderr，模型看不到
mcp = MCPServer("crawler-demo")
CLEANED: list[int] = []


@mcp.tool()
async def crawl(url: str, ctx: Context, max_pages: int = 5) -> dict:
    """模拟爬取一个网站：上报进度；被取消时在 finally 里清理"""
    logger.info("开始爬取 %s，最多 %d 页", url, max_pages)
    found: list[str] = []
    try:
        for i in range(max_pages):
            await anyio.sleep(0.2)  # 模拟抓取；这是一个取消点
            found.append(f"{url}/page-{i + 1}")
            await ctx.report_progress(i + 1, total=max_pages, message=f"抓取 {found[-1]}")
        return {"crawled": len(found), "pages": found[:3]}
    finally:
        # 无论正常结束、抛错还是被取消，都会走到这里；需要 await 的清理要 shield 并设时限
        with anyio.move_on_after(2, shield=True):
            CLEANED.append(len(found))
            logger.info("清理完成，已抓 %d 页", len(found))


async def client_main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[__file__, "--server"])
    async with Client(params) as client:

        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            print(f"[PROGRESS] {int(progress / total * 100) if total else 0}% — {message}")

        r = await client.call_tool("crawl", {"url": "https://example.com", "max_pages": 3}, progress_callback=on_progress)
        print("结果:", r.content[0].text.replace("\n", ""))

        # 取消 = 客户端超时。SDK 会发 notifications/cancelled，并真的取消 Server 端的处理函数
        try:
            await client.call_tool("crawl", {"url": "https://example.com", "max_pages": 50}, read_timeout_seconds=0.5)
        except MCPError as e:
            print("超时:", e.code, e)  # -32001 REQUEST_TIMEOUT


if __name__ == "__main__":
    if "--server" in sys.argv:
        mcp.run()
    else:
        asyncio.run(client_main())
