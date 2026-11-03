"""低层 Server（mcp 2.x）：on_* 处理函数传给构造函数，手动构造完整 Result

跑法（Streamable HTTP，:8000/mcp）:
    python demos/server/01_tools_lowlevel.py
自测（不起端口，Client 直接连 Server 对象）:
    python demos/server/01_tools_lowlevel.py --selftest
"""
from __future__ import annotations

import asyncio
import sys

from mcp import Client, MCPError
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

ADD = Tool(
    name="add",
    description="两数相加",
    input_schema={  # 低层 API 里这只是「声明」，调用前不会被校验！
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
)


async def list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
    return ListToolsResult(tools=[ADD])  # 必须自己构造完整的 ListToolsResult


async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    if params.name != "add":
        raise MCPError(INVALID_PARAMS, f"Unknown tool: {params.name}")  # 协议错误，带原 code
    args = params.arguments or {}  # 可能为 None，也没校验过
    try:
        total = int(args["a"]) + int(args["b"])
    except (KeyError, ValueError, TypeError):
        # 想让模型读到的错误：自己返回 is_error=True（抛普通异常只会变成 -32603）
        return CallToolResult(content=[TextContent(type="text", text="需要两个整数 a 和 b")], is_error=True)
    return CallToolResult(content=[TextContent(type="text", text=str(total))])


server = Server("low-level-demo", on_list_tools=list_tools, on_call_tool=call_tool)
app = server.streamable_http_app()  # 普通 ASGI app，可交给 uvicorn


async def selftest() -> None:
    async with Client(server) as client:  # 进程内连接，不用起端口
        print((await client.list_tools()).tools[0].name)
        print((await client.call_tool("add", {"a": 1, "b": 2})).content[0].text)
        r = await client.call_tool("add", {"a": "x"})
        print("is_error:", r.is_error, r.content[0].text)
        try:
            await client.call_tool("nope", {})
        except MCPError as e:
            print("MCPError:", e.code, e)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        asyncio.run(selftest())
    else:
        import uvicorn

        uvicorn.run(app, port=8000)
