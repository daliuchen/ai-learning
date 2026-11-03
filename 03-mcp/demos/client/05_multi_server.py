"""多 Server 聚合：自己写的并发聚合器（Client + 命名空间）/ 官方 ClientSessionGroup（mcp 2.x）

跑法:
    python demos/client/05_multi_server.py
"""
from __future__ import annotations

import asyncio
import sys

import anyio
import anyio.abc
from pathlib import Path

from mcp import Client, ClientSessionGroup, StdioServerParameters
from mcp.types import Implementation

DEMOS = Path(__file__).resolve().parents[1]
CONFIGS = {
    "hello": StdioServerParameters(command=sys.executable, args=[str(DEMOS / "basics" / "06_first_server.py")]),
    "booking": StdioServerParameters(command=sys.executable, args=[str(DEMOS / "server" / "08_errors_booking.py")]),
    "broken": StdioServerParameters(command=sys.executable, args=["-c", "import sys; sys.exit(1)"]),  # 故意起不来
}


class MultiServer:
    """方案 A：自己聚合——每个 Server 一个 Client。

    关键：anyio 要求「在哪个 task 里 enter，就必须在同一个 task 里 exit」。
    所以不能 asyncio.gather 里 enter、再在别处 exit（会报 'Attempted to exit cancel scope in a different task'）。
    正确做法：每个 Server 一个**长驻 task**，由它自己 `async with Client(...)`，用事件通知「就绪」并等待「停止」。
    """

    def __init__(self, configs: dict[str, StdioServerParameters]) -> None:
        self.configs = configs
        self.clients: dict[str, Client] = {}
        self._tg: anyio.abc.TaskGroup | None = None
        self._stop = anyio.Event()

    async def _run_one(self, name: str, params: StdioServerParameters, ready: anyio.Event) -> None:
        try:
            async with Client(params) as client:
                self.clients[name] = client
                ready.set()
                await self._stop.wait()  # 一直活到 __aexit__
        except Exception as e:  # noqa: BLE001  单个 Server 失败不拖垮整个 Host
            print(f"❌ {name} 启动失败: {type(e).__name__}")
            ready.set()

    async def __aenter__(self) -> "MultiServer":
        self._tg = anyio.create_task_group()
        await self._tg.__aenter__()
        readies = []
        for name, params in self.configs.items():  # 并发启动：全部先起 task，再统一等就绪
            ev = anyio.Event()
            readies.append(ev)
            self._tg.start_soon(self._run_one, name, params, ev)
        for ev in readies:
            await ev.wait()
        return self

    async def __aexit__(self, *exc) -> None:
        self._stop.set()  # 让各 task 在自己的上下文里退出 Client
        assert self._tg is not None
        await self._tg.__aexit__(*exc)

    async def list_tools(self) -> list[dict]:
        out = []
        for server, client in self.clients.items():
            for t in (await client.list_tools()).tools:
                out.append({
                    "name": f"{server}__{t.name}",  # 命名空间：Claude Code 的约定
                    "description": f"[Server: {server}] {t.description or ''}",  # 描述里带来源，LLM 不会混淆
                    "input_schema": t.input_schema,
                })
        return out

    async def call_tool(self, namespaced: str, arguments: dict):
        server, original = namespaced.split("__", 1)
        return await self.clients[server].call_tool(original, arguments)


async def main() -> None:
    async with MultiServer(CONFIGS) as ms:
        print("✅ 已连接:", list(ms.clients))
        for t in await ms.list_tools():
            print(f"  {t['name']:28} {t['description'][:40].splitlines()[0]}")
        r = await ms.call_tool("hello__add", {"a": 2, "b": 3})
        print("hello__add(2,3) =", r.content[0].text)

    # 方案 B：官方 ClientSessionGroup（注意：它底层是 ClientSession，用经典 initialize 握手）
    def by_server(name: str, server_info: Implementation) -> str:
        return f"{server_info.name}.{name}"

    async with ClientSessionGroup(component_name_hook=by_server) as group:
        for name in ("hello", "booking"):
            await group.connect_to_server(CONFIGS[name])
        print("\nClientSessionGroup:", sorted(group.tools))
        r = await group.call_tool("hello-mcp.add", {"a": 40, "b": 2})
        print("group.call_tool  =", r.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
