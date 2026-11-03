"""配套 Client：演示 structured_content、is_error、progress_callback"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

SERVER = Path(__file__).parent / "01_tools_weather.py"


async def on_progress(progress: float, total: float | None, message: str | None) -> None:
    print(f"   [progress] {progress}/{total} {message}")


async def main() -> None:
    async with Client(StdioServerParameters(command=sys.executable, args=[str(SERVER)])) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "get_forecast")
        print("title:", tool.title, "| annotations:", tool.annotations)
        print("input required:", tool.input_schema.get("required"))
        print("output schema keys:", list((tool.output_schema or {}).keys()))

        print("\n== 正常调用（带进度）==")
        r = await client.call_tool("get_forecast", {"city": "Beijing", "days": 2}, progress_callback=on_progress)
        print("is_error:", r.is_error)
        print("structured_content:", r.structured_content)

        print("\n== ToolError：is_error=True，消息给模型 ==")
        r = await client.call_tool("get_forecast", {"city": "Atlantis"})
        print("is_error:", r.is_error, "|", r.content[0].text)

        print("\n== 参数越界：SDK 在调用函数之前就拒绝，同样是 is_error ==")
        r = await client.call_tool("get_forecast", {"city": "Beijing", "days": 99})
        print("is_error:", r.is_error, "|", r.content[0].text[:80].replace("\n", " "))

        print("\n== ResourceLink ==")
        r = await client.call_tool("find_design_file", {"name": "home"})
        print([c.type for c in r.content])


if __name__ == "__main__":
    asyncio.run(main())
