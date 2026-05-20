# MCP Client 02：传输层 —— stdio、Streamable HTTP、Resumable Stream

> **一句话**：MCP 标准定义两种传输——**stdio**（本地子进程）和 **Streamable HTTP**（远程）。Streamable HTTP 取代了 2024-11-05 老规范的"HTTP+SSE 双端点"设计，用**单端点 + 可选 SSE**支持双向通信、resumable、鉴权。本篇讲清两种传输的细节和 Python Client 端的具体用法。

---

## 1. stdio：本地子进程

### 1.1 帧格式

stdio 传输的帧格式（spec 2025-11-25 改简化版）：

```
{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}\n
{"jsonrpc": "2.0", "id": 1, "result": {...}}\n
```

每行一条 JSON-RPC 消息，**换行符分隔**，消息体内**不能**含换行。

> 早期版本（2024-11-05）用 LSP 风格的 `Content-Length` header 框架，2025 后简化为换行分隔。SDK 自动处理两种格式。

### 1.2 Python Client 端

```python
from mcp.client.stdio import stdio_client, StdioServerParameters

params = StdioServerParameters(
    command="python",
    args=["server.py"],
    env={"DEBUG": "1"},                  # 子进程的环境变量
    cwd="/path/to/server/workdir",       # 工作目录
    encoding="utf-8",
    encoding_error_handler="strict",
)

async with stdio_client(params) as (read, write):
    ...
```

### 1.3 stderr 怎么处理

Server 可以在 stderr 写日志。Client 选项：

```python
# 默认：捕获到 sys.stderr（开发时方便）
async with stdio_client(params) as (r, w):
    ...

# 自定义重定向到文件
from anyio.streams.text import TextSendStream
# 高级用法见 SDK 文档
```

### 1.4 关闭 / 异常清理

`async with` 退出时：

1. SDK 把 Server stdin 关掉
2. 等 Server 自己 exit
3. 5 秒超时 → SIGTERM
4. 再超时 → SIGKILL

子进程崩了 read/write 会抛 `EndOfStream`。

### 1.5 适合场景

- 本地工具（filesystem / git / 本地数据库）
- 个人桌面集成（Claude Code 接到本地 Python 脚本）
- 离线开发与测试

---

## 2. Streamable HTTP：远程

### 2.1 端点设计

**一个 URL**（如 `https://mcp.example.com/mcp`），支持两个 HTTP 方法：

| 方法 | 用途 |
|------|------|
| **POST** | Client → Server 发请求 / 响应 / 通知 |
| **GET** | Client 建立 SSE 长连接接收 Server → Client 消息 |

### 2.2 POST 行为

请求：

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2025-11-25
Mcp-Session-Id: 1868a90c...   ← initialize 后必带

{"jsonrpc":"2.0","id":1,"method":"tools/list"}
```

响应有两种：

**普通 JSON 响应**（适合短请求）：

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}
```

**SSE 流响应**（适合长请求 / 流式输出 / Server 反向请求）：

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream

id: 1
event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{...}}

id: 2
event: message
data: {"jsonrpc":"2.0","id":1,"result":{...}}
```

每个 SSE 事件有：
- `id`: 用于 resumable（断线重连）
- `data`: 一条 JSON-RPC 消息

Server 自己决定用 JSON 还是 SSE。

### 2.3 GET 行为

```http
GET /mcp HTTP/1.1
Accept: text/event-stream
Mcp-Session-Id: 1868a90c...
```

返回 SSE 流，Server 可以主动 push：

- `notifications/*`
- 反向请求：`sampling/createMessage` / `elicitation/create` / `roots/list`

GET 建立的流上 **Server 不能发响应**（响应只在对应的 POST 流上）。

### 2.4 会话与 Session ID

`MCP-Session-Id` HTTP header 由 Server 在 initialize 响应里给：

```http
HTTP/1.1 200 OK
Mcp-Session-Id: 1868a90c-f1a3-...
Content-Type: application/json

{"jsonrpc":"2.0","id":1,"result":{...}}
```

之后 Client 所有请求都要带这个 header。Server 端用 session id 关联状态。

**没收到 session id**：Server 选择了**无状态模式**（stateless），不需要带。

### 2.5 协议版本头

Initialize 后所有 HTTP 请求都要带：

```
MCP-Protocol-Version: 2025-11-25
```

不带的话 Server 会按 `2025-03-26` 处理（向后兼容默认值）。

### 2.6 Python Client 端

```python
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

async with streamablehttp_client("https://mcp.example.com/mcp") as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        ...
```

带鉴权头：

```python
async with streamablehttp_client(
    "https://mcp.example.com/mcp",
    headers={"Authorization": "Bearer eyJ..."},
) as (r, w, _):
    ...
```

---

## 3. Resumable Stream（断线重连）

长跑 SSE 流如果中断了，怎么不丢消息？

```http
GET /mcp HTTP/1.1
Accept: text/event-stream
Last-Event-ID: 42
Mcp-Session-Id: 1868a90c...
```

Server 收到 `Last-Event-ID: 42` 后**重发 id > 42 的事件**。

实现要点：

- Server 必须把发出去的事件**保留**一段时间（按 TTL）
- Client 记住最后收到的 event id
- 网络抖动时自动重连

SDK 端 `streamablehttp_client` 自动处理这一切。

---

## 4. 安全：Origin / Localhost / DNS Rebinding

本地 HTTP MCP Server 有个微妙的安全风险：恶意网页用 DNS rebinding 攻击 localhost 上跑的 MCP Server。

防御：

1. **Server 端**：检查 `Origin` 头，不在白名单的返回 403
2. **Server 端**：本地部署时 **只绑 127.0.0.1**，不要 0.0.0.0
3. **Server 端**：要求 Authorization（哪怕本地）

> 详见 05-production/04-security。

---

## 5. 老 HTTP+SSE 传输（向后兼容）

2024-11-05 规范用的是"两个端点"：

- POST `/messages` — Client 发消息
- GET `/sse` — Server 发消息（带初始 `endpoint` 事件指出 messages 端点）

**已废弃**，但很多 Server 还在用。Python SDK 的 `sse_client` 仍然支持：

```python
from mcp.client.sse import sse_client

async with sse_client("https://old-server.example.com/sse") as (r, w):
    async with ClientSession(r, w) as session:
        await session.initialize()
        ...
```

Server 端如果要兼容老 Client，需要同时跑两套端点。

---

## 6. 自定义传输

MCP 规范允许自定义传输（WebSocket、Unix Socket、gRPC ……）。SDK 只要求 `read`/`write` 是 `anyio.abc.ObjectReceiveStream` / `ObjectSendStream`，所以你可以自己实现：

```python
import anyio

async def my_custom_transport():
    send_stream, recv_stream = anyio.create_memory_object_stream(max_buffer_size=100)
    other_send, other_recv = anyio.create_memory_object_stream(max_buffer_size=100)

    # ... 自己接管消息收发 ...

    return recv_stream, send_stream  # 给 ClientSession 用
```

但 99% 场景不必自定义，stdio + Streamable HTTP 足够。

---

## 7. 传输对比

| 维度 | stdio | Streamable HTTP |
|------|-------|-----------------|
| 部署 | Host 启动子进程 | 独立服务 |
| 客户端数量 | 1（本进程） | 多（多用户共享） |
| 延迟 | 极低 | 网络 RTT |
| 鉴权 | 进程权限 | HTTP 鉴权（OAuth 2.1 推荐） |
| 升级 | 用户改本地 | 后端发版即生效 |
| 离线 | ✅ | ❌ |
| 跨进程状态 | ❌ | ✅ |
| 适合场景 | 本地工具、文件 IO | SaaS 集成、企业内部数据 |

---

## 8. 综合 demo：连本地 + 连远程

```python
# demos/client/02_transport_demo.py
"""同一份 Client 代码同时演示 stdio + Streamable HTTP"""
import asyncio
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client


async def use_local():
    params = StdioServerParameters(
        command="python",
        args=[str(Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py")],
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            print(f"[本地] {init.serverInfo.name}")
            tools = await session.list_tools()
            print(f"  → {len(tools.tools)} 个工具")


async def use_remote(url: str, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with streamablehttp_client(url, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            init = await session.initialize()
            print(f"[远程] {init.serverInfo.name}")
            tools = await session.list_tools()
            print(f"  → {len(tools.tools)} 个工具")


async def main():
    await use_local()
    # 如果有自己跑的远程 Server，把 URL 改掉再开
    # await use_remote("http://localhost:8765/mcp", token="dev-token")


asyncio.run(main())
```

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| **stdio Server 没回响应** | Server stdout 被 print 污染了 |
| **HTTP 请求没带 `Accept: text/event-stream`** | Server 没法用 SSE，体验降级 |
| **session id 没带** | initialize 后所有请求都得带 |
| **Last-Event-ID 假设 Server 一定 replay** | spec 是 MAY，部分 Server 不实现，要做"重连后重新订阅"兜底 |
| **本地 HTTP Server 绑 0.0.0.0** | 安全风险，改成 127.0.0.1 |
| **没设 `MCP-Protocol-Version`** | 远程 Server 可能按老版本处理协议导致小差异 |

---

## 10. 下一步

- 📖 Sampling 反向请求 → [03-sampling.md](./03-sampling.md)
- 📖 Roots / Elicitation → [04-roots-elicitation.md](./04-roots-elicitation.md)
- 📖 远程部署（Server 端 Streamable HTTP）→ 05-production/01-remote-mcp
- 📖 OAuth 2.1 鉴权 → 05-production/02-auth-oauth

## 参考资料

- Transports spec：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- SSE 标准：https://html.spec.whatwg.org/multipage/server-sent-events.html
- Python SDK client modules：https://github.com/modelcontextprotocol/python-sdk/tree/main/src/mcp/client
