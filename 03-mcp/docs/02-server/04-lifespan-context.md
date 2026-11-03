# MCP Server 04：用 Lifespan 和 Context 向工具注入共享资源

> 生产环境的 MCP Server 几乎都要用到两个机制：lifespan 负责在 Server 启动时初始化 DB、HTTP client、缓存这类共享资源，并在关闭时释放；Context 负责把这些资源注入每个 Tool、Resource 和 Prompt。这篇讲两者的用法，以及 v2 里 lifespan 行为的一处重要变化。

---

## 1. 为什么需要 lifespan 和 context

下面这种写法很常见：

```python
# ❌ 错误：模块顶层建 DB 连接
import sqlalchemy
engine = sqlalchemy.create_engine("postgresql://...")

@mcp.tool()
def query(sql: str) -> list:
    with engine.connect() as conn:
        ...
```

它的问题在于：Server 进程崩溃重启时 engine 不会被释放；写测试时没法 mock；多个工具共享的状态无法隔离；想换数据库时要改一堆地方。

更好的做法是用 lifespan 管理资源的生命周期，再通过 Context 把资源注入每个工具。

---

## 2. lifespan 基础

MCPServer 有一个 `lifespan` 参数，接收一个返回异步上下文管理器的函数，通常就是用 `@asynccontextmanager` 装饰的 async generator：

```python
from contextlib import asynccontextmanager
from dataclasses import dataclass
from mcp.server import MCPServer


@dataclass
class AppContext:
    """全局共享状态"""
    db: object       # 实际是 SQLAlchemy engine / asyncpg pool / 任何东西
    http: object     # httpx.AsyncClient


@asynccontextmanager
async def app_lifespan(server: MCPServer):
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


mcp = MCPServer("my-server", lifespan=app_lifespan)
```

lifespan 的执行时机：

```
1. MCPServer 启动
2. 调用 app_lifespan() → 跑到 yield 前的代码（建连接）
3. 进入 Server 主循环（处理请求）
4. Server 收到关闭信号
5. 跑 yield 后的代码（释放）
6. 进程退出
```

> v2 在这里有行为变化：Streamable HTTP 下，lifespan 只在服务启动时跑一次，所有会话、所有请求共享同一份状态；v1 是每个会话跑一次（`stateless_http=True` 时每个请求跑一次）。好处是连接池、缓存的开销小了很多；代价是那些按连接持有的资源（比如每个客户端一个 DB 事务）得挪到工具函数里去。

---

## 3. 在 Tool / Resource / Prompt 里拿到 lifespan 的资源

通过 `Context` 注入：

```python
from mcp.server import MCPServer
from mcp.server.mcpserver import Context


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

加上类型提示会更方便：

```python
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

# 让 Context 知道 lifespan 类型，IDE 能补全
@mcp.tool()
async def f(ctx: Context[AppContext]) -> str:
    app = ctx.request_context.lifespan_context  # 类型推断为 AppContext
    ...
```

> `Context[AppContext]` 是官方 lifespan 文档里的标准写法，只是类型提示，不影响运行。

---

## 4. Context 的其他能力

Context 不只是拿 lifespan 资源的入口，它还提供：

| 方法 | 作用 |
|------|------|
| `ctx.report_progress(progress, total, message)` | 上报长任务进度 |
| `ctx.read_resource(uri)` | 调用本 Server 自己的 Resource |
| `ctx.session` | 拿到底层 ServerSession 对象，用来发 list_changed 等通知 |
| `ctx.notify_tools_changed()` 等 | 通知 2026 版 `subscriptions/listen` 的订阅者 |
| `ctx.request_id` | 当前 JSON-RPC 请求 ID |
| `ctx.headers` | HTTP 请求头（stdio 下为 `None`） |
| `ctx.request_context.lifespan_context` | lifespan 里 yield 出来的对象 |
| `ctx.mcp_server` | 当前的 `MCPServer`（v1 叫 `ctx.fastmcp`） |
| ~~`ctx.info()` / `warning()` / `error()` / `debug()`~~ | 协议层日志，已弃用（SEP-2577），改用标准库 `logging` |
| ~~`ctx.sample(...)`~~ | Sampling 已弃用（[Sampling 让 Server 借用 Host 的 LLM（已弃用）](../03-client/03-sampling.md)） |
| `ctx.elicit(message, schema)` | 只在 2025 版老连接上可用；2026 版请用 `Resolve(Elicit(...))`（[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)） |

还有一类参数不给模型看、由你的函数来填充，可以用 `Resolve` 依赖（`Annotated[T, Resolve(fn)]`）。这是 v2 新增的、和 Context 并列的第二种注入机制，见 SDK 文档的 Dependencies 一节和 [Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)。

---

## 5. 一个实际例子：带 DB 和缓存的订单 Server

```python
# demos/server/04_lifespan_orders.py（节选，完整版含 --selftest；用 SQLite 内存库代替远程 API）
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
```

这段代码里：
- 整个 Server 共用一个 DB 连接，不是每次请求都新建
- 缓存在各个工具之间共用
- 关闭时在 `finally` 里调用 `db.close()` 释放连接
- 同步函数（`def`）在 v2 里跑在工作线程上，所以 SQLite 连接要设 `check_same_thread=False`
- 日志用标准库 `logging`，不会弄脏 stdio 的 stdout

---

## 6. lifespan 的错误处理

如果初始化失败（比如 DB 连不上），异常在 lifespan 的 `try` 之前抛出，Server 就启动不起来。

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

> 如果你希望 DB 不可用时 Server 照样能跑、只是部分工具报错，可以把建连接挪到工具里按需建立。不过一般我更推荐 fail fast，让 Host 去重启 Server。

---

## 7. 组合多个 lifespan 资源

```python
@asynccontextmanager
async def app_lifespan(server):
    async with httpx.AsyncClient() as http:
        async with asyncpg.create_pool(DSN) as db:
            async with aio_pika.connect_robust(MQ_URL) as mq:
                yield AppContext(http=http, db=db, mq=mq)
```

嵌套的 `async with` 能保证任何一个资源初始化失败时，已经建好的资源都会被正确清理。也可以用 `AsyncExitStack`：

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

lifespan 和传输方式无关，同一份代码既能跑 stdio，也能跑 streamable-http：

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

v2 的测试比 v1 简单很多。`Client` 可以直接接收 Server 对象，在进程内建立连接，不用起子进程，也不用开端口；进入 `async with` 时 lifespan 启动，退出时关闭。

```python
# tests/test_orders.py
import pytest
from mcp import Client
from demos.server.orders import mcp   # 你的 MCPServer 实例

@pytest.mark.asyncio
async def test_get_order():
    async with Client(mcp) as client:
        result = await client.call_tool("get_order", {"order_id": "A1"})
        assert not result.is_error
```

几点说明：

- v1 的辅助函数 `create_connected_server_and_client_session()` 已移除，直接用 `Client(mcp)`
- `Client(mcp)` 默认协商到 2026-07-28，所以在工具里用了 `ctx.elicit()` 的测试会失败（`NoBackChannelError`）。要么把提问改成 `Resolve(...)`，要么给测试 Client 传 `mode="legacy"`
- 工具函数里的异常会被转成 `is_error=True` 的结果，断言这个结果即可；崩溃堆栈在日志里，pytest 的 `caplog` 能抓到

---

## 10. 低层 Server 类的写法

如果用的是低层的 `mcp.server.Server` 而不是 MCPServer，`lifespan` 是构造参数，处理函数从第一个参数 `ctx` 上读取它：

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from mcp.server import Server, ServerRequestContext
from mcp.types import CallToolRequestParams, CallToolResult, TextContent

@asynccontextmanager
async def app_lifespan(server: Server) -> AsyncIterator[dict]:
    db = await connect_db()
    try:
        yield {"db": db}
    finally:
        await db.close()

async def call_tool(ctx: ServerRequestContext[dict], params: CallToolRequestParams) -> CallToolResult:
    db = ctx.lifespan_context["db"]           # v1 里是 app.request_context.lifespan_context
    ...

server = Server("low-level", lifespan=app_lifespan, on_call_tool=call_tool)
```

低层 API 不要求用 `dataclass`，任意对象都可以作为 context。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| lifespan 没生效，资源没释放 | 必须用 `@asynccontextmanager` 加 `yield`，不能写成普通的 async function |
| `ctx` 没写类型注解 | MCPServer 靠类型注解判断要不要注入，`ctx` 不带 `: Context` 会被当成普通参数 |
| 多个 Tool 共享状态却没用 lifespan | 全局变量虽然能跑，但测试、多实例和资源释放都会出问题 |
| lifespan 初始化太久，启动慢 | 用 `asyncio.gather` 并发初始化多个资源 |
| 在 lifespan 里 print | 一样会污染 stdio，用 logging 输出到 stderr |
| 从 v1 升级后连接池、缓存的数量变了 | v2 的 HTTP 模式下 lifespan 只跑一次、全局共享，不要再依赖每个会话一份 |
| 想按 Client 切换 context | lifespan 由整个 Server 共享；要按 Client 区分，就在 Tool 里根据 `ctx.session` 处理 |

---

## 12. 下一步

- Completion 与 Pagination：[参数补全（Completion）与分页（Pagination）](./05-completion-pagination.md)
- Logging、Progress 与取消：[日志、进度、取消与 Ping](./06-logging-progress-ping.md)
- Tasks 扩展，让长任务异步、可恢复：[用 Tasks 扩展异步执行长请求](./07-tasks.md)

## 参考资料

- MCPServer `lifespan` 源码：https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/mcpserver/server.py
- SDK Lifespan：https://py.sdk.modelcontextprotocol.io/handlers/lifespan/
- SDK Context：https://py.sdk.modelcontextprotocol.io/handlers/context/
- SDK Testing：https://py.sdk.modelcontextprotocol.io/get-started/testing/
