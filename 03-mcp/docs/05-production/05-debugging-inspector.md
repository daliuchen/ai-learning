# MCP Production 05：调试与可观测 —— Inspector、日志、追踪

> **一句话**：写 MCP Server 时的"眼睛"主要是三个：**MCP Inspector**（最直接，看 raw JSON-RPC）、**结构化日志**（生产环境）、**OpenTelemetry / Logfire**（端到端追踪）。本篇讲怎么把它们配齐。

---

## 1. MCP Inspector：本地开发首选

Inspector 是官方提供的可视化调试器。前面章节多次提到，这里系统讲一遍。

### 1.1 启动

```bash
# 对 stdio Server
npx @modelcontextprotocol/inspector python server.py

# 对远程 Streamable HTTP Server
npx @modelcontextprotocol/inspector
# 在 UI 里选 Streamable HTTP 并填 URL
```

打开 http://localhost:6274。

### 1.2 主要面板

| 面板 | 用途 |
|------|------|
| **Connection** | 配 transport + 命令 + 鉴权头 |
| **Tools** | 列出工具、填参数、执行 |
| **Resources** | 列直接 + 模板 Resource，可读取、订阅 |
| **Prompts** | 列 prompt、填参数、看 messages |
| **Notifications** | 实时显示 Server 发的通知 |
| **Console** | 完整 JSON-RPC 流量，含发出的请求和收到的响应 |

### 1.3 排错套路

**Server 没响应任何请求**：
- Console 看 Server 端是否回了 initialize result
- 没回 → Server 进程错误（看本地日志、stderr）
- 90% 是 `print()` 污染了 stdio stdout

**握手成功但工具列表是空的**：
- 装饰器位置错（要在 module 顶层）
- `if __name__ == "__main__"` 块外没创建 mcp 实例

**工具调用卡死**：
- Server 端死循环 / 阻塞 IO
- 装个 progress 通知试试

**通知不到达**：
- Server 没声明对应 capability
- 例如 `logging` 没声明就发 `notifications/message` 会被丢

### 1.4 高级用法

- **代理重放**：Inspector 自带 proxy，可以把所有请求录下来重放
- **schema 校验**：Inspector 会按 Tool 的 inputSchema 校验你填的参数
- **Token 注入**：远程 Server 调试时在 UI 配 Authorization 头

---

## 2. Server 端结构化日志

生产环境用 `structlog` 写结构化日志到 stderr 或文件：

```python
import structlog

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger()


@mcp.tool()
async def search_orders(user_id: str, ctx: Context) -> list:
    log.info("tool.call", tool="search_orders", user_id=user_id)
    try:
        result = await db.fetch(...)
        log.info("tool.success", tool="search_orders", count=len(result))
        return result
    except Exception as e:
        log.exception("tool.error", tool="search_orders", error=str(e))
        raise
```

输出到 stderr（不污染 stdio 通道）：

```json
{"timestamp":"2026-05-20T14:21:00Z","level":"info","event":"tool.call","tool":"search_orders","user_id":"u_001"}
```

收集到 ELK / Loki / Cloudwatch 后能按 `tool` 聚合、按 `user_id` 过滤。

---

## 3. 用 Logfire 一键观测（推荐）

[Logfire](https://logfire.pydantic.dev) 是 Pydantic 团队出的可观测平台，对 LLM / MCP 友好。

### 3.1 接入

```python
import logfire
import asyncio
from mcp.server.fastmcp import FastMCP

logfire.configure(
    project_name="my-mcp",
    token=os.environ["LOGFIRE_TOKEN"],
)

mcp = FastMCP("my-mcp")

# 用 Logfire 自动 instrument 整个 FastMCP
# （SDK 版本不同方法名可能略变）

@mcp.tool()
async def search(q: str) -> str:
    with logfire.span("search.execute", query=q) as span:
        result = await do_search(q)
        span.set_attribute("hit_count", len(result))
        return result
```

Logfire UI 里能看到：
- 每次 tool call 的 latency
- 调用链（用户 → MCP Tool → 内部 DB 查询）
- 错误 + 堆栈
- 资源使用

### 3.2 让 Client 也可见

Pydantic AI Agent 配 Logfire 后，跨 Agent → MCP Server 的调用都串联起来：

```python
import logfire
logfire.instrument_pydantic_ai()

agent = Agent("openai:gpt-4o", toolsets=[MCPServerStdio("python", args=["server.py"])])
# 现在所有 agent 调用 + MCP tool 执行都在 Logfire 一张 trace 上
```

---

## 4. OpenTelemetry 集成

如果你已经用 OTel：

```python
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

trace.set_tracer_provider(TracerProvider())
trace.get_tracer_provider().add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="otel-collector:4317"))
)

tracer = trace.get_tracer("mcp-server")


@mcp.tool()
async def my_tool(x: int, ctx: Context) -> int:
    with tracer.start_as_current_span("my_tool") as span:
        span.set_attribute("input.x", x)
        result = await compute(x)
        span.set_attribute("output.result", result)
        return result
```

> spec 2025-11-25 在 `_meta` 字段约定 OTel 传播头（SEP-414），让 Client → Server 跨进程 trace 自动 propagate。SDK 实现进度看 release notes。

---

## 5. Prometheus 指标

最关键的几个指标：

```python
from prometheus_client import Counter, Histogram, start_http_server

TOOL_CALLS = Counter(
    "mcp_tool_calls_total",
    "Total tool calls",
    ["tool", "status"],
)
TOOL_LATENCY = Histogram(
    "mcp_tool_latency_seconds",
    "Tool execution latency",
    ["tool"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 30],
)
ACTIVE_SESSIONS = Counter("mcp_active_sessions", "Active client sessions")

# 在 lifespan 启动 metrics 端口
start_http_server(9090)
```

抓到 Prometheus 后写 alert：

```yaml
- alert: MCPHighErrorRate
  expr: |
    rate(mcp_tool_calls_total{status="error"}[5m])
    / rate(mcp_tool_calls_total[5m]) > 0.05
  for: 10m
```

---

## 6. Client 端可观测

Client 端要关注：

- 每个 Server 的连接状态（连上/断开/重连次数）
- 每个工具调用的 latency
- 收到的通知数（log / progress / list_changed）
- 反向请求频率（Server 调 sampling 多了说明 Server 设计问题）

```python
async def on_log(params):
    log.info("server.log", level=params.level, data=params.data, server=server_name)

async def on_message(msg):
    method = getattr(msg, "method", None)
    notification_counter.labels(method=method, server=server_name).inc()
```

---

## 7. Debug Mode 环境变量

SDK 支持几个环境变量打开内部日志：

```bash
# Python SDK debug 日志（具体名称看 SDK release）
PYTHONUNBUFFERED=1 MCP_LOG_LEVEL=debug python server.py

# 抓 stderr 看 trace
python server.py 2>server.err
```

Inspector 启动时加 `-d`：

```bash
DEBUG=mcp:* npx @modelcontextprotocol/inspector python server.py
```

---

## 8. 常见排错场景对照

| 现象 | 看哪 | 通常是 |
|------|------|--------|
| Server 启动后立刻退出 | stderr | 装饰器报错 / port 占用 |
| Inspector 0 tools | Console 看 initialize 响应 | print 污染 / 装饰器写错 |
| 跨 server 工具命名冲突 | Host 日志 | Server 自己加 prefix |
| 长任务无进度 | Inspector Console | progressToken 没传 |
| OAuth 401 | Server 端 middleware 日志 | audience / issuer 错 |
| SSE 流断开 | nginx / cloudflare 日志 | buffering 没关 |
| 性能慢 | Prometheus latency | 阻塞 IO / 没用 async DB |

---

## 9. 一个完整可观测的 Server 骨架

```python
# demos/production/05_observable_server.py
"""带 logging + metrics + tracing 的 Server 骨架"""
import os
import time
from contextlib import asynccontextmanager

import structlog
from prometheus_client import Counter, Histogram, start_http_server

from mcp.server.fastmcp import Context, FastMCP

# === 日志 ===
structlog.configure(processors=[
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.add_log_level,
    structlog.processors.JSONRenderer(),
])
log = structlog.get_logger()

# === 指标 ===
TOOL_CALLS = Counter("mcp_tool_calls_total", "Total tool calls", ["tool", "status"])
TOOL_LATENCY = Histogram("mcp_tool_latency_seconds", "Latency", ["tool"])


@asynccontextmanager
async def app_lifespan(server):
    start_http_server(int(os.getenv("METRICS_PORT", 9090)))
    log.info("server.start")
    try:
        yield {}
    finally:
        log.info("server.stop")


mcp = FastMCP("observable", lifespan=app_lifespan)


@mcp.tool()
async def echo(msg: str, ctx: Context) -> str:
    """回声测试"""
    start = time.time()
    status = "ok"
    try:
        log.info("tool.call", tool="echo", msg=msg)
        return msg
    except Exception as e:
        status = "error"
        log.exception("tool.error", tool="echo")
        raise
    finally:
        TOOL_CALLS.labels(tool="echo", status=status).inc()
        TOOL_LATENCY.labels(tool="echo").observe(time.time() - start)


if __name__ == "__main__":
    mcp.run()
```

跑：

```bash
METRICS_PORT=9090 python demos/production/05_observable_server.py
# 另一终端：
curl http://localhost:9090/metrics | grep mcp_
```

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| **Inspector 没启动** | 检查 npx 网络 / 用 `npm i -g` 装全局 |
| **logging 写到 stdout** | stdio 模式下污染协议；务必用 stderr 或 ctx.info |
| **指标 endpoint 暴露公网** | 9090 端口要内网或加鉴权 |
| **Logfire token 进 git** | 永远用环境变量 |
| **OTel 性能开销** | 高频工具采样率调低（如 1%） |

---

## 11. 下一步

05-production 全部 5 篇结束。下一章 06-advanced：MCP Apps、Agent Skills、Registry。

## 参考资料

- Inspector：https://github.com/modelcontextprotocol/inspector
- Debugging 指南：https://modelcontextprotocol.io/docs/tools/debugging
- Logfire：https://logfire.pydantic.dev
- OpenTelemetry：https://opentelemetry.io/docs/
- SEP-414 OTel propagation：https://modelcontextprotocol.io/seps/414-request-meta
