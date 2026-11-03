"""把当前项目作为只读知识库暴露：README / 文件树 / 任意 docs 下的 markdown（mcp 2.x）

跑法:
    python demos/server/02_resources_kb.py            # stdio
    python demos/server/02_resources_kb.py --selftest # 进程内自测
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError

mcp = MCPServer("kb-server")
ROOT = Path(__file__).resolve().parents[2]  # 指向 03-mcp/


@mcp.resource("kb://readme", title="项目 README", mime_type="text/markdown")
def readme() -> str:
    return (ROOT / "README.md").read_text()


@mcp.resource("kb://tree", title="项目文件树", mime_type="text/plain")
def file_tree() -> str:
    return "\n".join(str(p.relative_to(ROOT)) for p in sorted(ROOT.rglob("*.md")))


@mcp.resource("kb://doc/{+path}", mime_type="text/markdown")  # {+path} 是 RFC 6570：可跨多段路径
def doc(path: str) -> str:
    """读 docs/ 下的任意 markdown 文件，path 是 docs/ 后的相对路径"""
    docs_root = (ROOT / "docs").resolve()
    target = (docs_root / path).resolve()
    # 双保险：SDK 已默认拒绝 `..` 之类的路径穿越，这里再确认一次
    if not target.is_relative_to(docs_root) or not target.is_file():
        raise ResourceNotFoundError(f"没有这篇文档: {path}")
    return target.read_text()


async def selftest() -> None:
    from mcp import Client, MCPError

    async with Client(mcp) as client:
        print([t.uri_template for t in (await client.list_resource_templates()).resource_templates])
        print([r.uri for r in (await client.list_resources()).resources])
        c = (await client.read_resource("kb://doc/01-basics/03-primitives.md")).contents[0]
        print(c.uri, c.mime_type, c.text[:30].replace("\n", " "))
        for bad in ("kb://doc/nope.md", "kb://doc/../README.md"):
            try:
                await client.read_resource(bad)
            except MCPError as e:
                print(bad, "->", e.code, str(e)[:60])


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        asyncio.run(selftest())
    else:
        mcp.run()
