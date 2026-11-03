"""Elicitation + Roots：Resolve（一份代码适配 2025/2026）+ 手写 MRTR + 手动驱动重试循环（mcp 2.x）

跑法（进程内；不需要 API Key）:
    python demos/server/04_roots_elicit.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from mcp import Client
from mcp.client import ClientRequestContext
from mcp.server import MCPServer
from mcp.server.mcpserver import (
    AcceptedElicitation,
    Context,
    DeclinedElicitation,
    Elicit,
    ElicitationResult,
    ListRoots,
    Resolve,
)
from mcp.types import (
    ElicitRequest,
    ElicitRequestFormParams,
    ElicitRequestParams,
    ElicitResult,
    InputRequiredResult,
    ListRootsResult,
    Root,
    TextContent,
)

mcp = MCPServer("project-analyzer")


# ---------- 1) 推荐：Resolve(Elicit) —— 问题挂在参数上，SDK 按连接的协议版本选择传递方式 ----------
class Confirm(BaseModel):
    ok: bool = Field(description="确认继续？")
    include_hidden: bool = Field(default=False, description="是否包含隐藏文件")


def confirm_analysis(path: str) -> Elicit[Confirm]:
    # 注意：问题文本只能由工具参数等稳定数据构造；每一轮重试都必须渲染出完全相同的问题
    return Elicit(f"将分析目录 {path}。确认继续？", Confirm)


@mcp.tool()
async def analyze(path: str, confirm: Annotated[ElicitationResult[Confirm], Resolve(confirm_analysis)]) -> str:
    """分析一个目录（先问用户授权）"""
    match confirm:
        case AcceptedElicitation(data=Confirm(ok=True) as c):
            files = [p for p in Path(path).rglob("*") if p.is_file() and (c.include_hidden or not p.name.startswith("."))]
            return f"{path}: {len(files)} 个文件"
        case AcceptedElicitation():
            return "用户选择不分析"
        case DeclinedElicitation():
            return "用户拒绝"
        case _:
            return "用户取消"


# ---------- 2) Roots（已弃用，但仍可用）：同样用 Resolve ----------
def workspace_roots() -> ListRoots:
    return ListRoots()


@mcp.tool()
async def first_root(roots: Annotated[ListRootsResult, Resolve(workspace_roots)]) -> str:
    """返回 Client 声明的第一个工作目录（Roots 只是协作提示，不是安全边界）"""
    return str(roots.roots[0].uri) if roots.roots else "Client 没声明 roots"


# ---------- 3) 手写 MRTR：不走 Resolve，直接返回 InputRequiredResult（和 Resolve 不能混用于同一个工具）----------
ASK_REGION = ElicitRequest(
    params=ElicitRequestFormParams(
        message="数据库放在哪个区域？",
        requested_schema={"type": "object", "properties": {"region": {"type": "string"}}, "required": ["region"]},
    )
)


@mcp.tool()
async def provision(name: str, ctx: Context) -> str | InputRequiredResult:
    """手写多轮请求：第一轮返回问题，重试轮从 ctx.input_responses 读答案"""
    answer = (ctx.input_responses or {}).get("region")
    if not isinstance(answer, ElicitResult) or answer.content is None:
        return InputRequiredResult(input_requests={"region": ASK_REGION}, request_state="provision-v1")
    return f"已在 {answer.content['region']} 创建数据库 {name!r}"


# ---------- 演示 Client ----------
async def on_elicit(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
    print(f"  [Client 回调] 收到提问: {params.message}")
    if "区域" in params.message:
        return ElicitResult(action="accept", content={"region": "eu-west-1"})
    return ElicitResult(action="accept", content={"ok": True, "include_hidden": False})


async def list_roots(context: ClientRequestContext) -> ListRootsResult:
    return ListRootsResult(roots=[Root(uri="file:///tmp/demo-workspace", name="demo")])  # type: ignore[arg-type]


async def main() -> None:
    here = str(Path(__file__).parent)
    for mode in ("auto", "legacy"):
        async with Client(mcp, mode=mode, elicitation_callback=on_elicit, list_roots_callback=list_roots) as client:
            print(f"== mode={mode} -> 协议 {client.protocol_version}")
            print("  analyze   :", (await client.call_tool("analyze", {"path": here})).content[0].text)
            print("  first_root:", (await client.call_tool("first_root", {})).content[0].text)

    # 手动驱动 MRTR：看清每一轮线上发生了什么（只有 2026 连接有这个机制）
    async with Client(mcp, elicitation_callback=on_elicit) as client:
        r = await client.session.call_tool("provision", {"name": "orders"}, allow_input_required=True)
        print("== 第 1 轮返回:", type(r).__name__, "| 要问:", list(r.input_requests), "| request_state 已加密:", r.request_state != "provision-v1")
        responses = {"region": ElicitResult(action="accept", content={"region": "ap-southeast-1"})}
        r = await client.session.call_tool("provision", {"name": "orders"}, input_responses=responses, request_state=r.request_state, allow_input_required=True)
        print("== 第 2 轮返回:", type(r).__name__, "|", r.content[0].text)
        # 自动循环版：Client 替你重试，调用方看不到中间轮
        print("== 自动循环  :", (await client.call_tool("provision", {"name": "orders"})).content[0].text)

    # 没注册回调：2026 里 SDK 的占位回调会拒绝，整个调用以错误结束
    async with Client(mcp) as client:
        try:
            await client.call_tool("analyze", {"path": here})
        except Exception as e:  # noqa: BLE001
            print("== 无回调    :", type(e).__name__, e)


if __name__ == "__main__":
    asyncio.run(main())
