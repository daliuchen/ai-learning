"""Completion（参数补全）+ Pagination（低层 Server 手写分页）—— mcp 2.x

跑法:
    python demos/server/05_completion_paged.py    # 进程内自测，两部分都会跑
"""
from __future__ import annotations

import asyncio
from typing import Any

from mcp import Client
from mcp.server import MCPServer, Server, ServerRequestContext
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    ListResourcesResult,
    PaginatedRequestParams,
    PromptReference,
    Resource,
    ResourceTemplateReference,
)

# ============ 1. Completion：MCPServer 上唯一的一个 @mcp.completion() 处理函数 ============
mcp = MCPServer("kb-search")
DOCS = [f"doc_{i:03d}" for i in range(100)]
TAGS = ["python", "javascript", "rust", "go", "java", "swift", "kotlin"]
REPOS_BY_OWNER = {"modelcontextprotocol": ["python-sdk", "typescript-sdk", "inspector"], "pydantic": ["pydantic", "pydantic-ai"]}


@mcp.resource("kb://doc/{doc_id}")
def doc(doc_id: str) -> str:
    return f"内容: {doc_id}"


@mcp.resource("github://repos/{owner}/{repo}")
def repo(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


@mcp.prompt(name="search-docs")
def search_docs(query: str, tag: str = "all") -> str:
    return f"搜索: {query}（标签: {tag}）"


@mcp.completion()  # 注册这个处理函数 = 声明 completions 能力；必须是 async def
async def complete(
    ref: PromptReference | ResourceTemplateReference,
    argument: CompletionArgument,
    context: CompletionContext | None,  # 已经填好的其它参数（依赖补全）
) -> Completion | None:
    prefix = argument.value.lower()  # SDK 不会替你过滤，startswith 要自己写
    if isinstance(ref, ResourceTemplateReference) and ref.uri == "kb://doc/{doc_id}" and argument.name == "doc_id":
        matches = [d for d in DOCS if d.startswith(prefix)]
        return Completion(values=matches[:100], total=len(matches), has_more=len(matches) > 100)
    if isinstance(ref, PromptReference) and ref.name == "search-docs" and argument.name == "tag":
        return Completion(values=[t for t in TAGS if t.startswith(prefix)])
    if isinstance(ref, ResourceTemplateReference) and argument.name == "repo":
        owner = (context.arguments or {}).get("owner", "") if context else ""
        return Completion(values=[r for r in REPOS_BY_OWNER.get(owner, []) if r.startswith(prefix)])
    return None  # None = 没有建议，不是错误


# ============ 2. Pagination：MCPServer 不暴露分页 hook，用低层 Server 自己写 ============
BOOKS = [f"book-{n}" for n in range(1, 101)]
PAGE_SIZE = 10


async def list_books(ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None) -> ListResourcesResult:
    start = 0 if params is None or params.cursor is None else int(params.cursor)
    end = start + PAGE_SIZE
    page = [Resource(uri=f"books://catalog/{n}", name=n) for n in BOOKS[start:end]]
    return ListResourcesResult(resources=page, next_cursor=str(end) if end < len(BOOKS) else None)


paged = Server("Bookshop", on_list_resources=list_books)
# 要部署的话：app = paged.streamable_http_app()，再 `uvicorn 模块:app`


async def main() -> None:
    async with Client(mcp) as client:
        print("completions 能力:", client.server_capabilities.completions)
        r = await client.complete(PromptReference(name="search-docs"), {"name": "tag", "value": "ja"})
        print("tag=ja ->", r.completion.values)
        r = await client.complete(ResourceTemplateReference(uri="kb://doc/{doc_id}"), {"name": "doc_id", "value": "doc_01"})
        print("doc_01* ->", r.completion.values[:3], "total =", r.completion.total)
        r = await client.complete(
            ResourceTemplateReference(uri="github://repos/{owner}/{repo}"),
            {"name": "repo", "value": ""},
            context_arguments={"owner": "pydantic"},
        )
        print("owner=pydantic repo ->", r.completion.values)

    async with Client(paged) as client:  # 客户端翻页循环
        books: list[Resource] = []
        cursor: str | None = None
        while True:
            page = await client.list_resources(cursor=cursor)
            books.extend(page.resources)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        print("翻页取回", len(books), "条")


if __name__ == "__main__":
    asyncio.run(main())
