"""lifespan + Context：订单查询 Server（用 SQLite 内存库当「DB」，无需外部服务）

跑法:
    python demos/server/04_lifespan_orders.py             # stdio
    python demos/server/04_lifespan_orders.py --selftest  # 进程内自测
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

logger = logging.getLogger(__name__)  # 日志走标准库（写 stderr），不再用 ctx.info


@dataclass
class AppContext:
    db: sqlite3.Connection
    cache: dict[str, tuple[float, dict]] = field(default_factory=dict)  # order_id -> (过期时间, 数据)
    ttl: float = 300.0


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
    # ===== 启动时：整个 Server 只跑一次（v2 里 HTTP 模式也是一次，v1 是每个会话一次）=====
    db = sqlite3.connect(":memory:", check_same_thread=False)  # 同步函数会在工作线程里跑，所以要允许跨线程
    db.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, amount REAL, status TEXT)")
    db.executemany("INSERT INTO orders VALUES (?,?,?)", [("A1", 99.5, "paid"), ("A2", 15.0, "shipped")])
    try:
        yield AppContext(db=db)
    finally:
        # ===== 关闭时 =====
        db.close()
        logger.info("db closed")


mcp = MCPServer("orders-server", lifespan=app_lifespan)


@mcp.tool()
async def get_order(order_id: str, ctx: Context[AppContext]) -> dict:
    """查询订单（带 5 分钟缓存）"""
    app = ctx.request_context.lifespan_context  # lifespan 里 yield 出来的对象

    hit = app.cache.get(order_id)
    if hit and hit[0] > time.monotonic():
        logger.debug("缓存命中: %s", order_id)
        return hit[1]

    row = app.db.execute("SELECT id, amount, status FROM orders WHERE id=?", (order_id,)).fetchone()
    if row is None:
        raise ToolError(f"订单不存在: {order_id}")  # 模型能读到，可纠正
    data = {"id": row[0], "amount": row[1], "status": row[2]}
    app.cache[order_id] = (time.monotonic() + app.ttl, data)
    return data


@mcp.tool()
def list_orders(ctx: Context[AppContext]) -> list[dict]:
    """列所有订单（同步函数：v2 里会在工作线程执行，不阻塞事件循环）"""
    app = ctx.request_context.lifespan_context
    return [dict(id=i, amount=a, status=s) for i, a, s in app.db.execute("SELECT id, amount, status FROM orders")]


async def selftest() -> None:
    from mcp import Client

    async with Client(mcp) as client:  # 进入时 lifespan 启动，退出时关闭
        print((await client.call_tool("get_order", {"order_id": "A1"})).content[0].text.replace("\n", ""))
        r = await client.call_tool("get_order", {"order_id": "ZZ"})
        print("is_error:", r.is_error, "|", r.content[0].text)
        print((await client.call_tool("list_orders", {})).structured_content)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        asyncio.run(selftest())
    elif "--http" in sys.argv:
        mcp.run("streamable-http", port=8000)
    else:
        mcp.run()
