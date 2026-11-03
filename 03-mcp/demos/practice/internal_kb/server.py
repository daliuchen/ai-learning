"""内部知识库 MCP Server（mcp 2.x）：语义搜索 + 关键词搜索 + 按 ID 读全文

跑法:
    python demos/practice/internal_kb/server.py              # stdio
    python demos/practice/internal_kb/server.py --selftest   # 进程内自测（设了 OPENAI_API_KEY 就走语义检索）
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # 让 `python server.py` 和 Client 起的子进程都能 import indexer

from indexer import KBIndex, build_index, semantic_search  # noqa: E402

from mcp.server import MCPServer  # noqa: E402
from mcp.server.mcpserver import Context  # noqa: E402
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

logger = logging.getLogger("kb")  # 日志走 stderr，别 print
ROOT = Path(__file__).resolve().parent
DOCS_DIR = Path(os.getenv("KB_DOCS_DIR", ROOT / "docs"))
CACHE_DIR = Path(os.getenv("KB_CACHE_DIR", ROOT / ".cache"))


_INDEX: KBIndex | None = None  # 静态资源（URI 里没有 {变量}）不能声明 Context 参数，只能读模块级状态


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[KBIndex]:
    global _INDEX
    # 启动时索引一次，所有请求共享（v2：HTTP 模式 lifespan 也只跑一次）
    idx = _INDEX = await asyncio.to_thread(build_index, DOCS_DIR, CACHE_DIR)
    logger.info("已索引 %d 个 chunk（语义检索=%s）", len(idx.chunks), idx.semantic)
    yield idx


mcp = MCPServer(
    "internal-kb",
    instructions="团队内部知识库。回答流程/规范/FAQ 类问题前，先用 search_kb 或 keyword_search_kb 查，并引用返回的 path。",
    lifespan=app_lifespan,
)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


@mcp.tool(annotations=READ_ONLY)
async def search_kb(query: str, ctx: Context[KBIndex], top_k: int = 5) -> list[dict]:
    """语义搜索内部知识库。

    Args:
        query: 自然语言问题，例如"我们的 PR 流程是怎样的"
        top_k: 返回最相关的 N 条，默认 5

    返回 list[{doc_id, path, snippet, score}]，score 是余弦相似度（越大越相似）。
    """
    idx = ctx.request_context.lifespan_context
    if not idx.semantic:
        raise ToolError("语义检索未启用（没有配置 embedding 的 API Key）；请改用 keyword_search_kb")
    hits = await asyncio.to_thread(semantic_search, idx, query, top_k)
    return [{"doc_id": c.doc_id, "path": c.path, "snippet": c.text[:300], "score": round(s, 4)} for c, s in hits]


@mcp.tool(annotations=READ_ONLY)
def keyword_search_kb(keyword: str, ctx: Context[KBIndex]) -> list[dict]:
    """关键词精确搜索（不走向量，直接匹配）。"""
    idx = ctx.request_context.lifespan_context
    kw = keyword.lower()
    out, seen = [], set()
    for c in idx.chunks:
        pos = c.text.lower().find(kw)
        if pos >= 0 and (c.path not in seen):
            seen.add(c.path)
            out.append({"path": c.path, "snippet": c.text[max(0, pos - 80) : pos + len(keyword) + 80]})
    return out


@mcp.resource("kb://doc/{doc_id}", mime_type="text/markdown")
def get_doc(doc_id: str, ctx: Context[KBIndex]) -> str:
    """按 doc_id 读完整文档（资源函数也能声明 ctx；v1 的 mcp.get_context() 已删除）"""
    idx = ctx.request_context.lifespan_context
    chunks = sorted((c for c in idx.chunks if c.doc_id == doc_id), key=lambda c: c.index)
    if not chunks:
        raise ResourceNotFoundError(f"未找到文档: {doc_id}")  # 客户端收到 -32602，data 里带 URI
    return "\n".join(c.text for c in chunks)  # 注意：按字符切块有重叠，这里仅作演示；生产里直接读原文件


@mcp.resource("kb://index", mime_type="application/json")
def index_listing() -> list[dict]:
    """列出所有索引文档（静态资源：不能带 ctx，所以读 lifespan 里设置的模块级 _INDEX）"""
    seen: dict[str, str] = {}
    for c in (_INDEX.chunks if _INDEX else []):
        seen.setdefault(c.doc_id, c.path)
    return [{"doc_id": k, "path": v} for k, v in seen.items()]


async def selftest() -> None:
    from mcp import Client

    async with Client(mcp) as client:
        print("语义检索:", "开启" if os.environ.get("OPENAI_API_KEY") else "关闭（无 API Key，降级为关键词）")
        print("keyword 'CI'   ->", [h["path"] for h in (await client.call_tool("keyword_search_kb", {"keyword": "CI"})).structured_content["result"]])
        r = await client.call_tool("search_kb", {"query": "怎么报销发票", "top_k": 2})
        if r.is_error:
            print("search_kb      ->", r.content[0].text)
        else:
            print("search_kb      ->", [(h["path"], h["score"]) for h in r.structured_content["result"]])
        listing = (await client.read_resource("kb://index")).contents[0].text
        print("kb://index     ->", listing.replace("\n", "")[:90], "...")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        asyncio.run(selftest())
    else:
        mcp.run()
