"""订阅资源变更（2026-07-28 的 subscriptions/listen）：Server 与 Client 在同一个文件里

跑法（需要 Streamable HTTP；订阅是一条长连接）:
    python demos/server/02_resources_subscribe.py server   # 终端 1：起 Server :8000/mcp
    python demos/server/02_resources_subscribe.py client   # 终端 2：监听并触发变更
    python demos/server/02_resources_subscribe.py          # 一条命令：后台起 Server + 跑 Client
"""
from __future__ import annotations

import asyncio
import sys

from mcp import Client
from mcp.client.subscriptions import ResourceUpdated, ToolsListChanged
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

mcp = MCPServer("Sprint Board")
BOARDS = {"sprint": {"design": False, "build": False, "ship": False}}


@mcp.resource("board://{name}")
def board(name: str) -> str:
    tasks = BOARDS[name]
    return "\n".join(f"[{'x' if done else ' '}] {task}" for task, done in tasks.items())


@mcp.tool()
async def complete_task(board: str, task: str, ctx: Context) -> str:
    BOARDS[board][task] = True
    # 发布变更：只送达「订阅了这个 URI」的 listen 流；没人订阅就是空操作
    await ctx.notify_resource_updated(f"board://{board}")
    return f"{task}: done"


def sprint_report() -> str:
    return f"{sum(d for t in BOARDS.values() for d in t.values())} task(s) done"


@mcp.tool()
async def enable_reports(ctx: Context) -> str:
    mcp.add_tool(sprint_report)  # 运行时新增工具
    await ctx.notify_tools_changed()  # 通知「工具列表变了」的订阅者
    return "reporting is live"


URL = "http://127.0.0.1:8000/mcp"


async def run_client() -> None:
    async with Client(URL) as watcher, Client(URL) as actor:
        events: list[str] = []

        async def follow() -> None:
            # 一次 subscriptions/listen：声明想听哪几类
            async with watcher.listen(tools_list_changed=True, resource_subscriptions=["board://sprint"]) as sub:
                print("honored:", sub.honored)
                async for ev in sub:
                    match ev:
                        case ResourceUpdated(uri=uri):
                            text = (await watcher.read_resource(uri)).contents[0].text
                            print("资源变了，重新读取：\n" + text)
                        case ToolsListChanged():
                            print("工具列表变了：", [t.name for t in (await watcher.list_tools()).tools])
                    events.append(type(ev).__name__)
                    if len(events) == 2:
                        return

        task = asyncio.create_task(follow())
        await asyncio.sleep(0.5)  # 等 listen 流建立
        await actor.call_tool("complete_task", {"board": "sprint", "task": "design"})
        await actor.call_tool("enable_reports", {})
        await asyncio.wait_for(task, 5)


async def main_all() -> None:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), port=8000, log_level="warning"))
    st = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        await run_client()
    finally:
        server.should_exit = True
        await st


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "server":
        mcp.run("streamable-http", port=8000)
    elif mode == "client":
        asyncio.run(run_client())
    else:
        asyncio.run(main_all())
