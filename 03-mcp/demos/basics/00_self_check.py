"""自检脚本：验证 MCP SDK 安装 & 基本功能

跑法:
    python demos/basics/00_self_check.py

期望:
    全部 ✅, 退出码 0
"""
from __future__ import annotations

import asyncio
import sys


def check_python_version() -> bool:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 10)
    print(f"{'✅' if ok else '❌'} Python {major}.{minor} (需要 >=3.10)")
    return ok


def check_mcp_import() -> bool:
    try:
        import mcp  # noqa: F401
        from mcp.server.fastmcp import FastMCP  # noqa: F401
        from mcp import ClientSession  # noqa: F401
        from mcp.client.stdio import stdio_client, StdioServerParameters  # noqa: F401

        version = getattr(mcp, "__version__", "unknown")
        print(f"✅ mcp == {version}")
        return True
    except ImportError as e:
        print(f"❌ mcp 导入失败：{e}")
        print("   修复：pip install -U mcp")
        return False


async def check_client_server_roundtrip() -> bool:
    """启一个临时 Server，自己当 Client 连它，跑一遍完整 initialize/list/call"""
    import os
    import pathlib
    import tempfile

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    inline_server = """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("self-check")

@mcp.tool()
def ping(msg: str = "hi") -> str:
    \"\"\"回声测试工具\"\"\"
    return f"pong: {msg}"

mcp.run()
"""
    tmp = pathlib.Path(tempfile.gettempdir()) / "_mcp_self_check_server.py"
    tmp.write_text(inline_server)

    params = StdioServerParameters(command=sys.executable, args=[str(tmp)])
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "self-check", f"serverInfo 异常: {init.serverInfo}"

                tools = await session.list_tools()
                assert any(t.name == "ping" for t in tools.tools), "ping 工具没找到"

                result = await session.call_tool("ping", {"msg": "ok"})
                text = result.content[0].text
                assert "pong: ok" in text, f"返回值异常: {text}"

                print("✅ Client/Server 完整流程跑通 (initialize → list_tools → call_tool)")
                return True
    except Exception as e:
        print(f"❌ Client/Server 自检失败：{e!r}")
        return False
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def main() -> int:
    print("=== MCP 环境自检 ===")
    ok = True
    ok &= check_python_version()
    ok &= check_mcp_import()
    ok &= asyncio.run(check_client_server_roundtrip())
    print()
    if ok:
        print("🎉 一切正常，可以开始 06-first-server.md")
        return 0
    print("⚠️  请按上方提示修复后再继续")
    return 1


if __name__ == "__main__":
    sys.exit(main())
