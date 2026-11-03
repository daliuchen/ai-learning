"""Tools 综合 demo：schema / structuredContent / annotations / ToolError / 进度（mcp 2.x）

跑法:
    python demos/server/01_tools_weather.py              # stdio
    python demos/server/01_tools_weather.py --http       # Streamable HTTP, :8000/mcp
    python demos/server/01_tools_weather_client.py       # 配套 Client，自动起子进程
"""
from __future__ import annotations

import asyncio
import sys
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ResourceLink, TextContent, ToolAnnotations

mcp = MCPServer("weather-pro")


class Forecast(BaseModel):
    city: str
    day: int
    temperature_c: float = Field(description="摄氏温度")
    conditions: str
    humidity: float = Field(ge=0, le=1, description="0-1 之间")


@mcp.tool(
    title="获取天气预报",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
)
async def get_forecast(
    city: Annotated[str, Field(min_length=1, description="城市名（中文/英文）")],
    ctx: Context,
    days: Annotated[int, Field(ge=1, le=7, description="未来几天")] = 1,
    unit: Literal["c", "f"] = "c",
) -> list[Forecast]:
    """查询某城市未来 N 天天气预报。"""
    if city.lower() == "atlantis":
        # ToolError：模型能读到这句话，并据此改参数重试
        raise ToolError(f"未知城市: {city}（提示：尝试 'Beijing' / 'Tokyo' / 'NYC'）")

    out: list[Forecast] = []
    for i in range(days):
        await ctx.report_progress(i + 1, total=days, message=f"第 {i + 1} 天")
        await asyncio.sleep(0.05)  # 模拟网络
        temp = 20.0 + i
        out.append(
            Forecast(
                city=city,
                day=i + 1,
                temperature_c=temp if unit == "c" else temp * 9 / 5 + 32,
                conditions=["晴", "多云", "雨"][i % 3],
                humidity=0.5,
            )
        )
    return out


@mcp.tool()
def find_design_file(name: str) -> list:
    """返回 ResourceLink（只给指针，不把大文件塞进上下文）+ 一段说明文字。"""
    return [
        TextContent(type="text", text=f"找到设计稿 {name}"),
        ResourceLink(type="resource_link", uri=f"figma://files/{name}", name=name, mime_type="application/figma"),
    ]


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run("streamable-http", host="127.0.0.1", port=8000)
    else:
        mcp.run()
