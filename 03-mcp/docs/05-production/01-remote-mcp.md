# MCP Production 01：远程 MCP —— Streamable HTTP 部署

> **一句话**：把 FastMCP Server 用 `mcp.run(transport="streamable-http")` 起在 uvicorn / starlette 后面，加上 nginx / cloudflare 做 TLS 和路由，你就有了一个生产级远程 MCP Server。本篇讲完整的部署链路、关键配置、横向扩展。

---

## 1. 远程 MCP vs 本地 stdio：本质差别

| 维度 | 本地 stdio | 远程 Streamable HTTP |
|------|-----------|----------------------|
| 进程 | Host 子进程 | 独立服务 |
| 并发 | 1 个 Client | N 个 Client 共享 |
| 状态 | 进程内存 | 必须考虑跨节点 |
| 鉴权 | 进程级 | OAuth 2.1 + RFC 9728 |
| 部署 | 用户更新 | 后端发版 |

写代码的工作量本质一样，**难点在状态、鉴权、可观测**。

---

## 2. 最简 Streamable HTTP Server

```python
# server_http.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "remote-demo",
    host="127.0.0.1",
    port=8765,
)

@mcp.tool()
def echo(msg: str) -> str:
    return msg

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

启动：

```bash
python server_http.py
# 监听 http://127.0.0.1:8765/mcp
```

Client 连：

```python
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

async with streamablehttp_client("http://127.0.0.1:8765/mcp") as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        ...
```

---

## 3. 嵌入到 Starlette / FastAPI

FastMCP 暴露的端点本质是 Starlette `Mount`。你可以把它挂到 FastAPI 应用里和其他路由一起跑：

```python
# app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("integrated", stateless_http=True)


@mcp.tool()
def hello(name: str) -> str:
    return f"Hello {name}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(lifespan=lifespan)

# 挂载 MCP 端点到 /mcp
app.mount("/mcp", mcp.streamable_http_app())


@app.get("/healthz")
def healthz():
    return {"ok": True}
```

启动：

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

现在：
- `POST /mcp` 是 MCP 端点
- `GET /healthz` 是健康检查
- 业务路由可以和 MCP 共存

---

## 4. 有状态 vs 无状态

FastMCP 默认**有状态**——每个 Client 用 `Mcp-Session-Id` 维持会话。这意味着：

- Session 数据存在 Server 内存
- 多节点部署需要 sticky session 或共享 store

**stateless 模式**（`stateless_http=True`）：

```python
mcp = FastMCP("stateless", stateless_http=True)
```

无状态下：

- 每次请求像独立 RPC
- 跨节点无需 sticky
- ⚠️ Server 端不能依赖 session 内的 lifespan 状态做"按用户"区分

适合无状态：纯查询类、纯无副作用工具。

适合有状态：长任务 + 进度通知、订阅 Resource、Sampling 流。

---

## 5. 多节点部署

### 5.1 Sticky Session（最简）
负载均衡按 `Mcp-Session-Id` cookie / header 路由：

```nginx
upstream mcp_backend {
    hash $http_mcp_session_id consistent;
    server mcp-1:8000;
    server mcp-2:8000;
    server mcp-3:8000;
}

server {
    location /mcp {
        proxy_pass http://mcp_backend;
        proxy_buffering off;        # SSE 不能缓冲
        proxy_read_timeout 3600s;   # 长连接
    }
}
```

### 5.2 共享 Session Store
Session 状态存 Redis：

```python
# 伪代码——FastMCP 当前没有内置 Redis session store，
# 你可以用 starlette middleware 自己实现
```

适合大规模、跨地域。

### 5.3 全无状态 + 外部消息总线
Sampling / Subscription 走 Redis Pub/Sub 或 NATS。复杂度高，只在超大规模时考虑。

---

## 6. SSE 与超时配置

Streamable HTTP 大量场景会用 SSE。关键配置：

### 6.1 nginx

```nginx
location /mcp {
    proxy_pass http://app:8000;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_set_header Connection "";
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

### 6.2 Cloudflare / CDN
- 关掉 minify / proxy buffering
- 启用 Server-Sent Events 兼容（有些 CDN 默认会切断长连接）

### 6.3 uvicorn / Hypercorn
默认就支持 SSE，但 worker 数量要够（每个长连接占一个 worker）。建议 worker = 4-8 起，配合 `--lifespan on`。

---

## 7. 安全：本地 vs 公网

### 7.1 本地 HTTP MCP（很多场景没意识到的雷）
本地 HTTP Server 要警惕 **DNS rebinding** 攻击：

```python
mcp = FastMCP(
    "local",
    host="127.0.0.1",   # 千万别 0.0.0.0
)
```

Server 端检查 `Origin` 头（FastMCP 已内置）。

### 7.2 公网部署必备
- HTTPS：用 nginx / Caddy / Cloudflare 终结 TLS
- 鉴权：OAuth 2.1（见下一篇 02-auth-oauth）
- 限流：nginx rate_limit 或 FastAPI middleware
- 日志：所有 tool call 记审计日志

---

## 8. 健康检查与监控

```python
from fastapi import FastAPI

app = FastAPI(lifespan=lifespan)
app.mount("/mcp", mcp.streamable_http_app())


@app.get("/healthz")
async def healthz():
    """k8s liveness"""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """k8s readiness：检查 DB / 缓存 是否就绪"""
    if not await db_connected():
        return {"status": "db down"}, 503
    return {"status": "ready"}
```

监控指标建议导出 Prometheus：

```python
from prometheus_client import Counter, Histogram

TOOL_CALLS = Counter("mcp_tool_calls_total", "tool calls", ["tool", "status"])
TOOL_LATENCY = Histogram("mcp_tool_latency_seconds", "tool latency", ["tool"])

# 在 FastMCP middleware / lifespan 里包装 call_tool 注入这两个指标
```

---

## 9. Dockerfile 模板

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

`docker-compose.yml`：

```yaml
services:
  mcp:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgres://...
      - OAUTH_ISSUER=https://auth.example.com
    depends_on:
      - redis
      - postgres
```

---

## 10. K8s 部署要点

- **Replicas**：起步 3 个，配 HPA 按 QPS 或连接数扩
- **Service**：用 `sessionAffinity: ClientIP` 做 sticky（粗糙版）
- **Ingress**：用 nginx ingress + 上面 SSE 配置
- **PreStop hook**：让 pod 优雅退出时关闭已有 SSE 流

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

```python
# demos/production/01_remote_server.py
"""远程 MCP Server，挂在 FastAPI 上"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from mcp.server.fastmcp import Context, FastMCP


mcp = FastMCP("remote-demo", stateless_http=False)


@mcp.tool()
async def slow_compute(n: int, ctx: Context) -> int:
    """长任务"""
    import asyncio
    total = 0
    for i in range(n):
        total += i
        if i % 1000 == 0:
            await ctx.report_progress(progress=i, total=n)
        await asyncio.sleep(0.001)
    return total


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="MCP Remote Demo", lifespan=lifespan)
app.mount("/mcp", mcp.streamable_http_app())


@app.get("/healthz")
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", 8765)))
```

跑：

```bash
python demos/production/01_remote_server.py
```

Client 连：

```python
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

async with streamablehttp_client("http://127.0.0.1:8765/mcp") as (r, w, _):
    async with ClientSession(r, w) as session:
        await session.initialize()
        result = await session.call_tool("slow_compute", {"n": 50000})
        print(result.content[0].text)
```

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **SSE 流被代理切断** | nginx 关 proxy_buffering、Cloudflare 关 minify |
| **K8s rollout 时 Client 断流** | preStop hook + readiness gate |
| **多节点 session 丢失** | sticky session 或换 stateless_http=True |
| **本地 HTTP 暴露公网** | 改 127.0.0.1，远程要用 OAuth |
| **uvicorn worker 太少** | 长连接占满 worker，要 4-8 起 |

---

## 13. 下一步

- 📖 OAuth 2.1 鉴权 → [02-auth-oauth.md](./02-auth-oauth.md)
- 📖 企业鉴权 → [03-enterprise-auth.md](./03-enterprise-auth.md)
- 📖 安全防御 → [04-security.md](./04-security.md)
- 📖 可观测 / 调试 → [05-debugging-inspector.md](./05-debugging-inspector.md)

## 参考资料

- Transports spec：https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- Connect Remote Servers：https://modelcontextprotocol.io/docs/develop/connect-remote-servers
- FastMCP HTTP：https://github.com/modelcontextprotocol/python-sdk
