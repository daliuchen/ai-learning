"""完整错误处理的订票 Server：ToolError / MCPError / 崩溃 / schema 校验 / 幂等键（mcp 2.x）

跑法:
    python demos/server/08_errors_booking.py             # stdio
    python demos/server/08_errors_booking.py --selftest  # 进程内自测：逐种错误对比 Client 看到什么
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
from datetime import date
from typing import Annotated

from pydantic import BaseModel, Field

from mcp import MCPError
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import INVALID_PARAMS, ToolAnnotations

logger = logging.getLogger("booking")
mcp = MCPServer("booking")

FAKE_DB = {"u_001": {"name": "Alice", "tier": "vip"}, "u_002": {"name": "Bob", "tier": "standard"}}
IDEMPOTENT_RESULTS: dict[str, dict] = {}


class BookingResult(BaseModel):
    booking_id: str
    flight: str
    seats: int
    user_name: str


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))
async def book_flight(
    user_id: Annotated[str, Field(min_length=1)],
    flight_no: Annotated[str, Field(pattern=r"^[A-Z]{2}\d{2,4}$", description="航班号，如 CA1981")],
    date_str: Annotated[str, Field(description="YYYY-MM-DD")],
    seats: Annotated[int, Field(ge=1, le=9)],
    idempotency_key: Annotated[str, Field(description="客户端生成的幂等键；重试用同一个")],
) -> BookingResult:
    """订机票：校验日期 / 用户 / 座位数；相同 idempotency_key 重试不会重复下单。"""
    if (hit := IDEMPOTENT_RESULTS.get(idempotency_key)) is not None:
        return BookingResult(**hit)  # 重放保护

    try:
        flight_date = date.fromisoformat(date_str)
    except ValueError:
        # 模型能纠正的错误 → ToolError（模型读得到消息）
        raise ToolError(f"日期格式错: '{date_str}'，期望 YYYY-MM-DD，例如 2026-12-25") from None
    if flight_date < date.today():
        raise ToolError(f"日期 {flight_date} 已过去，不能预订")

    user = FAKE_DB.get(user_id)
    if user is None:
        raise ToolError(f"用户不存在: {user_id}（提示：UID 应为 u_ + 3 位数字）")
    if user["tier"] == "standard" and seats > 4:
        raise ToolError(f"普通用户单次最多订 4 个座位，你申请了 {seats} 个")

    await asyncio.sleep(0.05)  # 模拟下单
    booking_id = "bk_" + hashlib.sha1(f"{user_id}{flight_no}{date_str}".encode()).hexdigest()[:8]
    result = BookingResult(booking_id=booking_id, flight=flight_no, seats=seats, user_name=user["name"])
    IDEMPOTENT_RESULTS[idempotency_key] = result.model_dump()
    return result


@mcp.tool()
def admin_only(ctx: Context) -> str:
    """演示 MCPError：模型再聪明也救不了的错误（请求本身该被拒绝）→ 协议错误，模型看不到"""
    raise MCPError(INVALID_PARAMS, "caller lacks the required scope 'admin'")


@mcp.tool()
def buggy() -> str:
    """演示崩溃：未预料的异常。模型只会看到 'Error executing tool buggy'，堆栈在 Server 日志"""
    return {}["missing"]  # KeyError


async def selftest() -> None:
    from mcp import Client

    tomorrow = date.fromordinal(date.today().toordinal() + 30).isoformat()
    base = {"user_id": "u_001", "flight_no": "CA1981", "date_str": tomorrow, "seats": 2, "idempotency_key": "k1"}
    async with Client(mcp) as client:
        r = await client.call_tool("book_flight", base)
        print("成功     :", r.is_error, r.structured_content)
        r2 = await client.call_tool("book_flight", base)
        print("幂等重放 :", r2.structured_content["booking_id"] == r.structured_content["booking_id"])
        for label, patch in [
            ("日期已过", {"date_str": "2020-01-01", "idempotency_key": "k2"}),
            ("普通用户超限", {"user_id": "u_002", "seats": 8, "idempotency_key": "k3"}),
            ("用户不存在", {"user_id": "u_999", "idempotency_key": "k4"}),
            ("pattern 不匹配", {"flight_no": "invalid", "idempotency_key": "k5"}),
        ]:
            r = await client.call_tool("book_flight", {**base, **patch})
            print(f"{label:9}: is_error={r.is_error} | {r.content[0].text.splitlines()[0][:70]}")
        r = await client.call_tool("buggy", {})
        print("崩溃     :", r.is_error, "|", r.content[0].text)
        try:
            await client.call_tool("admin_only", {})
        except MCPError as e:
            print("MCPError :", e.code, "|", e)  # 协议错误：call_tool 直接抛异常，没有 result


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        logging.disable(logging.CRITICAL)
        asyncio.run(selftest())
    else:
        mcp.run()
