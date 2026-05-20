"""Hello MCP —— 演示三大原语的最小 Server

跑法（stdio 模式，给 Host 启动用）:
    python demos/basics/06_first_server.py

可视化调试:
    npx @modelcontextprotocol/inspector python demos/basics/06_first_server.py
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hello-mcp")


# ===== Tools：模型可调用的"动词" =====


@mcp.tool()
def add(a: int, b: int) -> int:
    """两个整数相加。

    Args:
        a: 被加数
        b: 加数
    """
    return a + b


@mcp.tool()
def current_time(timezone: str = "Asia/Shanghai") -> str:
    """获取当前时间（按指定时区）。

    Args:
        timezone: IANA 时区标识，默认 Asia/Shanghai。
                  例：UTC、America/New_York、Europe/London
    """
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        return f"❌ 未知时区: {timezone}"
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M:%S %Z%z")


# ===== Resource：应用可读取的"上下文" =====


@mcp.resource("hello://greeting/{name}")
def greeting(name: str) -> str:
    """给指定姓名的人生成一个问候语"""
    return f"你好，{name}！欢迎使用 MCP。今天是 {datetime.now():%Y-%m-%d}。"


# ===== Prompt：用户显式触发的"模板" =====


@mcp.prompt(name="code-review")
def code_review(file: str, language: str = "python") -> str:
    """生成一段 code review 引导。

    Args:
        file: 要审查的文件路径
        language: 编程语言，默认 python
    """
    return (
        f"请对 {language} 文件 `{file}` 做代码 review，关注以下方面：\n"
        f"1. 安全性（注入、敏感信息泄漏）\n"
        f"2. 性能（明显的算法或 IO 瓶颈）\n"
        f"3. 可读性（命名、注释、复杂度）\n"
        f"4. 测试覆盖率（关键路径有没有用例）\n"
        f"\n请按 issue 严重度从高到低排列。"
    )


if __name__ == "__main__":
    mcp.run()  # 默认 stdio
