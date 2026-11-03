# MCP Production 01：用 Streamable HTTP 部署远程 MCP

> 用 `mcp.streamable_http_app()` 拿到一个普通的 Starlette ASGI 应用，交给 uvicorn（可以多 worker）运行，前面用 nginx、Caddy 或 Cloudflare 处理 TLS 和路由，再配好 Host 白名单，就是一个能上生产的远程 MCP Server。2026-07-28 的协议没有会话，多副本负载均衡不再需要粘性会话（只有还没升级的 2025 版老客户端才需要）。部署时真正要处理的是两件事：`requestState` 的密钥和订阅总线。

---

## 1. 远程 MCP 与本地 stdio 的区别

| 维度 | 本地 stdio | 远程 Streamable HTTP |
|------|-----------|----------------------|
| 进程 | Host 的子进程 | 独立服务 |
| 并发 | 1 个 Client | N 个 Client 共享 |
| 状态 | 进程内存 | 2026 协议下请求自包含；跨请求的状态靠显式句柄或外部存储 |
| 鉴权 | 进程级 | OAuth 2.1 + RFC 9728 |
| 部署 | 用户自己更新 | 后端发版 |

写代码的工作量差不多，难的是 Host 白名单、鉴权、多副本下的密钥和通知，以及可观测性。

---

## 2. 最简单的 Streamable HTTP Server

```python
# server_http.py
from mcp.server import MCPServer

mcp = MCPServer("remote-demo")          # 构造函数只描述「它是什么」

@mcp.tool()
def echo(msg: str) -> str:
    return msg

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8765)   # 传输选项都传给 run()，不是 MCPServer(...)
```

> 从 v1 迁移：`host`、`port`、`stateless_http`、`json_response`、端点路径等参数，都从 `MCPServer(...)` 的构造函数挪到了 `run()` / `streamable_http_app()`。写 `MCPServer("x", port=9000)` 会得到 `TypeError: MCPServer.__init__() got an unexpected keyword argument 'port'`。`mount_path` 被删除了，要挂在子路径下，用 ASGI 的 `Mount`。另外，`MCP_*` 环境变量和 `.env` 不会再被自动读取。

`run()` 在 streamable-http 模式下的选项有：`host`（默认 127.0.0.1）、`port`（默认 8000）、`streamable_http_path`（默认 `/mcp`）、`json_response`、`stateless_http`、`max_request_body_size`（默认 4 MiB，超过直接返回 413）、`session_idle_timeout`（老会话空闲 1800 秒后关闭）、`max_sessions`（单进程最多 10000 个老会话）、`transport_security` 等。

Client 连接：

```python
from mcp import Client

async with Client("http://127.0.0.1:8765/mcp") as client:      # URL = Streamable HTTP
    ...
```

---

## 3. 嵌入 Starlette / FastAPI

`streamable_http_app()` 返回的是一个 Starlette 应用，任何能托管 ASGI 的东西（uvicorn、Hypercorn、另一个 Starlette 应用、FastAPI）都能托管它。要和自己的路由放在一起，就把它放进 `Mount`：

```python
from contextlib import asynccontextmanager
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from mcp.server import MCPServer

mcp = MCPServer("integrated")

@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():     # ⚠️ 被挂载的子应用的 lifespan 不会运行，最外层必须自己进入
        yield

app = Starlette(
    routes=[Route("/healthz", healthz), Mount("/", app=mcp.streamable_http_app())],   # 自己的路由放 Mount 前面
    lifespan=lifespan,
)
```

- `Mount("/", ...)` 会匹配所有路径，所以你自己的路由必须写在它前面，写在后面就永远匹配不到
- 外层 `lifespan` 里那行 `async with mcp.session_manager.run()` 最容易漏掉。漏了的话应用能启动，路由也能解析，但第一个请求打到 `/mcp` 时才报 `RuntimeError: Task group is not initialized. Make sure to use run().`
- `mcp.session_manager` 要在调用过 `streamable_http_app()` 之后才存在
- 一个应用里挂多个 Server：每个 Server 用自己的 `Mount("/notes", ...)`，各自的 `session_manager.run()` 用 `AsyncExitStack` 一起进入；端点地址是挂载前缀加 `/mcp`。想让前缀本身就是完整路径，传 `streamable_http_path="/"`
- FastAPI 也一样：`app = FastAPI(lifespan=lifespan); app.mount("/mcp", mcp.streamable_http_app(streamable_http_path="/"))`
- 浏览器客户端需要 CORS，在最外层包上 Starlette 的 `CORSMiddleware`

---

## 4. 会话、有状态与无状态（2026 之后完全变了）

| 客户端的协议版本 | 会话 | 负载均衡要做什么 |
|------|------|-----------|
| 2026-07-28 | 没有。`Mcp-Session-Id` 从不出现，每个请求自包含 | 什么都不用做，任何 worker 都能处理任何请求 |
| 2025-11-25 及更早（默认） | `Mcp-Session-Id`，存在某个 worker 的内存里 | 需要粘性会话；请求被转到别的 worker 会得到 `404 Session not found` |
| 2025-11-25 及更早 + `stateless_http=True` | 无 | 不需要粘性；代价是失去 Server → Client 的反向通道（sampling、`ctx.elicit()`）和断点续传 |

需要理解的几点：

- 2026 路径天生就是无状态的，不需要你打开什么开关。`stateless_http=True` 只影响老客户端那一路，2026 的流量根本不会走到那里
- 所以 Server 同时服务两代客户端、而老客户端还占多数时，负载均衡仍然要为它们做粘性（或者打开 `stateless_http=True`，放弃反向通道）。等客户端都升级了，粘性就可以去掉
- 需要跨调用保持状态时，不要依赖会话，而是用 Server 生成的显式句柄作为工具参数（见[用 Tasks 扩展异步执行长请求](../02-server/07-tasks.md)），状态放在外部存储里
- lifespan 现在只在服务启动时运行一次（v1 是每个会话一次），状态由所有会话共享

---

## 5. 多节点部署要处理的两件事

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4      # 或多副本 + LB
```

### 5.1 `requestState` 的密钥（用了多轮请求或 `Resolve` 就一定会遇到）

只要工具用了 `Resolve(...)`（elicitation、sampling、roots），或者手写了 `InputRequiredResult`，就是多轮请求：第一轮返回问题和一个 `requestState` 令牌，客户端回答后用一个全新的 HTTP 请求重试。`MCPServer` 默认用进程启动时随机生成的密钥加密这个令牌，4 个 worker 就有 4 把互不认识的密钥。重试请求落到另一个 worker 上，就会得到这个固定的错误：

```json
{"code": -32602, "message": "Invalid or expired requestState", "data": {"reason": "invalid_request_state"}}
```

如果出现「单 worker 一切正常，开 2 个 worker 就时不时失败」，几乎可以肯定是这个原因。真实原因（`unknown key` / `audience`）只记录在服务端日志里。修复要做两件事，缺一不可：

```python
from mcp.server.mcpserver import MCPServer, RequestStateSecurity

mcp = MCPServer("billing", request_state_security=RequestStateSecurity(keys=[os.environ["MCP_STATE_KEY"]]))
```

1. 所有实例共享同一把 key（至少 32 字节，可以用 `python -c "import secrets; print(secrets.token_hex(32))"` 生成）。`keys[0]` 用来签发，列表里所有的 key 都能用来验证，这样就能不停机轮换
2. 所有实例使用同一个名字。每个令牌都把 Server 的 `name` 作为 audience 声明，并且严格校验。`MCPServer(f"billing-{POD}")` 看起来方便观测，却会让每一次跨实例重试都失败。如果一定要每个实例单独命名，就给整个集群显式指定 `RequestStateSecurity(keys=[...], audience="billing")`

重启、部署、客户端从另一个进程恢复流程，都相当于换了一个 worker。

### 5.2 订阅通知（`subscriptions/listen`）

一条 listen 流就是一个长连接响应，固定在某个副本上。在另一个副本上调用 `ctx.notify_resource_updated(...)`，通知到不了这条流，因为默认的 `SubscriptionBus` 在进程内存里。这种失败是静默的：调用成功了，流上却什么都没有。三种办法选一个：

- 给所有副本实现同一个共享的 `SubscriptionBus`（只有 `publish` / `subscribe` 两个方法，接到 Redis 或 NATS 的 pub/sub 上），传入 `MCPServer(subscriptions=bus)`
- 不需要订阅的话，用 `MCPServer(subscriptions=False)` 关掉，别向客户端承诺它们收不到的事件
- 只部署单副本

### 5.3 老客户端的粘性会话（最简单的做法）
让负载均衡按 `Mcp-Session-Id` 头路由：

```nginx
upstream mcp_backend {
    hash $http_mcp_session_id consistent;
    server mcp-1:8000;
    server mcp-2:8000;
}
```

没有会话头的请求（2026 路径，以及老客户端的第一个 `initialize`）会被一致性哈希到某个节点上，这没有问题。

---

## 6. SSE 响应流与超时配置

需要流式输出（进度通知、长时间运行的工具）时，Streamable HTTP 的响应会升级成 SSE。反向代理的关键配置：

```nginx
location /mcp {
    proxy_pass http://app:8000;
    proxy_http_version 1.1;
    proxy_buffering off;        # SSE 不能缓冲
    proxy_set_header Connection "";
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

- Cloudflare 和 CDN：关掉 minify 和缓冲；有些 CDN 默认会切断长连接，要留意
- uvicorn：每个长连接只占一个连接，不占一个 worker（异步模型），所以不用为了连接数堆 worker，worker 数按 CPU 核数配置即可
- 2026 删除了 SSE 断点续传，流被中间设备切断时这次请求就丢了，客户端必须用新的请求 ID 重发，所以写操作要配幂等键（见[错误处理与参数校验](../02-server/08-errors-validation.md)）

---

## 7. 安全：Host 白名单（部署后第一个会遇到的问题）

`streamable_http_app()` 不知道自己将来会部署在哪个域名后面，所以默认开启 DNS rebinding 防护，只接受 `Host` 为 `127.0.0.1:<port>` / `localhost:<port>` / `[::1]:<port>` 的请求。本机开发时正合适，可一旦部署到真实域名，所有请求都会被拒绝：

```
421 Misdirected Request     Invalid Host header
403 Forbidden               Invalid Origin header
```

这是一个纯文本的 HTTP 响应，不是 JSON-RPC 错误，MCP 客户端只会报一个笼统的传输错误，具体原因只在服务端日志里有一行 warning。所以新部署的 Server 拒绝所有连接时，先怀疑 Host 白名单。修复方法：

```python
from mcp.server.transport_security import TransportSecuritySettings

security = TransportSecuritySettings(
    allowed_hosts=["mcp.example.com", "mcp.example.com:*"],      # 精确字符串；裸主机头和任意端口两种都要列
    allowed_origins=["https://app.example.com"],                  # 只有浏览器才会带 Origin
)
app = mcp.streamable_http_app(transport_security=security)
```

- 如果反向代理已经控制了 `Host` 头，直接关掉这项检查才是如实的配置：`TransportSecuritySettings(enable_dns_rebinding_protection=False)`
- 传一个非本地的 `host="mcp.example.com"` 并不会把它加进白名单，它只会让保护默认不开启，结果是所有 Host 都被接受。想把规则写清楚，就用 `transport_security=`
- 如果 TLS 在代理处终止，要给 uvicorn 加上 `--proxy-headers --forwarded-allow-ips='<代理地址>'`。否则应用以为自己跑在 `http://` 下，重定向（比如 `/mcp` → `/mcp/`）会指向 `http://…`，而 Python 客户端会拒绝从 HTTPS 降级到 HTTP：`MCPError: Redirect to http://… not followed`

公网部署还必须有这些：

- HTTPS：由 nginx、Caddy 或 Cloudflare 终止 TLS
- 鉴权：OAuth 2.1（见下一篇 [远程 MCP 的 OAuth 2.1 鉴权](02-auth-oauth.md)）
- 限流：nginx 的 `limit_req` 或中间件。2026 起请求带有 `Mcp-Method` / `Mcp-Name` 头，网关不用解析 body 就能按方法或工具限流
- 日志：所有 tool call 都记审计日志

---

## 8. 健康检查与监控

`ping` 在 2026 版已经删除，健康检查用普通的 HTTP 端点（放在 `Mount("/")` 前面）：

```python
async def healthz(request):   # k8s liveness
    return JSONResponse({"status": "ok"})

async def readyz(request):    # k8s readiness：检查 DB / 缓存
    ready = await db_connected()
    return JSONResponse({"status": "ready" if ready else "db down"}, status_code=200 if ready else 503)
```

监控方面，SDK 默认带 OpenTelemetry：每条消息一个 server span，没装 exporter 时几乎没有开销（见[调试与可观测（Inspector、日志与追踪）](05-debugging-inspector.md)）。要导出 Prometheus 指标，用 `MCPServer(middleware=[...])` 写一个中间件包住每次请求，按 `ctx.method` 和工具名打点：

```python
from prometheus_client import Counter, Histogram
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext

TOOL_CALLS = Counter("mcp_requests_total", "requests", ["method", "status"])
LATENCY = Histogram("mcp_request_seconds", "latency", ["method"])

async def metrics(ctx: ServerRequestContext, call_next: CallNext) -> HandlerResult:
    with LATENCY.labels(ctx.method).time():
        try:
            result = await call_next(ctx)       # 返回的是序列化后的 dict（线上格式）
            TOOL_CALLS.labels(ctx.method, "tool_error" if isinstance(result, dict) and result.get("isError") else "ok").inc()
            return result
        except Exception:
            TOOL_CALLS.labels(ctx.method, "error").inc()
            raise

mcp = MCPServer("remote-demo", middleware=[metrics])      # 官方标注该中间件接口为 provisional
```

---

## 9. Dockerfile 模板

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt     # 里面要有 mcp>=2.3、uvicorn

COPY . .
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips=*"]
```

（只有在除了代理以外没人能直接访问 uvicorn 时，`--forwarded-allow-ips=*` 才是安全的。）

`docker-compose.yml`：

```yaml
services:
  mcp:
    build: .
    ports: ["8000:8000"]
    environment:
      - MCP_STATE_KEY=${MCP_STATE_KEY}        # 所有副本共享
      - MCP_SERVER_NAME=billing               # 所有副本同名
      - ALLOWED_HOSTS=mcp.example.com
```

---

## 10. K8s 部署要点

- Replicas：从 3 个起步，用 HPA 按 QPS 或 CPU 扩容
- Secret：`MCP_STATE_KEY` 用 K8s Secret 下发给所有副本；Pod 名不要放进 `MCPServer(name)`
- Service：只有 2026 客户端时不需要 `sessionAffinity`；还有 2025 老客户端的话再开 `ClientIP`（比较粗糙的做法）
- Ingress：nginx ingress 加上前面的 SSE 配置
- PreStop hook：优雅退出时，让进行中的流先收尾
- 探针：liveness 用 `/healthz`，readiness 用 `/readyz`

```yaml
apiVersion: apps/v1
kind: Deployment
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: mcp
          image: my-mcp:latest
          env:
            - name: MCP_STATE_KEY
              valueFrom: {secretKeyRef: {name: mcp-secrets, key: state-key}}
          lifecycle:
            preStop:
              exec:
                command: ["/bin/sh", "-c", "sleep 30"]
          readinessProbe:
            httpGet: {path: /readyz, port: 8000}
          livenessProbe:
            httpGet: {path: /healthz, port: 8000}
```

---

## 11. 完整 demo

Server（Starlette 挂载、健康检查、Host 白名单、可选的多实例密钥）：

```python
# demos/production/01_remote_server.py
import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp.server import MCPServer
from mcp.server.mcpserver import Context, RequestStateSecurity
from mcp.server.transport_security import TransportSecuritySettings

# ① 多实例：所有实例同一把 key + 同一个 name；单实例（比如这个 demo 默认）可以不配
state_key = os.environ.get("MCP_STATE_KEY")
mcp = MCPServer(
    os.environ.get("MCP_SERVER_NAME", "remote-demo"),
    request_state_security=RequestStateSecurity(keys=[state_key]) if state_key else None,
)


@mcp.tool()
async def slow_compute(n: int, ctx: Context) -> int:
    """长任务：边算边上报进度"""
    total = 0
    for i in range(n):
        total += i
        if i % 100 == 0:
            await ctx.report_progress(i, total=n)
        await asyncio.sleep(0.001)
    return total


# ② Host / Origin 白名单：不配的话，部署到真实域名后「每个请求都 421」
hosts = [h for h in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h]
security = TransportSecuritySettings(
    allowed_hosts=[h2 for h in hosts for h2 in (h, f"{h}:*")],
    allowed_origins=[],  # 只有浏览器才会带 Origin
)


# ③ 健康检查：和 MCP 端点放在同一个 Starlette 里（k8s liveness / readiness 用）
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def readyz(request: Request) -> JSONResponse:
    ready = True  # 这里检查 DB / 缓存 是否就绪
    return JSONResponse({"status": "ready" if ready else "not ready"}, status_code=200 if ready else 503)


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    # 被 Mount 的子应用的 lifespan 不会运行：最外层应用必须自己进入 session_manager.run()，否则首个请求就
    # RuntimeError: Task group is not initialized
    async with mcp.session_manager.run():
        yield


mcp_app = mcp.streamable_http_app(transport_security=security)
app = Starlette(
    routes=[
        Route("/healthz", healthz),  # 自己的路由必须放在 Mount("/") 之前，它会吞掉所有路径
        Route("/readyz", readyz),
        Mount("/", app=mcp_app),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    import uvicorn

    # 多 worker：uvicorn 模块名:app --workers 4（2026 协议无需粘性会话；2025 老客户端仍需要 sticky）
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8780")), log_level="warning")
```

Client（两代协议、进度回调、故意触发 421）：

```python
# demos/production/01_remote_client.py
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
```

运行：

```bash
PORT=8780 python demos/production/01_remote_server.py     # 终端 1
PORT=8780 python demos/production/01_remote_client.py     # 终端 2
```

实际输出：

```
   [progress] 0/1000
   [progress] 500/1000
[auto  ] 协议 2026-07-28 -> {'result': 499500}
   [progress] 0/1000
   [progress] 500/1000
[legacy] 协议 2025-11-25 -> {'result': 499500}
healthz: {'status': 'ok'}
Host 不在白名单: 421 Invalid Host header
```

同一个端点，`auto` 走 2026 路径（没有会话），`legacy` 走 2025 路径（服务端日志里能看到 `Created new transport with session ID`）；伪造 Host 头得到的是纯文本的 `421`，请求根本没有到达 MCP 层。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| 部署到域名后所有请求都返回 `421 Invalid Host header` | 配置 `transport_security=TransportSecuritySettings(allowed_hosts=[...])`；原因只在日志里 |
| `RuntimeError: Task group is not initialized` | 外层应用的 lifespan 没有进入 `mcp.session_manager.run()`（被 Mount 的子应用的 lifespan 不会运行） |
| 自己的路由 404 | 写在了 `Mount("/")` 后面，挪到前面去 |
| 开 2 个 worker 后，多轮请求时不时报 `Invalid or expired requestState` | 共享 `RequestStateSecurity(keys=[...])`，并且所有实例用同一个名字（或者显式指定 `audience`） |
| 订阅通知偶尔收不到 | 默认总线在进程内；多副本要用共享的 `SubscriptionBus`，或者设 `subscriptions=False` |
| `MCPServer("x", port=9000)` 报 TypeError | v2 里传输相关的选项要传给 `run()` / `streamable_http_app()` |
| 负载均衡后面报 `Redirect to http://… not followed` | 给 uvicorn 加 `--proxy-headers --forwarded-allow-ips` |
| SSE 流被代理切断 | nginx 关闭 `proxy_buffering` 并调长超时；Cloudflare 关闭 minify |
| K8s 滚动发布时 Client 断流 | 配 preStop hook 和 readiness gate；写操作配幂等键 |
| 本地 HTTP 服务暴露到了公网 | 绑定 127.0.0.1，远程访问必须用 OAuth |
| 老客户端报 404 "Session not found" | 2025 路径需要粘性会话，或者设 `stateless_http=True` |
| 请求体超过 4 MiB 被返回 413 | 确实需要传大消息时再调大 `max_request_body_size` |

---

## 13. 下一步

- OAuth 2.1 鉴权：[远程 MCP 的 OAuth 2.1 鉴权](./02-auth-oauth.md)
- 企业鉴权：[企业鉴权（Client Credentials、企业管理授权与网关）](./03-enterprise-auth.md)
- 安全防御：[防范 Prompt 注入、Tool 投毒与路径越权](./04-security.md)
- 可观测与调试：[调试与可观测（Inspector、日志与追踪）](./05-debugging-inspector.md)

## 参考资料

- Transports spec：https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- Python SDK Deploy & scale：https://py.sdk.modelcontextprotocol.io/run/deploy/
- Add to an existing app：https://py.sdk.modelcontextprotocol.io/run/asgi/
- Serving legacy clients：https://py.sdk.modelcontextprotocol.io/run/legacy-clients/
- Connect Remote Servers：https://modelcontextprotocol.io/docs/2026-07-28/develop/connect-remote-servers
