# MCP Server 04：Lifespan 与 Context —— 把 DB 连接、HTTP Client 等"注入"到工具里

> **一句话**：用 lifespan 在 Server 启动时初始化共享资源（DB、HTTP client、缓存），在 Server 关闭时优雅释放；用 Context 把这些资源注入到每个 Tool / Resource / Prompt。这是写"真实生产"MCP Server 必须掌握的两个机制。

---

## 1. 为什么需要 lifespan / context

错误写法很常见：

```python
# ❌ 错误：模块顶层建 DB 连接
import sqlalchemy
engine = sqlalchemy.create_engine("postgresql://...")

@mcp.tool()
def query(sql: str) -> list:
    with engine.connect() as conn:
        ...
```

问题：
- Server 进程崩了重启时 engine 不释放
- 写测试时没法 mock
- 多个工具共享状态没法隔离
- 想换 DB 时要改一堆地方

**正确做法**：
1. 用 lifespan 控制资源生命周期
2. 通过 Context 注入到每个工具

---

## 2. lifespan 基础

FastMCP 提供 `lifespan` 参数，接收一个 async generator：

```python
from contextlib import asynccontextmanager
from dataclasses import dataclass
from mcp.server.fastmcp import FastMCP


@dataclass
class AppContext:
    """全局共享状态"""
    db: object       # 实际是 SQLAlchemy engine / asyncpg pool / 任何东西
    http: object     # httpx.AsyncClient


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Server 整个生命周期共享的资源"""
    import httpx
    # ===== 启动时 =====
    db = await connect_db()
    http = httpx.AsyncClient(timeout=30)

    try:
        # 这一段是 Server 实际运行时段
        yield AppContext(db=db, http=http)
    finally:
        # ===== 关闭时 =====
        await db.close()
        await http.aclose()


mcp = FastMCP("my-server", lifespan=app_lifespan)
```

lifespan 执行时机：

```
1. FastMCP 启动
2. 调用 app_lifespan() → 跑到 yield 前的代码（建连接）
3. 进入 Server 主循环（处理请求）
4. Server 收到关闭信号
5. 跑 yield 后的代码（释放）
6. 进程退出
```

---

## 3. 在 Tool / Resource / Prompt 里拿到 lifespan 的资源

通过 `Context` 注入：

```python
from mcp.server.fastmcp import Context, FastMCP


@mcp.tool()
async def search_user(name: str, ctx: Context) -> list[dict]:
    """搜用户"""
    # 拿到 lifespan 里 yield 出来的对象
    app: AppContext = ctx.request_context.lifespan_context

    rows = await app.db.fetch(
        "SELECT id, name FROM users WHERE name ILIKE $1",
        f"%{name}%",
    )
    return [dict(r) for r in rows]


@mcp.tool()
async def call_external(url: str, ctx: Context) -> str:
    app: AppContext = ctx.request_context.lifespan_context
    r = await app.http.get(url)
    return r.text[:1000]
```

**类型提示技巧**：

```python
from mcp.server.fastmcp import Context, FastMCP

# 让 Context 知道 lifespan 类型，IDE 能补全
@mcp.tool()
async def f(ctx: Context["AppContext"]) -> str:
    app = ctx.request_context.lifespan_context  # 类型推断为 AppContext
    ...
```

> Python SDK 当前版本里 Context 泛型支持还在演进，写 `Context[AppContext]` 不是强约束。但带类型注解能让你的 IDE 知道字段。

---

## 4. Context 的其他能力

Context 不只是 lifespan 的入口，它还提供：

| 方法 | 作用 |
|------|------|
| `ctx.info(msg)` / `warning(msg)` / `error(msg)` / `debug(msg)` | 发日志通知给 Client |
| `ctx.report_progress(progress, total)` | 上报长任务进度 |
| `ctx.sample(messages, ...)` | 反向请求 Host 的 LLM（详见 03-client/03-sampling） |
| `ctx.elicit(message, schema)` | 反向问用户（详见 03-client/04-roots-elicitation） |
| `ctx.read_resource(uri)` | 调用本 Server 自己的 Resource |
| `ctx.session` | 拿到底层 ServerSession 对象，做底层操作 |
| `ctx.request_context.request_id` | 当前 JSON-RPC 请求 ID |
| `ctx.request_context.meta` | 请求的 `_meta` 字段 |

---

## 5. 一个真实例子：带 DB + Cache 的订单 Server

```python
# demos/server/04_lifespan_orders.py
"""演示 lifespan + Context：订单查询 Server"""
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from cachetools import TTLCache
from mcp.server.fastmcp import Context, FastMCP


@dataclass
class AppContext:
    http: httpx.AsyncClient
    cache: TTLCache  # 简单内存缓存：5 分钟、最多 1000 条


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    http = httpx.AsyncClient(
        base_url="https://api.example.com",
        timeout=30,
        headers={"Authorization": "Bearer demo-token"},
    )
    cache = TTLCache(maxsize=1000, ttl=300)
    try:
        yield AppContext(http=http, cache=cache)
    finally:
        await http.aclose()


mcp = FastMCP("orders-server", lifespan=app_lifespan)


@mcp.tool()
async def get_order(order_id: str, ctx: Context) -> dict:
    """查询订单（带 5 分钟缓存）"""
    app: AppContext = ctx.request_context.lifespan_context

    if order_id in app.cache:
        await ctx.debug(f"缓存命中: {order_id}")
        return app.cache[order_id]

    await ctx.info(f"调远程 API: {order_id}")
    r = await app.http.get(f"/orders/{order_id}")
    if r.status_code == 404:
        from mcp.server.fastmcp.exceptions import ToolError
        raise ToolError(f"订单不存在: {order_id}")
    r.raise_for_status()
    data = r.json()
    app.cache[order_id] = data
    return data


@mcp.tool()
async def list_recent_orders(days: int = 7, ctx: Context = None) -> list[dict]:
    """列最近 N 天订单"""
    app: AppContext = ctx.request_context.lifespan_context
    r = await app.http.get("/orders", params={"days": days})
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    mcp.run()
```

**关键点**：
- HTTP Client 全 Server 共用一个连接池（不是每次请求都建新连接）
- 缓存跨工具共用
- 关闭时 `aclose()` 释放连接

---

## 6. lifespan 错误处理

如果初始化失败（DB 连不上），lifespan 的 `try` 之前抛异常，Server 启动失败。

```python
@asynccontextmanager
async def app_lifespan(server):
    try:
        db = await connect_with_retry(max_attempts=3)
    except Exception as e:
        # 直接抛 → Server 启动失败
        raise RuntimeError(f"DB 不可用: {e}") from e

    try:
        yield AppContext(db=db)
    finally:
        await db.close()
```

> 优雅退化：如果你想"DB 不可用时 Server 还能跑、只是某些工具报错"，把建连接挪到工具里按需建。但一般推荐 **fail fast**，让 Host 重启 Server。

---

## 7. 多个 lifespan 资源的组合

```python
@asynccontextmanager
async def app_lifespan(server):
    async with httpx.AsyncClient() as http:
        async with asyncpg.create_pool(DSN) as db:
            async with aio_pika.connect_robust(MQ_URL) as mq:
                yield AppContext(http=http, db=db, mq=mq)
```

`async with` 嵌套保证任何一个失败都能正确清理已建立的资源。或者用 `AsyncExitStack`：

```python
from contextlib import AsyncExitStack

@asynccontextmanager
async def app_lifespan(server):
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient())
        db = await stack.enter_async_context(asyncpg.create_pool(DSN))
        mq = await stack.enter_async_context(aio_pika.connect_robust(MQ_URL))
        yield AppContext(http=http, db=db, mq=mq)
```

---

## 8. 同时支持 stdio 和 HTTP 部署

lifespan 跟传输无关——同一份代码既能跑 stdio 也能跑 streamable-http：

```python
# 命令行参数决定传输
import sys

if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # 默认 stdio
```

---

## 9. 测试 lifespan 与 Context

```python
# tests/test_orders.py
import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

@pytest.mark.asyncio
async def test_get_order():
    params = StdioServerParameters(
        command="python",
        args=["demos/server/04_lifespan_orders.py"],
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool("get_order", {"order_id": "abc"})
            assert not result.isError
```

测试 lifespan 是否被正确调用，最直接的方式是端到端跑一遍。

---

## 10. 低层 Server 类的等价写法

如果你用低层 `mcp.server.Server` 而非 FastMCP：

```python
from mcp.server import Server, NotificationOptions
from mcp.server.lowlevel.server import Server as LowLevelServer

app = LowLevelServer("low-level")

@asynccontextmanager
async def app_lifespan(server):
    db = await connect_db()
    try:
        yield {"db": db}
    finally:
        await db.close()

app.lifespan = app_lifespan

@app.call_tool()
async def call_tool(name, args):
    db = app.request_context.lifespan_context["db"]
    ...
```

低层 API 不强制 `dataclass`，可以用任意对象作 context。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **lifespan 没生效 / 资源没释放** | 必须用 `@asynccontextmanager` + `yield`，不能写成普通 async function |
| **`ctx: Context` 没用上类型注解** | FastMCP 用类型注解识别要不要注入；写 `ctx` 不带 `: Context` 会被当普通参数 |
| **多个 Tool 想共享状态没用 lifespan** | 用全局变量虽然能跑，但测试 / 多实例 / 资源释放都会出问题 |
| **lifespan 阻塞太久导致启动慢** | 用 `asyncio.gather` 并发初始化多个资源 |
| **lifespan 里 print** | 同样会污染 stdio，用 logging 到 stderr |
| **想根据 Client 切换 context** | lifespan 是**全 Server 共享**的；按 Client 切换要在 Tool 里基于 `ctx.session` 做 |

---

## 12. 下一步

- 📖 Completion + Pagination → [05-completion-pagination.md](./05-completion-pagination.md)
- 📖 Logging / Progress / Ping → [06-logging-progress-ping.md](./06-logging-progress-ping.md)
- 📖 Tasks 扩展（让长任务异步可恢复） → [07-tasks.md](./07-tasks.md)

## 参考资料

- FastMCP `lifespan` 源码：https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/fastmcp/server.py
- Context 用法参考：https://github.com/modelcontextprotocol/python-sdk#context
