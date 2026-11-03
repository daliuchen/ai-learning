# MCP Production 05：调试与可观测（Inspector、日志与追踪）

> 写 MCP Server 时，了解它在做什么主要靠三样东西：MCP Inspector，可以直接看 JSON-RPC 流量，现在有 Web、CLI、TUI 三种形态；日志，stdio 下写 stderr，HTTP 下接你自己的日志系统（协议层 Logging 已弃用）；OpenTelemetry，Python SDK 2.x 的每个 Server 默认就在发 span，Client 和 Server 都用 SDK 时 trace 会自动串起来。这篇讲怎么把这三样配齐。

---

## 1. MCP Inspector：本地开发的首选

Inspector 是官方的调试工具。最新版（`@modelcontextprotocol/inspector`，需要 Node 22.19+）一个包里有三种客户端，共用同一个核心（传输、配置文件、OAuth 状态和协议代际协商都相同）：

| 形态 | 启动 | 用途 |
|------|------|------|
| Web（默认） | `npx @modelcontextprotocol/inspector python server.py` | 浏览器里的完整图形界面 |
| CLI | `npx @modelcontextprotocol/inspector --cli python server.py --method tools/list` | 适合脚本、CI 和编码 Agent：每次运行连接一次、发一个请求、打印结果、退出 |
| TUI | `npx @modelcontextprotocol/inspector --tui python server.py` | 没有浏览器时用的终端界面 |
| mcpdo（实验性） | 随包安装 | 连接一次并保持，可以多次发命令（适合跨请求的流程：订阅、elicitation、任务） |

### 1.1 启动

```bash
# 对 stdio Server：命令放在后面
npx @modelcontextprotocol/inspector python server.py

# 对远程 Streamable HTTP Server
npx @modelcontextprotocol/inspector --server-url https://api.example.com/mcp --transport http
```

Web 版启动后会打印一个带有本次启动 token 的 URL，并自动打开浏览器（0.x 版固定是 `http://localhost:6274`；设置 `MCP_AUTO_OPEN_ENABLED=false` 可以不自动打开）。Inspector 把 OAuth token、client secret 和 stdio 的 `env:` 值存在系统钥匙串里；没有钥匙串的环境会存到 `~/.mcp-inspector/secrets.json`，这个文件不加密，需要加密的话要提供 key。

> 装了 `mcp[cli]` 的话也可以用 `mcp dev server.py`，SDK 会替你启动 Inspector（它只认 `MCPServer`，低层 `Server` 需要自己运行）。

### 1.2 CLI：一行命令验证 Server（在 CI 里最有用）

```bash
mcp-inspector --cli python server.py --method tools/list
mcp-inspector --cli python server.py --method tools/call --tool-name add --tool-arg a=1 --tool-arg b=2
mcp-inspector --cli https://api.example.com/mcp --transport http --method tools/call --tool-name get_weather --tool-arg city=Boston --format json | jq .result
mcp-inspector --cli python server.py --method tools/list --strict      # 把 schema 可移植性检查变成 CI 门槛
```

`--method` 支持 `initialize`（只做探测，返回 serverInfo / protocolVersion / capabilities / instructions）、`tools/list`、`tools/call`、`resources/list`、`resources/read`、`resources/templates/list`、`prompts/list`、`prompts/get` 等；`logging/setLevel` 只在老的协议代际里有。

### 1.3 协议代际（protocol eras）

Inspector 和 SDK 的 `Client` 一样，先尝试 2026 版的 `server/discover`，失败了再回退到老的握手。不同面板在两代协议下的表现不一样（比如 2026 版没有 `ping`，订阅用的是 `subscriptions/listen`）。官方有一个专门的 "Protocol eras" 页面。

### 1.4 主要面板（Web）

| 面板 | 用途 |
|------|------|
| Connection | 配置 transport、命令、鉴权头或 OAuth |
| Tools | 列出工具，填参数，执行 |
| Resources | 列出直接 Resource 和模板 Resource，可以读取和订阅 |
| Prompts | 列出 prompt，填参数，查看 messages |
| Notifications / 监控侧边栏 | 实时显示通知和完整的 JSON-RPC 流量 |

### 1.5 排错思路

Server 对任何请求都没有响应：先看 Server 有没有返回 `server/discover`（或 `initialize`）的结果。没有返回就是 Server 进程出错了，去看 stderr，最常见的原因是 `print()` 污染了 stdio 的 stdout。

协商成功了但工具列表是空的：装饰器的位置不对（必须在模块顶层），或者 mcp 实例没有在 `if __name__ == "__main__"` 块之外创建。

工具调用卡住：Server 端有死循环或阻塞 IO，可以加一个 `ctx.report_progress` 看看进展到哪了。

收不到通知：2026 版里，通知只会发给打开了 `subscriptions/listen` 的 Client，而且服务端要用 `ctx.notify_*` 发送（见[用 Resources 提供上下文数据](../02-server/02-resources.md)）。

远程请求返回 `421 Invalid Host header`：没配 Host 白名单（见[用 Streamable HTTP 部署远程 MCP](01-remote-mcp.md)第 7 节）。Inspector 里只能看到一个笼统的传输错误，原因记录在服务端日志里。

---

## 2. Server 端日志

2026-07-28 弃用了协议层 Logging（`ctx.info()` / `notifications/message` / `logging/setLevel`），不要在它上面搭新的可观测体系。官方的建议是：

- stdio：写 stderr（宿主会自动捕获）。不要写 stdout，那会污染协议
- Streamable HTTP：stderr 不会被客户端捕获，要用你自己的日志聚合系统或 OpenTelemetry；排查请求时用 curl 或浏览器 DevTools 的 Network 面板查看 SSE 流

在 Python 里用标准库 `logging` 就行（`MCPServer` 构造时已经替你调用了 `basicConfig`，handler 指向 stderr，级别由 `log_level=` 控制；如果你自己先配置过，不会被覆盖）。生产环境建议用 JSON 格式：

```python
import json, logging, sys

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"ts": self.formatTime(record), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}, ensure_ascii=False)

handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(JsonFormatter())
logging.getLogger("obs").addHandler(handler)
```

（用 `structlog` 也一样，只要输出到 stderr。）日志收集到 ELK、Loki 或 CloudWatch 之后，就能按 `tool` 聚合、按用户过滤。

SDK 自己也会记日志：`ToolError` 记一行 INFO；工具里意料之外的异常记成带完整堆栈的 ERROR（模型只能看到 `Error executing tool xxx`）；参数校验失败记 INFO。所以不需要为了记日志到处写 `try/except`。

---

## 3. 追踪：OpenTelemetry（SDK 默认开启）

你的 Server 其实已经在被追踪了，不用加任何代码。每个 `MCPServer` 和低层 `Server` 都会为每条入站消息生成一个 `SERVER` span：

- span 名是方法加目标，比如 `tools/call search_books`、`tools/list`、`server/discover`
- 属性有 `mcp.method.name`、`mcp.protocol.version`（每个 span 都有）、`jsonrpc.request.id`（请求才有）
- 处理函数抛异常，或者工具结果是 `is_error=True` 时，span 状态记为 error
- `tools/call` 遵循 OTel 的 GenAI 语义约定：`gen_ai.operation.name="execute_tool"`、`gen_ai.tool.name`；`prompts/get` 带有 `gen_ai.prompt.name`。所以在追踪 UI 里，你的工具调用会和其他 Agent 的工具调用归在一起

没装 exporter 时几乎没有开销：SDK 只依赖轻量的 `opentelemetry-api`，没有 SDK 和 exporter 的情况下，创建 span 什么都不做。需要查看时，把另一半装上：

```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp
```

按常规方式配置好 exporter，Server 代码一行都不用改，SDK 一直在创建的那些 span 就能看到了。[Pydantic Logfire](https://logfire.pydantic.dev) 是可选的后端之一：`pip install logfire` 加 `logfire.configure()`。

跨进程串联（SEP-414）：Client 和 Server 都用 SDK 时，Client 会把 W3C trace context（`traceparent` / `tracestate` / `baggage`）放进请求的 `_meta`，Server 读取后，自己的 span 就挂在 Client 的 span 下面，属于同一条 trace，不需要你做任何事。如果收到的消息没带 trace context（来自不用 SDK 的 Client），Server 的 span 会挂在服务端当前的 span 下面，不会变成一条孤立的 trace。

下面的 demo 用 `ConsoleSpanExporter` 把 span 打印出来。Client 端自己建了一个 `my-app-request` span，把 `tools/call echo` 包在里面。输出里能看到 Server 的 `tools/call echo` 和 `tools/list` 两个 span 共用同一个 trace id，parent 都是 Client 的 span；没有外层 span 的 `tools/call fail` 是一条单独的 trace，状态为 `ERROR`。

如果要关掉追踪（基本用不着，没有 exporter 时它什么都不做），把 `OpenTelemetryMiddleware` 从中间件列表里去掉即可。它在 `mcp.server._otel` 里，下划线开头是有意为之：它被标为 provisional，导入路径以后可能会变。

---

## 4. 指标：用中间件

中间件（`MCPServer(middleware=[...])`）是一个包住每条入站消息的 `async (ctx, call_next)` 函数，适合做观测（计时、日志、指标）和拒绝请求（鉴权）。官方把它标为 provisional，2.x 的小版本里签名可能会变，别把整个 Server 都建立在它上面。几个要注意的地方：

- `ctx.method` 是原始的方法名字符串，`ctx.params` 是还没校验过的原始参数
- 中间件列表里最外层的先执行，`middleware[0]` 离网络最近
- `call_next` 返回的结果已经序列化成了线上格式的 dict（camelCase），所以判断工具错误要用 `result.get("isError")`，而不是 `result.is_error`

```python
from prometheus_client import Counter, Histogram
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext

CALLS = Counter("mcp_requests_total", "requests", ["method", "status"])
LATENCY = Histogram("mcp_request_seconds", "latency", ["method"], buckets=[0.01, 0.05, 0.1, 0.5, 1, 5, 30])

async def metrics(ctx: ServerRequestContext, call_next: CallNext) -> HandlerResult:
    with LATENCY.labels(ctx.method).time():
        try:
            result = await call_next(ctx)
            CALLS.labels(ctx.method, "tool_error" if isinstance(result, dict) and result.get("isError") else "ok").inc()
            return result
        except Exception:
            CALLS.labels(ctx.method, "exception").inc()
            raise

mcp = MCPServer("observable", middleware=[metrics])
```

指标接入 Prometheus 之后，可以写这样的告警规则：

```yaml
- alert: MCPHighErrorRate
  expr: |
    sum(rate(mcp_requests_total{status!="ok"}[5m])) / sum(rate(mcp_requests_total[5m])) > 0.05
  for: 10m
```

指标端口（`start_http_server(9090)`）只对内网开放，不要暴露到公网。

---

## 5. Client 端的可观测

Client 端值得关注的有：

- 每个 Server 的连接状态（已连接、断开、重连次数）
- 每次工具调用的延迟（在 `client.call_tool` 外面包一层计时，或者用 OTel）
- 通知数量：用 `message_handler` 兜底统计，以及 `client.listen(...)` 的事件数
- 多轮请求的轮数（`input_required_max_rounds`）：同一个调用反复要求输入，说明 Server 的设计有问题（比如每一轮的问题都不一样）
- Sampling 等已弃用的反向能力的使用频率

```python
async def on_any(message):
    if isinstance(message, Exception):
        log.error("transport error: %r", message)
        return
    notification_counter.labels(method=message.method, server=server_name).inc()

async with Client(url, message_handler=on_any) as client: ...
```

Client 和 Server 都用 SDK、也都配了 OTel exporter 时，一条 trace 就能从你的应用一直追到 Server 里的工具。

---

## 6. 排错用的开关

```bash
# 让 SDK 自己更啰嗦：MCPServer(..., log_level="DEBUG")；stdio 下日志都在 stderr
python server.py 2>server.err

# Inspector 的 CLI 一行验证
mcp-inspector --cli python server.py --method tools/list
```

SDK 2.x 不再读取 `MCP_*` 环境变量或 `.env` 文件（v1 里 `FASTMCP_*`、`MCP_LOG_LEVEL` 之类的设置都没有了），所有配置都通过构造参数和 `run()` 传入。

---

## 7. 常见排错场景对照

| 现象 | 看哪里 | 通常的原因 |
|------|------|--------|
| Server 启动后立刻退出 | stderr | 装饰器报错，或端口被占用 |
| Inspector 显示 0 tools | 在监控侧边栏看 `server/discover` 的结果 | print 污染了输出，或装饰器写错 |
| 跨 Server 工具重名 | Host 日志 | 让 Server 自己加前缀 |
| 长任务没有进度 | Inspector 监控 | 调用时没带 `progressToken`（客户端没传 `progress_callback`） |
| OAuth 401 | Server 日志和 `WWW-Authenticate` | audience、issuer 或 scope 不对 |
| 所有请求都是 421 / 403 | 服务端日志里的一行 warning | 没配置 Host / Origin 白名单 |
| 多副本下多轮请求随机失败 | 服务端日志里的 `requestState rejected` | 没有共享 `RequestStateSecurity` 的 key，或者实例名不同 |
| SSE 流断开 | nginx / Cloudflare 日志 | 没关 buffering；2026 版不能续传，要重发 |
| 性能慢 | OTel span 或延迟指标 | 有阻塞 IO；同步函数跑在线程里，要注意线程池 |

---

## 8. 一个完整可观测的 Server 骨架

```python
# demos/production/05_observable_server.py
import asyncio
import json
import logging
import sys
import time
from collections import Counter, defaultdict

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from mcp import Client
from mcp.server import MCPServer
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver.exceptions import ToolError

# === 1) 日志：结构化 JSON 写 stderr（不污染 stdio 的 stdout）===
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"ts": self.formatTime(record), "level": record.levelname, "logger": record.name, "msg": record.getMessage()}, ensure_ascii=False)


handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(JsonFormatter())
logging.getLogger("obs").addHandler(handler)
logging.getLogger("obs").setLevel(logging.INFO)
log = logging.getLogger("obs")

# === 2) 指标：用中间件包住每个请求（这里用内存计数器代替 prometheus_client，接口一样）===
CALLS: Counter[tuple[str, str]] = Counter()
LATENCY: dict[str, list[float]] = defaultdict(list)


async def metrics(ctx: ServerRequestContext, call_next: CallNext) -> HandlerResult:
    t0 = time.perf_counter()
    status = "ok"
    try:
        result = await call_next(ctx)
        # 中间件看到的结果已经是序列化后的 dict（线上格式，camelCase）；工具返回 is_error=True 也算错误
        if isinstance(result, dict) and result.get("isError"):
            status = "tool_error"
        return result
    except Exception:
        status = "exception"
        raise
    finally:
        CALLS[(ctx.method, status)] += 1
        LATENCY[ctx.method].append(time.perf_counter() - t0)


mcp = MCPServer("observable", middleware=[metrics])  # 中间件接口官方标注为 provisional


@mcp.tool()
def echo(msg: str) -> str:
    """回声测试"""
    log.info("tool.call echo msg=%s", msg)
    return msg


@mcp.tool()
def fail() -> str:
    """总是失败的工具"""
    raise ToolError("expected failure")


async def main() -> None:
    # === 3) 追踪：SDK 每条消息一个 SERVER span；装上 OTel SDK + exporter 就「亮起来」，Server 代码一行不改 ===
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(out=sys.stdout, formatter=lambda s: _fmt(s) + "\n")))
    trace.set_tracer_provider(provider)

    async with Client(mcp) as client:
        with trace.get_tracer("client-app").start_as_current_span("my-app-request"):  # Client 侧的 span：与 Server span 同一条 trace
            await client.call_tool("echo", {"msg": "hi"})
        await client.call_tool("fail", {})
    print("\n== 指标 ==")
    for (method, status), n in sorted(CALLS.items()):
        print(f"  {method:12} {status:11} x{n}")


def _fmt(span) -> str:  # 只打印关心的字段，输出更短
    a = dict(span.attributes or {})
    keep = {k: a[k] for k in ("mcp.method.name", "mcp.protocol.version", "gen_ai.tool.name", "gen_ai.operation.name") if k in a}
    parent = format(span.parent.span_id, "x")[:6] if span.parent else "-"
    return f"span {span.name!r:22} kind={span.kind.name:8} trace={format(span.context.trace_id, 'x')[:8]} parent={parent:7} status={span.status.status_code.name:5} {keep}"


if __name__ == "__main__":
    asyncio.run(main())
```

实际输出（节选，trace id 每次都不同）：

```
span 'server/discover'      kind=SERVER   trace=a3444e19 parent=-       status=UNSET {'mcp.method.name': 'server/discover', 'mcp.protocol.version': '2026-07-28'}
span 'tools/call echo'      kind=SERVER   trace=b526f88a parent=3842cb  status=UNSET {... 'gen_ai.tool.name': 'echo', 'gen_ai.operation.name': 'execute_tool'}
span 'tools/list'           kind=SERVER   trace=b526f88a parent=3842cb  status=UNSET {...}
span 'my-app-request'       kind=INTERNAL trace=b526f88a parent=-       status=UNSET {}
span 'tools/call fail'      kind=SERVER   trace=ce891d4c parent=-       status=ERROR {...}

== 指标 ==
  server/discover ok          x1
  tools/call   ok          x1
  tools/call   tool_error  x1
  tools/list   ok          x1
```

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| Inspector 起不来 | 新版需要 Node 22.19+；也可以用 `npm i -g` 全局安装 |
| 日志写到了 stdout | stdio 模式下会污染协议，要写 stderr（标准库 `logging` 默认就是） |
| 靠 `ctx.info()` 记线上日志 | 已弃用，改用标准库 `logging` 加 OTel |
| 中间件里 `result.is_error` 永远不成立 | `call_next` 返回的是序列化后的 dict，要用 `result.get("isError")` |
| 指标端点暴露在公网 | 9090 端口只对内网开放，或者加上鉴权 |
| Logfire / OTLP 的 token 提交进了 git | 一律用环境变量 |
| 担心 OTel 的性能开销 | 没有 exporter 时什么都不做；有 exporter 时，对高频工具把采样率调低（比如 1%） |
| 在 Client 里等 Server 的 `notifications/message` | 2026 版里要给 `Client` 传 `log_level=` 才收得到，而且协议层日志已经弃用 |

---

## 10. 下一步

生产部分的 5 篇到这里结束。下一章从[用 MCP Apps 在客户端中嵌入交互式 UI](../06-advanced/01-mcp-apps.md)开始，讲 MCP Apps、Agent Skills 和 Registry。

## 参考资料

- Inspector：https://modelcontextprotocol.io/docs/2026-07-28/tools/inspector （Web / CLI / TUI / mcpdo / protocol-eras 子页）
- Debugging 指南：https://modelcontextprotocol.io/docs/2026-07-28/tools/debugging
- Python SDK OpenTelemetry：https://py.sdk.modelcontextprotocol.io/run/opentelemetry/
- Python SDK Middleware：https://py.sdk.modelcontextprotocol.io/advanced/middleware/
- Logfire：https://logfire.pydantic.dev
- OpenTelemetry：https://opentelemetry.io/docs/
