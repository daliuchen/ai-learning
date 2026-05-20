"""
01_mcp.py
=========
Pydantic AI 与 MCP（Model Context Protocol）集成示例。

包含三个独立 demo（用命令行参数选择跑哪个）：

    python demos/modules/01_mcp.py client     # Agent 作 client，调用 filesystem MCP server
    python demos/modules/01_mcp.py server     # 用 FastMCP 启动一个最小 MCP server
    python demos/modules/01_mcp.py test       # 没 API Key 也能跑：用 TestModel + 假 MCP server 验证注册流程

参考：
- https://ai.pydantic.dev/mcp/client/
- https://ai.pydantic.dev/mcp/server/
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# =====================================================================
# Demo A：Agent 作 client，调用官方 filesystem MCP server
# =====================================================================
async def demo_client() -> None:
    """
    依赖：本机有 npx（即 Node.js 安装好）。
    第一次跑 npx 会自动下载 @modelcontextprotocol/server-filesystem。
    """
    from pydantic_ai import Agent
    from pydantic_ai.mcp import MCPServerStdio

    if not os.getenv("OPENAI_API_KEY"):
        print("[skip] OPENAI_API_KEY 未设置，请用 `python ... test` 跑离线版")
        return

    # 准备工作目录
    workdir = Path("/tmp/mcp-demo")
    workdir.mkdir(exist_ok=True)

    fs = MCPServerStdio(
        "npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", str(workdir)],
        timeout=15,
    )

    agent = Agent(
        "openai:gpt-4o-mini",
        toolsets=[fs],
        system_prompt=(
            f"你是文件管家，工作目录是 {workdir}。"
            "用户问你问题时，可以读写该目录下的文件。"
        ),
    )

    async with agent:
        print("===== Q1: 创建文件 =====")
        r1 = await agent.run("创建一个 hello.txt，内容是『你好，MCP！』")
        print(r1.output)

        print("\n===== Q2: 读取文件 =====")
        r2 = await agent.run("hello.txt 里写的是什么？")
        print(r2.output)

        print("\n===== Q3: 列目录 =====")
        r3 = await agent.run(f"{workdir} 下有哪些文件？")
        print(r3.output)


# =====================================================================
# Demo B：用 FastMCP 启动一个最小 MCP server
# =====================================================================
def demo_server() -> None:
    """
    单独运行这个会启动一个 stdio MCP server。
    用 MCP Inspector 或别的 client 连进来测试：

        npx @modelcontextprotocol/inspector python demos/modules/01_mcp.py server
    """
    from mcp.server.fastmcp import FastMCP
    from pydantic_ai import Agent

    mcp = FastMCP("pydantic-ai-demo-server")

    # 底层用 Pydantic AI Agent 当"大脑"
    poet_agent = Agent(
        "openai:gpt-4o-mini" if os.getenv("OPENAI_API_KEY") else None,
        system_prompt="你是一位押韵小诗人，回答必须押韵。",
    )

    @mcp.tool()
    async def echo(text: str) -> str:
        """原样返回输入"""
        return f"[server echo] {text}"

    @mcp.tool()
    async def write_poem(theme: str) -> str:
        """根据主题写一首押韵小诗"""
        if poet_agent.model is None:
            return f"（假装写了一首关于「{theme}」的诗，因为没设 API Key）"
        r = await poet_agent.run(f"主题：{theme}")
        return r.output

    @mcp.resource("config://demo")
    def app_config() -> str:
        """暴露一个静态资源"""
        return '{"name": "pydantic-ai-demo", "version": "0.1.0"}'

    @mcp.prompt()
    def summarize_prompt(text: str) -> str:
        """暴露一个可复用 prompt 模板"""
        return f"请用一句中文总结下面这段话：\n\n{text}"

    print("[server] starting on stdio... (Ctrl+C to stop)", file=sys.stderr)
    mcp.run()


# =====================================================================
# Demo C：用 TestModel 离线验证 MCP 注册流程
# =====================================================================
async def demo_test() -> None:
    """
    没 API Key 也能跑：
    - 启动一个本地写的小 MCP server 子进程
    - 用 TestModel 当模型，验证 toolsets 注册流程
    """
    from pydantic_ai import Agent
    from pydantic_ai.mcp import MCPServerStdio
    from pydantic_ai.models.test import TestModel

    # 启动同文件作为 server（subcommand=server）
    me = Path(__file__).resolve()
    server = MCPServerStdio(
        sys.executable,
        args=[str(me), "server"],
        env={"OPENAI_API_KEY": ""},   # 故意空，让 server 里也走 TestModel
        timeout=10,
    )

    agent = Agent(TestModel(), toolsets=[server])

    async with agent:
        # TestModel 默认会"调用所有可见工具一次"再 final，借此验证注册成功
        result = await agent.run("hello")
        print("===== output =====")
        print(result.output)
        print("\n===== 已注册的工具 =====")
        for msg in result.all_messages():
            for part in getattr(msg, "parts", []):
                if hasattr(part, "tool_name"):
                    print(" -", part.tool_name)


# =====================================================================
# 入口
# =====================================================================
def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    if mode == "client":
        asyncio.run(demo_client())
    elif mode == "server":
        demo_server()
    elif mode == "test":
        asyncio.run(demo_test())
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
