"""演示路径越权防御 + 大小限制 + 类型白名单 + SDK 的 safe_join / 资源模板默认防护（mcp 2.x）

跑法:
    python demos/production/04_safe_filesystem.py             # stdio
    python demos/production/04_safe_filesystem.py --selftest  # 进程内自测：各种攻击输入被拒绝
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.path_security import safe_join

SANDBOX = Path(tempfile.gettempdir()) / "mcp-sandbox"
SANDBOX.mkdir(exist_ok=True)
(SANDBOX / "hello.txt").write_text("hello sandbox")
MAX_SIZE = 1024 * 100  # 100KB
ALLOWED_EXT = {".txt", ".md", ".json", ".log"}

mcp = MCPServer("safe-fs")


def _safe_path(user_path: str) -> Path:
    """把用户（其实是 LLM）提供的相对路径转成 sandbox 内的绝对路径。safe_join 是 SDK 的路径包含边界。"""
    try:
        return Path(safe_join(SANDBOX, user_path))
    except Exception:  # 越权、绝对路径、空字节……统一成对模型友好、不泄漏路径结构的错误
        raise ToolError(f"路径不合法: {user_path!r}") from None


@mcp.tool()
def read_file(path: str) -> str:
    """读 sandbox 内的文件"""
    target = _safe_path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}")
    if target.suffix not in ALLOWED_EXT:
        raise ToolError(f"不支持的扩展名: {target.suffix}")
    if target.stat().st_size > MAX_SIZE:
        raise ToolError(f"文件过大: {target.stat().st_size}B (max {MAX_SIZE}B)")
    return target.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
def write_file(path: str, content: str) -> dict:
    """⚠️ 写入文件（破坏性操作，调用方应该让用户确认）"""
    target = _safe_path(path)
    if target.suffix not in ALLOWED_EXT:
        raise ToolError(f"不支持写入 {target.suffix}")
    if len(content.encode("utf-8")) > MAX_SIZE:
        raise ToolError("内容过大")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"path": str(target.relative_to(SANDBOX)), "size": target.stat().st_size}


# 资源模板：v2 默认就拒绝 `..`、绝对路径和空字节（在你的函数被调用之前）
@mcp.resource("sandbox://{+path}")
def sandbox_file(path: str) -> str:
    return read_file(path)


async def selftest() -> None:
    from mcp import Client, MCPError

    async with Client(mcp) as client:
        for bad in ("../../etc/passwd", "/etc/passwd", "a\x00b.txt", "evil.sh"):
            r = await client.call_tool("read_file", {"path": bad})
            print(f"read_file({bad!r:22}) -> is_error={r.is_error} | {r.content[0].text.splitlines()[0][:50]}")
        print("read_file('hello.txt')            ->", (await client.call_tool("read_file", {"path": "hello.txt"})).content[0].text)
        for uri in ("sandbox://hello.txt", "sandbox://../secret.txt", "sandbox:///etc/passwd"):
            try:
                print(f"read_resource({uri!r:26}) ->", (await client.read_resource(uri)).contents[0].text)
            except MCPError as e:
                print(f"read_resource({uri!r:26}) -> MCPError {e.code} {str(e)[:40]}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        asyncio.run(selftest())
    else:
        mcp.run()
