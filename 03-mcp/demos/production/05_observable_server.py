"""带日志 + 指标（中间件）+ 追踪（SDK 默认的 OpenTelemetry span）的 Server 骨架（mcp 2.x）

跑法（进程内自测：Client 与 Server 在同一进程，span 用 ConsoleSpanExporter 打印出来，能看到 Client→Server 的 trace 串联）:
    pip install opentelemetry-sdk            # SDK 本身只依赖 opentelemetry-api，没装 SDK 时 span 是空操作
    python demos/production/05_observable_server.py
"""
from __future__ import annotations

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
