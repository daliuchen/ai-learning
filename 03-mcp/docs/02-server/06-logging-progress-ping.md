# MCP Server 06：日志、进度、取消与 Ping

> 一个跑得久的工具要处理好三件事：用 progress 通知上报进度，被取消时干净地收尾，给运维留下日志。2026-07-28 弃用了协议层 Logging，删除了 `ping`，日志回到 Python 标准库 `logging` 和 OpenTelemetry，探活交给传输层。这篇讲这几件事在 Python SDK 2.x 里怎么做。

---

## 1. 四个 utility 的现状（2026-07-28 之后）

| Utility | 方向 | 状态 | 怎么做 |
|---------|------|------|--------|
| Logging（`notifications/message`、`logging/setLevel`） | Server → Client | 已弃用（SEP-2577）；`logging/setLevel` 在 2026 版里已移除 | stdio 下写 stderr；HTTP 下用标准库 `logging` + OpenTelemetry |
| Progress | Server → Client | 保留；2026 起只剩 Server → Client 方向 | `ctx.report_progress(...)` |
| Ping | 双向 | 删除（2025 版老连接上仍可用） | 用 `server/discover`、`tools/list` 或 HTTP 健康检查探活 |
| Cancellation | 任一方 → 对方 | 保留 | SDK 自动取消处理函数，你负责写好 `finally` |

---

## 2. Logging：回到标准库

### 2.1 为什么被弃用

协议层日志的做法是：Server 通过 `ctx.info()` 之类的方法，把日志作为通知推给 Client。2026-07-28 认为这个能力很少有客户端认真用，和 stderr、OpenTelemetry 重复，还把运维日志和协议流量混在了一起。SEP-2577 把它标成 Deprecated（功能至少保留 12 个月，但不建议新实现再用），官方的建议是：

- stdio 下写 stderr
- 其它情况用 OpenTelemetry（见[调试与可观测（Inspector、日志与追踪）](../05-production/05-debugging-inspector.md)）
- 过渡期内，Client 如果还想要协议日志，就在每个请求的 `_meta` 里带上 `io.modelcontextprotocol/logLevel`；请求里没有这个字段时，Server 不得发送 `notifications/message`（2025 版会话级的 `logging/setLevel` 已经删了）

在 Python SDK 里调用 `ctx.info()` / `ctx.warning()` 仍然有效，但会触发 `MCPDeprecationWarning`（它是一个 `UserWarning`，默认会打印出来）。

### 2.2 推荐写法

```python
import logging
from mcp.server import MCPServer

logger = logging.getLogger(__name__)     # 模块顶层建一次
mcp = MCPServer("demo", log_level="DEBUG")   # 构造时已帮你 basicConfig，handler 指向 stderr；默认 INFO

@mcp.tool()
def heavy_task(n: int) -> str:
    logger.info("开始处理 n=%s", n)
    try:
        result = compute(n)
    except Exception:
        logger.exception("出错")   # 工具里未捕获的异常 SDK 也会记 ERROR 带堆栈
        raise
    return str(result)
```

- 日志是给运维看的，模型永远看不到。想让模型读到的内容，用 `return` 或 `raise ToolError`
- stdio 下 stdout 是协议通道，别用 `print()`。SDK 会把已经 flush 的零散 stdout 输出转到 stderr，但没 flush 的 `print` 可能在进程退出时才写进协议流。`logging` 的 handler 每条都会 flush，所以用它
- 如果你已经自己配置过 logging，`MCPServer` 不会覆盖（`basicConfig` 不替换已有的 handler）
- 想追踪每个请求的耗时和成败，别靠日志。SDK 默认就把每条消息包成 OpenTelemetry span，只有装了 exporter 才会产生开销

### 2.3 日志级别怎么用

info 记关键节点，debug 记详细过程（生产环境关掉），warning 记能继续但有问题的情况，error 在抛错前留一笔。日志里不要放 token、用户隐私这类敏感信息。需要结构化日志时可以用 `structlog` 之类的库，把 JSON 输出到 stderr，再交给采集器。

---

## 3. Progress

### 3.1 协议

调用方在请求的 `_meta` 里放一个 `progressToken`：

```json
{
  "method": "tools/call",
  "params": {
    "name": "scrape",
    "arguments": {"url": "..."},
    "_meta": {"progressToken": "abc-123"}
  }
}
```

被调用方发送进度通知：

```json
{
  "method": "notifications/progress",
  "params": {
    "progressToken": "abc-123",
    "progress": 47,
    "total": 100,
    "message": "已抓取 47 页"
  }
}
```

`progress` 必须单调递增；`total` 可选，不传表示总量未知。2026-07-28 里进度通知只能从 Server 发往 Client（Client → Server 方向的进度已弃用），而且和所有请求级通知一样，只会出现在该请求自己的响应流里（Streamable HTTP 下就是那个 POST 的 SSE 响应）。

### 3.2 Python SDK

服务端用 `ctx.report_progress(progress, total=None, message=None)`：

```python
@mcp.tool()
async def scrape(url: str, ctx: Context) -> str:
    pages = await discover(url)
    for i, p in enumerate(pages):
        await fetch(p)
        await ctx.report_progress(i + 1, total=len(pages), message=f"抓取 {p}")
    return f"完成 {len(pages)} 页"
```

- 调用方没要进度（请求里没有 `progressToken`）时，`report_progress` 什么也不做，所以可以无条件上报
- 不知道真实总数就别传 `total`，客户端回调收到的会是 `total=None`

客户端在每次调用时传入回调，而不是挂在 `Client` 上：

```python
async def on_progress(progress: float, total: float | None, message: str | None) -> None:
    print(f"进度 {progress}/{total}: {message}")

result = await client.call_tool("scrape", {"url": "..."}, progress_callback=on_progress)
```

> 在真实的传输上，通知和结果是分别送达的，慢一点的回调可能在 `call_tool` 返回之后还在执行。只有进程内的测试连接能保证所有进度都先于结果到达。

### 3.3 几点建议

进度别发得太密，每秒少于 10 条就够了。没有真实总数时不要传 `total`，免得显示一个假的百分比。规范允许 Client 收到进度通知时重置请求超时，但 Server 不应该靠这个无限续命，最好配合一个最大超时。

---

## 4. Ping：2026-07-28 起移除

2025 版里，`ping` 是一个双向的空请求，用来探活和给 SSE 长连接保活。2026-07-28 把它整个删了：既然没有长期存在的会话，协议层也就不需要探测对方还在不在。替代做法：

| 以前 | 现在 |
|------|------|
| 探测 Server 是否可达 | `server/discover`（带缓存提示，开销小），或者一次 `tools/list`；HTTP 上用普通的健康检查端点（见[用 Streamable HTTP 部署远程 MCP](../05-production/01-remote-mcp.md)） |
| 给 SSE 长连接保活 | 交给传输层或反向代理的 keep-alive；订阅流断了就重新 `subscriptions/listen`（流不能恢复，也不是重放日志） |
| stdio 探活 | 一般不需要，子进程退出时管道会直接断开 |

对 2025 版的老连接，SDK 仍然会应答 `ping`（`{"method":"ping"}` → `{"result":{}}`）；对 2026 版连接发 `ping` 会得到 `-32601 Method not found`。

---

## 5. Cancellation

### 5.1 协议

发起方想撤回已经发出的请求时：

```json
{
  "method": "notifications/cancelled",
  "params": {"requestId": 42, "reason": "user_cancelled"}
}
```

接收方收到后应当立即停止处理，不再发送响应，并释放资源。这是一条通知，没有响应。

### 5.2 Python SDK：v2 会真正取消你的函数

Client 的请求超时或者调用被取消时，SDK 现在会真正取消 Server 端的处理函数（v1 不会）。`async def` 函数会在正在 `await` 的地方收到取消异常，函数展开，返回值被丢弃。大多数工具什么都不用做，只有两类需要处理。

第一类是有资源要清理的 `async def` 工具。清理逻辑放进 `finally`；如果清理时还要 `await`，必须加 `shield` 并设定时限：

```python
import anyio

@mcp.tool()
async def order_book(title: str) -> str:
    """下单并占住库存，等待付款"""
    holds.add(title)
    try:
        await take_payment(title)
        return f"Ordered {title!r}."
    finally:
        # 被取消的协程里，后续每个 await 都会立刻再抛；shield 才能让清理跑完
        with anyio.move_on_after(5, shield=True):
            await release_hold(title)
```

第二类是普通的 `def` 工具。它跑在工作线程里，外部没法打断线程，只能由工具自己定期检查：

```python
import anyio.from_thread

@mcp.tool()
def rebuild_index(titles: list[str]) -> str:
    offline.add("search")
    try:
        for title in titles:
            anyio.from_thread.check_cancelled()   # 没取消时什么都不做，取消后会抛
            index_book(title)
        return f"Indexed {len(titles)} books."
    finally:
        offline.discard("search")
```

一个从不检查的 `def` 工具会一直跑到结束，然后结果被丢弃。

> v1 里常见的 `ctx.request_context.cancelled` 标志已经不是推荐写法，改用 `finally` 配合 anyio 的取消语义。

在客户端，取消调用所在的任务，或者设置 `read_timeout_seconds`，就会触发取消，SDK 会自动发出 `notifications/cancelled`：

```python
try:
    await client.call_tool("crawl", {...}, read_timeout_seconds=0.5)
except MCPError as e:
    print(e.code)    # -32001 REQUEST_TIMEOUT（v1 是 408；超时参数也从 timedelta 改成了 float 秒）
```

### 5.3 几点建议

清理用 `try/finally`，不要用 `except CancelledError`。取消异常绝对不能吞掉，必须让它继续往上抛。长时间运行的代码里要有 await 点（`await anyio.sleep(0)` 也行），否则没有地方可以被取消。写操作被取消时可能只做了一半，所以要设计成幂等的或可补偿的。

---

## 6. 综合 demo：进度、取消与标准库日志

```python
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
```

这个文件既是 Server 也是 Client（Client 会自动拉起 stdio 子进程），运行 `python demos/server/06_progress_cancel.py`。实际输出（节选）：

```
[PROGRESS] 33% — 抓取 https://example.com/page-1
[PROGRESS] 66% — 抓取 https://example.com/page-2
[PROGRESS] 100% — 抓取 https://example.com/page-3
结果: {  "crawled": 3,  "pages": [...] }
超时: -32001 Request 'tools/call' timed out
# Server 的 stderr 里可以看到被取消的那次调用仍然执行了 finally：「清理完成，已抓 2 页」
```

---

## 7. 常见坑

| 坑 | 排查 |
|----|------|
| 用 `print(...)` 打日志 | 会污染 stdio 通道；改用标准库 `logging`（写 stderr） |
| 还在用 `ctx.info()` / `ctx.warning()` | 已弃用，会触发 `MCPDeprecationWarning`，改用 `logging`。如果要继续服务老客户端又不想看到警告，可以过滤掉这个 warning |
| 进度通知一秒上百条 | UI 会卡；按实际进度发，别每次循环都发 |
| 进度值回退或重复 | 规范要求 `progress` 单调递增 |
| 吞掉了取消异常 | 会破坏取消语义；清理放进 `finally`，让异常继续往上抛 |
| 取消后清理时的 `await` 立刻又抛异常 | 用 `anyio.move_on_after(秒, shield=True)` 包起来 |
| 普通 `def` 工具取消不掉 | 线程无法从外部打断，要周期性调用 `anyio.from_thread.check_cancelled()` |
| 想用 `ping` 保活 | 2026 版已移除；订阅流断了就重新 listen，HTTP 靠健康检查和代理的 keep-alive |

---

## 8. 下一步

- 更彻底的异步长任务：[用 Tasks 扩展异步执行长请求](./07-tasks.md)
- 错误处理：[错误处理与参数校验](./08-errors-validation.md)
- 客户端怎么接收这些通知：[自己写 Client（连接、发现与调用工具）](../03-client/01-client-basics.md)
- OpenTelemetry：[调试与可观测（Inspector、日志与追踪）](../05-production/05-debugging-inspector.md)

## 参考资料

- Progress：https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/progress
- Cancellation：https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/cancellation
- Logging（已弃用）：https://modelcontextprotocol.io/specification/2026-07-28/deprecated
- SDK Progress / Cancellation / Logging：https://py.sdk.modelcontextprotocol.io/handlers/progress/ 、https://py.sdk.modelcontextprotocol.io/handlers/cancellation/ 、https://py.sdk.modelcontextprotocol.io/handlers/logging/
