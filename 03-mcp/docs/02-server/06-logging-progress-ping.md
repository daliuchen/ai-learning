# MCP Server 06：Logging / Progress / Ping / Cancellation —— 长任务的"心电图"

> **一句话**：Server 把工具执行过程通过 **logging notification** 告诉 Client、用 **progress notification** 上报百分比、用 **ping** 双向探活、收到 **cancellation notification** 时优雅终止。这一篇把这四件事和 Python SDK 落地一次性讲完。

---

## 1. 四个 utility 一张表对照

| Utility | 方向 | 何时发 | Capability |
|---------|------|--------|------------|
| **Logging** | Server → Client | 任何想让用户/开发者看到的运行时信息 | Server 声明 `logging` |
| **Progress** | Server → Client（或反向） | 长任务进度更新 | 隐式（带 progressToken 时自动） |
| **Ping** | 双向 | 探测对方还活着 | 无需声明（始终可用） |
| **Cancellation** | 任一方 → 对方 | 取消还在跑的请求 | 无需声明 |

---

## 2. Logging

### 2.1 协议

Server 声明能力：

```json
{"capabilities":{"logging":{}}}
```

Server 发：

```json
{
  "method": "notifications/message",
  "params": {
    "level": "info",
    "logger": "weather-server",
    "data": "正在查询 Beijing 的天气..."
  }
}
```

`level` 支持：`debug` / `info` / `notice` / `warning` / `error` / `critical` / `alert` / `emergency`（syslog 级别）。

Client 可以发请求设过滤等级：

```json
{
  "method": "logging/setLevel",
  "params": {"level": "warning"}
}
```

### 2.2 Python SDK

最方便的是 `Context.info` / `warning` / `error` 等：

```python
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("demo")

@mcp.tool()
async def heavy_task(n: int, ctx: Context) -> str:
    await ctx.debug(f"开始处理 n={n}")
    try:
        result = compute(n)
    except Exception as e:
        await ctx.error(f"出错: {e}")
        raise
    await ctx.info(f"完成: result={result}")
    return str(result)
```

Client 端可以注册回调接收：

```python
async def on_log(params):
    print(f"[{params.level}] {params.data}")

async with ClientSession(read, write, logging_callback=on_log) as session:
    ...
```

### 2.3 设计建议

- **info**：默认级别，给用户看的进展（"正在查询..."）
- **debug**：详细 trace，开发用，生产关
- **warning**：能继续但有问题（缓存失效、降级）
- **error**：抛错前留一笔，便于排查
- **不要在 logging 里塞秘密**：Logging 会被 Host 展示，可能被截图、被采集

---

## 3. Progress

### 3.1 协议

调方在请求的 `_meta` 里塞 `progressToken`：

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

被调方发进度通知：

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

`progress` 单调递增、`total` 可选（不传就是 indeterminate）。

### 3.2 Python SDK

服务端用 `ctx.report_progress`：

```python
@mcp.tool()
async def scrape(url: str, ctx: Context) -> str:
    pages = await discover(url)
    for i, p in enumerate(pages):
        await fetch(p)
        await ctx.report_progress(
            progress=i + 1,
            total=len(pages),
            message=f"抓取 {p}",
        )
    return f"完成 {len(pages)} 页"
```

FastMCP 自动从请求的 `_meta.progressToken` 拿 token，没传就跳过通知。

客户端：

```python
async def on_progress(progress, total, message):
    print(f"进度 {progress}/{total}: {message}")

# 调工具时传 token + 回调
result = await session.call_tool(
    "scrape",
    {"url": "..."},
    progress_callback=on_progress,
)
```

### 3.3 设计建议

- 进度别太密集（每秒 < 10 条）
- 没有真实总数时不传 `total`（避免假百分比）
- 重置超时：spec 允许 Client 在收到进度通知时重置请求超时，但**Server 别滥用**——配合最大超时
- 失败时也发一条最终进度（progress=total）让 UI 收尾

---

## 4. Ping

### 4.1 协议

双向都能发：

```json
{"jsonrpc":"2.0","id":99,"method":"ping"}
{"jsonrpc":"2.0","id":99,"result":{}}
```

返回空对象就行。

### 4.2 Python SDK

SDK 内部自动响应 ping，你**不用**自己写 handler。

主动发 ping：

```python
# 客户端
pong = await session.send_ping()
print(f"Server 还活着，往返耗时...")
```

服务端在 lifespan 里也可以发 ping 探测客户端：

```python
import anyio

async def keepalive(server):
    while True:
        await anyio.sleep(30)
        try:
            await server.session.send_ping()
        except Exception:
            break
```

### 4.3 设计建议

- **HTTP 长 SSE 流**：每 30-60 秒 ping 一次防中间代理超时
- **stdio**：通常不需要，子进程死了管道会直接断
- **超时**：ping 应该几百毫秒回，超过 5 秒就当对方死了

---

## 5. Cancellation

### 5.1 协议

发起方想撤销已发过的请求：

```json
{
  "method": "notifications/cancelled",
  "params": {
    "requestId": 42,
    "reason": "user_cancelled"
  }
}
```

被调方收到后**应当**：

1. 立刻停止处理（如果还在跑）
2. 不要再发响应（响应会被对方忽略）
3. 释放资源

注意：是**通知**，没有响应。

### 5.2 Python SDK

服务端：长跑任务里检查取消：

```python
@mcp.tool()
async def long_task(n: int, ctx: Context) -> str:
    for i in range(n):
        # 显式检查取消（async cancellation）
        if ctx.request_context.cancelled:
            await ctx.warning("被取消，提前退出")
            return "cancelled"
        await ctx.report_progress(progress=i + 1, total=n)
        await asyncio.sleep(1)
    return "done"
```

> SDK 的 `ctx.request_context.cancelled` 是布尔标志。底层 SDK 也会在请求被取消时给协程发 `asyncio.CancelledError`，所以你只需要让代码"async 友好"——大量 await 点 + 没把 CancelledError 吞掉。

客户端：

```python
import asyncio

call_task = asyncio.create_task(session.call_tool("long_task", {"n": 100}))
await asyncio.sleep(3)
call_task.cancel()  # SDK 会自动发 notifications/cancelled
```

### 5.3 设计建议

- 写 Tool 时**永远不要 catch `CancelledError` 然后吞掉**
- 长跑要有 `await asyncio.sleep(0)` 或类似 yield 点
- 释放资源用 `try/finally`，别用 `except CancelledError`

---

## 6. 综合 demo：网页抓取 Server

```python
# demos/server/06_logging_progress.py
"""演示 logging / progress / cancellation 的综合 Server"""
import asyncio
import random
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("crawler-demo")


@mcp.tool()
async def crawl(url: str, max_pages: int = 20, ctx: Context = None) -> dict:
    """模拟爬取一个网站"""
    await ctx.info(f"开始爬取 {url}，最多 {max_pages} 页")

    pages_found = []
    try:
        for i in range(max_pages):
            # 模拟抓取
            await asyncio.sleep(0.2)

            page_url = f"{url}/page-{i+1}"
            pages_found.append(page_url)

            await ctx.report_progress(
                progress=i + 1,
                total=max_pages,
                message=f"抓取 {page_url}",
            )

            if random.random() < 0.05:  # 5% 概率模拟 warning
                await ctx.warning(f"页面 {page_url} 加载慢（5s+）")

    except asyncio.CancelledError:
        await ctx.warning(f"任务被取消，已抓 {len(pages_found)} 页")
        raise  # 必须重新 raise，让 SDK 知道任务取消了
    finally:
        await ctx.info(f"清理资源，已抓 {len(pages_found)} 页")

    return {"crawled": len(pages_found), "pages": pages_found[:5]}


if __name__ == "__main__":
    mcp.run()
```

Client 跑：

```python
# demos/client/06_progress_client.py
import asyncio
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


async def main():
    params = StdioServerParameters(
        command="python", args=["demos/server/06_logging_progress.py"]
    )

    async def on_log(params):
        print(f"[LOG/{params.level}] {params.data}")

    async def on_progress(progress, total, message):
        pct = int(progress / total * 100) if total else 0
        print(f"[PROGRESS] {pct}% — {message}")

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w, logging_callback=on_log) as session:
            await session.initialize()
            result = await session.call_tool(
                "crawl",
                {"url": "https://example.com", "max_pages": 5},
                progress_callback=on_progress,
            )
            print(f"\n最终结果: {result.content[0].text}")


asyncio.run(main())
```

---

## 7. 常见坑

| 坑 | 排查 |
|----|------|
| **`print(...)` 打日志** | 污染 stdio 通道；用 `ctx.info` 或 logging→stderr |
| **进度通知一秒上百条** | UI 卡顿；按真实节奏发，别每个循环都发 |
| **没声明 `logging` 能力** | FastMCP 调 `ctx.info` 时会自动声明 |
| **吞掉 CancelledError** | 取消语义被破坏；要 re-raise |
| **Server 长时间无响应没 ping** | HTTP SSE 流可能被中间代理超时干掉，要定期 ping |
| **logging level 没过滤** | Client 调 `logging/setLevel` 后 Server 端要自己过滤 |

---

## 8. 下一步

- 📖 Tasks 扩展（更彻底的异步） → [07-tasks.md](./07-tasks.md)
- 📖 错误处理 → [08-errors-validation.md](./08-errors-validation.md)
- 📖 客户端怎么收这些通知 → 03-client/01-client-basics

## 参考资料

- Logging：https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/logging
- Progress：https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/progress
- Ping：https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/ping
- Cancellation：https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/cancellation
