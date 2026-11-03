"""自检脚本：验证 MCP SDK 安装 & 基本功能（mcp 2.x）

跑法:
    python demos/basics/00_self_check.py

期望:
    全部 ✅, 退出码 0
"""
from __future__ import annotations

import asyncio
import sys
from importlib.metadata import PackageNotFoundError, version


def check_python_version() -> bool:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 11)
    print(f"{'✅' if ok else '❌'} Python {major}.{minor} (需要 >=3.11)")
    return ok


def check_mcp_import() -> bool:
    try:
        from mcp import Client, StdioServerParameters  # noqa: F401
        from mcp.server import MCPServer  # noqa: F401

        try:
            v = version("mcp")
        except PackageNotFoundError:
            v = "unknown"
        major = int(v.split(".")[0]) if v[0].isdigit() else 0
        print(f"{'✅' if major >= 2 else '⚠️ '} mcp == {v} (本手册基于 2.x)")
        return True
    except ImportError as e:
        print(f"❌ mcp 导入失败：{e}")
        print("   修复：pip install -U 'mcp>=2.3'")
        print("   若你的环境是 mcp 1.x，把 `from mcp.server import MCPServer` 换成 `from mcp.server.fastmcp import FastMCP`")
        return False


async def check_client_server_roundtrip() -> bool:
    """启一个临时 Server，自己当 Client 连它，跑一遍完整流程"""
    import pathlib
    import tempfile

    from mcp import Client, StdioServerParameters

    inline_server = """
from mcp.server import MCPServer

mcp = MCPServer("self-check")

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
        async with Client(params) as client:
            assert client.server_info and client.server_info.name == "self-check", client.server_info
            tools = await client.list_tools()
            assert any(t.name == "ping" for t in tools.tools), "ping 工具没找到"
            result = await client.call_tool("ping", {"msg": "ok"})
            assert "pong: ok" in result.content[0].text, result
            print(f"✅ Client/Server 完整流程跑通 (协议 {client.protocol_version}: 协商 → list_tools → call_tool)")
            return True
    except Exception as e:
        print(f"❌ Client/Server 自检失败：{e!r}")
        return False
    finally:
        tmp.unlink(missing_ok=True)


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
