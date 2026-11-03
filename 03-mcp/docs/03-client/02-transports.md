# MCP Client 02：传输方式 stdio、Streamable HTTP 与进程内

> MCP 的标准传输只有两种：本地子进程用的 stdio，远程用的 Streamable HTTP。Python SDK 另外提供了一种进程内连接，用于测试和嵌入。Streamable HTTP 取代了 2024-11-05 的「HTTP+SSE 双端点」，到 2026-07-28 又去掉了会话、GET 流和断点续传。在 `mcp.Client` 里不需要单独配置传输，第一个参数的类型决定了用哪种。

---

## 1. stdio：本地子进程

### 1.1 帧格式

按行分隔的 JSON，用换行符分隔消息，消息体里不能有换行：

```
{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {...}}\n
{"jsonrpc": "2.0", "id": 1, "result": {...}}\n
```

没有 `Content-Length` 头，那是 LSP 的做法，MCP 从来没这么用过。帧由 SDK 自动处理，不用自己拼。

### 1.2 Python Client 端

```python
from mcp import Client, StdioServerParameters

params = StdioServerParameters(
    command="python",
    args=["server.py"],
    env={"BOOKSHOP_API_KEY": "secret"},  # 在子进程的最小环境之上追加
    cwd="/path/to/server/workdir",       # 工作目录
)

async with Client(params) as client:
    ...
```

> 子进程不会继承你的环境变量。它只拿到一个最小白名单（POSIX 上是 `HOME`、`LOGNAME`、`PATH`、`SHELL`、`TERM`、`USER`），免得敏感信息流进你没审查过的进程。Server 需要 API key 的话，用 `env=` 显式传入。

### 1.3 stderr 的处理

Server 的 stderr 默认原样接到你的 stderr 上，开发时比较方便。要重定向到文件，就自己构建传输：

```python
from mcp import Client, stdio_client

with open("server.log", "w") as log_file:
    async with Client(stdio_client(params, errlog=log_file)) as client:
        ...
```

### 1.4 关闭与异常清理

退出 `async with` 时，SDK 会关闭 Server 的 stdin，等它自己退出；超时后发 SIGTERM，再超时发 SIGKILL。v2 在这里有一处行为变化：在 POSIX 上，正常退出的 Server 留下的子孙进程不会再被一起杀掉。子进程崩溃时，读写会抛出错误。

### 1.5 适用场景

本地工具（filesystem、git、本地数据库），个人桌面集成（比如让 Claude Code 连本地 Python 脚本），以及离线开发和测试。

---

## 2. Streamable HTTP：远程

### 2.1 端点设计（2026-07-28）

只有一个 URL（如 `https://mcp.example.com/mcp`），Client 把每条 JSON-RPC 消息都 POST 上去：

| 方法 | 用途 |
|------|------|
| POST | 发送请求。响应是 `application/json`，或者升级成这个 POST 自己的 `text/event-stream` 响应流 |
| ~~GET~~ | 2025 版用它打开 Server → Client 的通知流；2026-07-28 起移除，改为 POST `subscriptions/listen`（它的响应流就是订阅流） |

### 2.2 一次 POST 的实际样子

请求（2026-07-28）：

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: add

{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"add","arguments":{"a":1,"b":2},
 "_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}
```

- `Accept` 必须同时包含 `application/json` 和 `text/event-stream`
- 每个请求都带 `MCP-Protocol-Version`
- 新增了 `Mcp-Method`（所有请求都带）和 `Mcp-Name`（`tools/call`、`resources/read`、`prompts/get` 带），网关和限流器不用解析 body 就能路由。Tool 的输入 schema 里还可以用 `x-mcp-header` 把某个参数映射成 `Mcp-Param-*` 头，Server 会做交叉校验（SEP-2243），两者不一致时返回 `-32020 HeaderMismatch`
- 没有 `Mcp-Session-Id`，任何一个副本都能处理这个请求

响应有两种。第一种是普通 JSON 响应，用于短请求，下面是上面那条请求的实际响应：

```http
HTTP/1.1 200 OK
content-type: application/json

{"jsonrpc":"2.0","id":1,"result":{"content":[{"text":"3","type":"text"}],"isError":false,"resultType":"complete","structuredContent":{"result":3},"_meta":{"io.modelcontextprotocol/serverInfo":{...}}}}
```

第二种是 SSE 流响应，用于长请求，比如需要发进度通知、多条消息的情况：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream

event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{...}}

event: message
data: {"jsonrpc":"2.0","id":1,"result":{...}}
```

用 JSON 还是 SSE，由 Server 自己决定。

### 2.3 会话：2026 版没有了，2025 版还在

| | Legacy（2025-11-25 及更早） | Modern（2026-07-28） |
|---|---|---|
| 会话 | `initialize` 响应头里返回 `Mcp-Session-Id`，之后每个请求都必须带上，不带会得到 `400 Missing session ID` | 没有。需要跨调用保持的状态，用 Server 生成的显式句柄作为工具参数传递（见[用 Tasks 扩展异步执行长请求](../02-server/07-tasks.md)） |
| Server → Client 通知 | 独立的 GET SSE 流 | `subscriptions/listen` |
| 断线续传 | `Last-Event-ID` | 已移除，流断了请求就丢了，必须用新的请求 ID 重发 |

Python SDK 的 `MCPServer` 能同时应答两代客户端。`Client(url)` 默认 `mode="auto"`，自动选择；`mode="legacy"` 强制走老路径，需要 Server 发反向请求时用。

### 2.4 Python Client 端

```python
from mcp import Client

async with Client("https://mcp.example.com/mcp") as client:   # URL = Streamable HTTP
    ...
```

需要鉴权头、代理、mTLS 或自定义超时时，自己创建一个 `httpx2.AsyncClient`（v2 的 HTTP 客户端从 `httpx` 换成了 `httpx2`），交给 `streamable_http_client`：

```python
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

async with httpx2.AsyncClient(
    headers={"Authorization": "Bearer eyJ..."},
    timeout=httpx2.Timeout(30.0, read=300.0),      # 别丢：httpx2 默认 5 秒，长工具会读超时
) as http_client:
    async with Client(streamable_http_client("https://mcp.example.com/mcp", http_client=http_client)) as client:
        ...
```

几点注意：

- `streamable_http_client` 不再接受 `headers=` / `timeout=`（v1 的 `streamablehttp_client(url, headers=...)` 已移除），参数只有 `url`、`http_client`、`terminate_on_close`、`max_sse_event_size`
- 你创建的 `httpx2.AsyncClient` 由你负责进入和退出，SDK 不会关闭它
- 单个 SSE 事件默认上限是 1 MiB，结果更大时传 `max_sse_event_size=`
- 只跟随同源重定向（带尾斜杠的 307/308、同主机的 http→https），其它重定向会失败，请直接配置最终 URL
- `httpx2` 使用操作系统的证书库（truststore），不是 certifi。精简容器里没有系统 CA 时，设置 `SSL_CERT_FILE`/`SSL_CERT_DIR`，或者传 `verify=ssl_context`
- 非 2xx 响应现在只会让对应的那个请求得到 JSON-RPC 错误，不会让整条连接断掉

---

## 3. 断线与重连

2025 版有可恢复的流：`Last-Event-ID` 配合 Server 端缓存的事件。2026-07-28 把这些全删了，SSE 事件不再带 ID，Server 也不再重发。

好处是 Server 不用保存已发送的事件，可以做到彻底无状态，任何副本都能提供服务。代价是流一断，这次请求就丢了：读操作直接重发即可，写操作要靠幂等键防止重复执行（见[错误处理与参数校验](../02-server/08-errors-validation.md) 第 10 节）。`subscriptions/listen` 的流断开后，要重新 listen 并重新拉取最新状态，订阅流本来就不是重放日志。

---

## 4. 安全：Origin、Localhost 与 DNS Rebinding

本地 HTTP MCP Server 有一个不太容易察觉的安全风险：恶意网页可以通过 DNS rebinding 攻击跑在 localhost 上的 MCP Server。

防御办法：

1. Server 端校验 `Origin` 头（规范要求：存在且不合法时返回 403）；SDK 内置的 `transport_security` 提供 Host/Origin 白名单
2. 本地部署时只绑定 127.0.0.1，不要绑 0.0.0.0
3. 即使是本地，也要求 Authorization

> 详见[用 Streamable HTTP 部署远程 MCP](../05-production/01-remote-mcp.md) 和 [防范 Prompt 注入、Tool 投毒与路径越权](../05-production/04-security.md)。

---

## 5. 旧的 HTTP+SSE 传输（已弃用）

2024-11-05 规范用的是两个端点：POST `/messages` 加 GET `/sse`。它从 2025-03-26 起被 Streamable HTTP 取代，2026-07-28 正式标为 Deprecated（SEP-2596）。Python SDK 仍然保留了 `sse_client`，用来连接还没升级的老 Server：

```python
from mcp import Client
from mcp.client.sse import sse_client

async with Client(sse_client("https://old-server.example.com/sse")) as client:
    ...
```

新项目不要再用它。Server 端的 `mcp.run("sse", ...)` 也还在，只是为了服务还没迁移的客户端。WebSocket 传输在 v2 里被整个删掉了，它从来就不是 MCP 规范的一部分，`mcp[ws]` 这个 extra 也一起取消了。

---

## 6. 自定义传输

任何能写成 `async with x as (read, write)` 的异步上下文管理器都可以当传输，对应 `mcp.client` 里的 `Transport` 协议。`Client` 按类型解析第一个参数：`str` 走 `streamable_http_client(url)`，`StdioServerParameters` 走 `stdio_client(params)`，Server 对象走进程内连接，其它对象都当作传输直接使用。因此 `stdio_client(...)`、`streamable_http_client(...)`、`sse_client(...)` 以及你自己写的传输，都可以直接传给 `Client`。

绝大多数情况下不需要自定义，stdio 加 Streamable HTTP 就够了。

---

## 7. 三种传输对比

| 维度 | stdio | Streamable HTTP | 进程内 |
|------|-------|-----------------|--------|
| 部署 | Host 启动子进程 | 独立服务 | 同一进程 |
| 客户端数量 | 1（本进程） | 多个（多用户共享） | 1 |
| 延迟 | 极低 | 网络 RTT | 无 |
| 鉴权 | 进程权限 | HTTP 鉴权（推荐 OAuth 2.1） | 不涉及 |
| 升级 | 用户更新本地 | 后端发版即生效 | 不涉及 |
| 离线 | 可以 | 不行 | 可以 |
| 适用场景 | 本地工具、文件 IO | SaaS 集成、企业内部数据 | 单元测试、把 Server 嵌入应用 |

---

## 8. 综合 demo：stdio、HTTP、自定义 Header、legacy 与进程内

```python
# demos/client/02_transport_demo.py
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
```

实际输出：

```
[stdio ] hello-mcp  protocol=2026-07-28  tools=2
[http  ] echo-http  protocol=2026-07-28
[http+h] who_am_i -> Bearer dev-token
[legacy] protocol=2025-11-25
[memory] echo-http  protocol=2026-07-28
```

同一个 Server，同一份 `streamable_http_app()`，`auto` 模式协商到 2026-07-28，`legacy` 模式协商到 2025-11-25，这就是 Server 同时服务两代客户端的效果。

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| stdio Server 没有响应 | Server 的 stdout 被 print 污染了 |
| stdio 子进程读不到环境变量 | 子进程只继承最小白名单，用 `StdioServerParameters(env=...)` 显式传 |
| `streamablehttp_client(url, headers=...)` 报错 | v2 里改名为 `streamable_http_client`，也没有 `headers=` 了；请求头设在自己创建的 `httpx2.AsyncClient` 上 |
| 自建的 `httpx2.AsyncClient` 没设 `timeout` | 默认 5 秒，慢工具会读超时；用 `httpx2.Timeout(30.0, read=300.0)` |
| HTTP 请求没带 `Accept: application/json, text/event-stream` | Server 会拒绝 |
| 2026 版请求缺少 `MCP-Protocol-Version` / `Mcp-Method` 头 | 远程 Server 可能返回 400 或 `-32020`；SDK 会自动带上，手写 curl 时要自己加 |
| 以为可以用 `Last-Event-ID` 续传 | 2026-07-28 起移除；流断了就重发（写操作要配幂等键） |
| 本地 HTTP Server 绑在 0.0.0.0 | 有安全风险，改成 127.0.0.1 |
| 用 `import httpx` 去捕获 SDK 的网络异常 | SDK 现在用的是 `httpx2`，异常类型是 `httpx2.ConnectError` 等 |
| 还在用 WebSocket 传输 | v2 已移除 |

---

## 10. 下一步

- Sampling（已弃用）及其替代方案：[Sampling 让 Server 借用 Host 的 LLM（已弃用）](./03-sampling.md)
- Roots、Elicitation 与多轮请求：[Elicitation、Roots 与多轮请求（MRTR）](./04-roots-elicitation.md)
- 远程部署（Server 端的 Streamable HTTP）：[用 Streamable HTTP 部署远程 MCP](../05-production/01-remote-mcp.md)
- OAuth 2.1 鉴权：[远程 MCP 的 OAuth 2.1 鉴权](../05-production/02-auth-oauth.md)

## 参考资料

- Transports spec：https://modelcontextprotocol.io/specification/2026-07-28/basic/transports
- Python SDK Client transports：https://py.sdk.modelcontextprotocol.io/client/transports/
- Running your server：https://py.sdk.modelcontextprotocol.io/run/
- SSE 标准：https://html.spec.whatwg.org/multipage/server-sent-events.html
