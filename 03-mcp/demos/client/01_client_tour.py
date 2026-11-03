"""Client 全景：连接信息 / list / call / 结果三件套 / 资源 / Prompt / 补全 / 翻页 / 回调（mcp 2.x）

跑法（进程内，不起端口）:
    python demos/client/01_client_tour.py
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from mcp import Client, MCPError
from mcp.client import ClientRequestContext
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    ElicitRequestParams,
    ElicitResult,
    PromptReference,
    ResourceTemplateReference,
    TextContent,
    TextResourceContents,
)

mcp = MCPServer("Bookshop", instructions="Search the catalog before recommending a book.")
GENRES = ["fiction", "non-fiction", "poetry"]


class Book(BaseModel):
    title: str
    author: str
    year: int


@mcp.tool(title="Search the catalog")
def search_books(query: str, limit: int = 10) -> str:
    """Search the catalog by title or author."""
    return f"Found 3 books matching {query!r} (showing up to {limit})."


@mcp.tool()
def lookup_book(title: str) -> Book:
    """Look up a book by its exact title."""
    if title != "Dune":
        raise ToolError(f"No book titled {title!r} in the catalog.")
    return Book(title="Dune", author="Frank Herbert", year=1965)


@mcp.resource("catalog://genres")
def genres() -> list[str]:
    """The genres the catalog is organised by."""
    return GENRES


@mcp.resource("catalog://genres/{genre}")
def books_in_genre(genre: str) -> str:
    """Every title we stock in one genre."""
    return f"3 books filed under {genre}."


@mcp.prompt(title="Recommend a book")
def recommend(genre: str) -> str:
    """Ask for a recommendation in a genre."""
    return f"Recommend one {genre} book from the catalog and say why."


@mcp.completion()
async def complete_genre(ref: PromptReference | ResourceTemplateReference, argument: CompletionArgument, context: CompletionContext | None) -> Completion | None:
    return Completion(values=[g for g in GENRES if g.startswith(argument.value)])


library = MCPServer("Library")


class CardHolder(BaseModel):
    name: str


@library.tool()
async def issue_card(ctx: Context) -> str:
    """Server 在工具执行中途主动向用户要输入（ctx.elicit，2025 风格）"""
    answer = await ctx.elicit("What name should go on the card?", schema=CardHolder)
    return f"Card issued to {answer.data.name}." if answer.action == "accept" else "No card issued."


async def main() -> None:
    async with Client(mcp) as client:
        # ---- 连接信息：进入 async with 时协商已完成 ----
        print("server_info     :", client.server_info.name)
        print("protocol_version:", client.protocol_version)
        print("instructions    :", client.instructions)
        print("capabilities    :", sorted(client.server_capabilities.model_dump(exclude_none=True)))

        # ---- list_tools ----
        for t in (await client.list_tools()).tools:
            print("tool            :", t.name, "|", t.title, "|", t.input_schema["required"])

        # ---- call_tool：content（给模型）/ structured_content（给代码）/ is_error ----
        r = await client.call_tool("lookup_book", {"title": "Dune"})
        print("content         :", [b.type for b in r.content], "| structured:", r.structured_content, "| is_error:", r.is_error)
        r = await client.call_tool("lookup_book", {"title": "Solaris"})
        print("ToolError       :", r.is_error, "|", r.content[0].text)
        r = await client.call_tool("does_not_exist", {})
        print("unknown tool    :", r.is_error, "|", r.content[0].text)  # 不是异常，也是 is_error=True

        # ---- 资源 ----
        print("resources       :", [x.uri for x in (await client.list_resources()).resources])
        print("templates       :", [x.uri_template for x in (await client.list_resource_templates()).resource_templates])
        for c in (await client.read_resource("catalog://genres/poetry")).contents:
            if isinstance(c, TextResourceContents):
                print("read            :", c.text)

        # ---- Prompt / 补全 ----
        p = await client.get_prompt("recommend", {"genre": "poetry"})
        print("prompt          :", p.messages[0].role, p.messages[0].content.text)
        c = await client.complete(PromptReference(name="recommend"), {"name": "genre", "value": "p"})
        print("complete        :", c.completion.values)

    # ---- 回调 = 能力声明：注册了 elicitation_callback，Client 才声明 elicitation ----
    async def on_elicit(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
        print("callback        :", params.message)
        return ElicitResult(action="accept", content={"name": "Ada Lovelace"})

    # 老式「Server 主动问」（ctx.elicit）只在 legacy 连接上有效，所以这里 mode="legacy"
    async with Client(library, mode="legacy", elicitation_callback=on_elicit) as client:
        r = await client.call_tool("issue_card", {})
        print("legacy elicit   :", r.content[0].text)
    async with Client(library, mode="legacy") as client:  # 没注册回调 = 没声明能力
        try:
            await client.call_tool("issue_card", {})
        except MCPError as e:
            print("no callback     :", e.code, e)


if __name__ == "__main__":
    asyncio.run(main())
