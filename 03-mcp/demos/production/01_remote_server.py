"""远程 MCP Server：Streamable HTTP + Starlette 挂载 + 健康检查 + Host 白名单 + 多实例 requestState 密钥（mcp 2.x）

跑法:
    python demos/production/01_remote_server.py            # 监听 127.0.0.1:8780/mcp ，健康检查 /healthz
    python demos/production/01_remote_client.py            # 另一个终端：连它、调工具、演示 421 / 健康检查
环境变量:
    MCP_STATE_KEY   多实例部署时所有实例共享的密钥（>= 32 字节）：python -c "import secrets; print(secrets.token_hex(32))"
    MCP_SERVER_NAME 所有实例必须同名（audience），别用 per-pod 的名字
    PORT / ALLOWED_HOSTS（逗号分隔）
"""
from __future__ import annotations

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
