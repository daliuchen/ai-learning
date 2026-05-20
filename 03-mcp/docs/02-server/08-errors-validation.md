# MCP Server 08：错误处理与参数校验

> **一句话**：MCP 错误分两类——**协议错误**（JSON-RPC error，模型看不到、不能自我纠正）和**工具执行错误**（result.isError=true，模型能看到、可自我修正）。区分好这两种 + 输入用 Pydantic 严格校验 = 90% 错误处理工作。

---

## 1. 两类错误的本质区别

| 维度 | 协议错误（Protocol Error） | 工具执行错误（Tool Execution Error） |
|------|---------------------------|--------------------------------------|
| 表现形式 | JSON-RPC `error` 字段 | Result `isError: true` |
| 错误码 | -32xxx | 无（在 content text 里） |
| 模型能否看见 | 多数 Host **不会**转给模型 | **会**转给模型 |
| 是否可自我纠正 | 几乎不可能 | 是 |
| 典型例子 | 工具名不存在、参数 schema 不匹配 | 业务规则违反、外部 API 失败、值越界 |

**判断标准**：错误信息**对 LLM 有用、能让它换种方式重试**吗？

- 有用 → ToolError / isError
- 没用（说明是 bug） → 让它变成协议错误

---

## 2. 标准 JSON-RPC 错误码

| Code | 名称 | MCP 里何时出现 |
|------|------|----------------|
| -32700 | Parse error | 收到了非 JSON / 损坏 JSON |
| -32600 | Invalid Request | 不是合法 JSON-RPC 2.0 / task 要求未满足 |
| -32601 | Method not found | 调用了 Server 不支持的 method |
| -32602 | Invalid params | 参数缺失 / 类型错 / task 取消时已终态 |
| -32603 | Internal error | Server 内部异常 |
| -32000..-32099 | Server-defined | Server 自定义业务错误 |

### MCP 应用层错误码（在 -32000..-32099 区段）

| Code | 含义 |
|------|------|
| -32002 | Resource not found |
| 其它 | 由 spec 后续约定 |

---

## 3. Server 端：输入校验

最常见的"参数错"——Pydantic 已经帮你做掉一大半：

```python
from typing import Literal
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("orders")

class OrderSearchInput(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    days: int = Field(default=7, ge=1, le=90)
    status: Literal["paid", "shipped", "refunded"] | None = None

@mcp.tool()
def search_orders(user_id: str, days: int = 7, status: str | None = None) -> list:
    """搜索订单"""
    # FastMCP 已经按签名做了 Pydantic 校验：
    # - user_id 不能为空字符串
    # - days 1-90
    # - status 必须是三个之一
    # 校验失败 → SDK 自动返回 -32602 协议错误
    return []
```

但 Pydantic 防御不了**业务规则**：

```python
from mcp.server.fastmcp.exceptions import ToolError
from datetime import date

@mcp.tool()
def book_flight(date_str: str, seats: int) -> dict:
    """订票"""
    try:
        flight_date = date.fromisoformat(date_str)
    except ValueError:
        # 业务校验失败：返回友好错误让 LLM 自我纠正
        raise ToolError(
            f"日期格式错误: '{date_str}'，请用 ISO 格式 YYYY-MM-DD，比如 2026-05-20"
        )

    if flight_date < date.today():
        raise ToolError(f"日期 {flight_date} 已过去，不能预订")

    if seats < 1 or seats > 9:
        raise ToolError(f"座位数必须在 1-9 之间，你给的是 {seats}")

    return {"booking_id": "abc"}
```

`ToolError` → FastMCP 自动转成 `result.isError = true` + 错误文本，LLM 能看见并重试。

---

## 4. Server 端：外部 API 失败

```python
import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP("external")

@mcp.tool()
async def fetch_user(user_id: str, ctx: Context) -> dict:
    app = ctx.request_context.lifespan_context
    try:
        r = await app.http.get(f"/users/{user_id}")
    except httpx.TimeoutException:
        # 超时是"可重试"错误，告诉 LLM 它可以再试一次
        raise ToolError(f"上游 API 超时，请稍后重试")
    except httpx.NetworkError:
        # 网络错误：让 LLM 知道是临时问题
        raise ToolError("网络异常，请稍后重试")

    if r.status_code == 404:
        raise ToolError(f"用户不存在: {user_id}")
    if r.status_code == 429:
        raise ToolError("被限流，请稍后再试")
    if r.status_code >= 500:
        # 5xx 是 Server 端 bug，**抛真异常**——Host 端会包成 -32603
        # 这样 LLM 不会一直重试一个失败的服务
        r.raise_for_status()

    return r.json()
```

**判断**：
- 4xx（输入错） → `ToolError`，告诉 LLM 改输入
- 408 / 429 / 503（临时问题） → `ToolError`，提示 LLM 稍后重试
- 5xx（服务问题） → 抛真异常 → 协议错误，让 Host 终止重试

---

## 5. Server 端：内部异常

未捕获异常 → SDK 自动包成 `-32603 Internal error`。但你应该**永远**主动 catch：

```python
import logging

log = logging.getLogger(__name__)

@mcp.tool()
async def risky_op(x: int) -> int:
    try:
        return some_business_logic(x)
    except KeyError as e:
        log.exception("missing key in result")
        raise ToolError(f"内部数据缺失: {e}，请联系管理员")
    except Exception as e:
        log.exception("unexpected error")
        # 不暴露内部堆栈给 LLM——只给安全的提示
        raise ToolError("发生了内部错误，已记录日志，请稍后重试")
```

**安全提醒**：错误消息**不要泄漏内部信息**（数据库 schema、文件路径、token 片段）。LLM 是不可信终端，Host 可能把它转给最终用户。

---

## 6. Resource 错误

Resource 错误用标准 JSON-RPC error，**不**用 isError：

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("docs")

@mcp.resource("docs://{name}")
def doc(name: str) -> str:
    path = SAFE_DOCS_DIR / name
    if not path.exists():
        # ✅ 让 SDK 包成 -32002
        raise FileNotFoundError(name)
    return path.read_text()
```

FastMCP 把 `FileNotFoundError` / `KeyError` 等标准 Python 异常映射到合适的 MCP 错误码。

---

## 7. 错误信息写作模板

LLM 读你的错误信息时是想"我该怎么改请求"。好的错误：

```
❌ 不好的：
"Invalid date"
"Validation failed"
"Error: 400"

✅ 好的：
"日期格式错误: '2026/5/20'，请用 ISO 格式 YYYY-MM-DD，例如 2026-05-20"
"days 必须在 1-90 之间，你给了 100"
"用户不存在: u_123（提示：用户 ID 应为 u_ 前缀 + 6 位数字）"
```

模板：**[问题][实际值] + [期望值或纠正方法]**。

---

## 8. Client 端：消费错误

```python
result = await session.call_tool("book_flight", {"date_str": "wrong"})

if result.isError:
    # 工具执行错误：拿到错误内容，决定是否让 LLM 重试
    error_text = result.content[0].text
    print(f"工具失败但可重试: {error_text}")
else:
    print(f"成功: {result.content[0].text}")
```

**如果是协议错误**（参数 schema 错、工具不存在），SDK 直接抛 `mcp.shared.exceptions.McpError`：

```python
from mcp.shared.exceptions import McpError

try:
    result = await session.call_tool("nonexistent_tool", {})
except McpError as e:
    print(f"协议错误: code={e.error.code}, msg={e.error.message}")
```

---

## 9. 综合 demo：带完整错误处理的订票 Server

```python
# demos/server/08_errors_booking.py
import asyncio
from datetime import date

from pydantic import BaseModel, Field
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP("booking")

FAKE_DB = {
    "u_001": {"name": "Alice", "tier": "vip"},
    "u_002": {"name": "Bob", "tier": "standard"},
}

class BookingResult(BaseModel):
    booking_id: str
    flight: str
    seats: int
    user_name: str


@mcp.tool()
async def book_flight(
    user_id: str = Field(min_length=1),
    flight_no: str = Field(pattern=r"^[A-Z]{2}\d{2,4}$"),
    date_str: str = Field(description="YYYY-MM-DD"),
    seats: int = Field(ge=1, le=9),
    ctx: Context = None,
) -> BookingResult:
    """订机票。

    会做：
    1. 日期格式 / 不能过去
    2. 用户存在性
    3. VIP / Standard 限座位数
    """
    # 1. 日期校验
    try:
        flight_date = date.fromisoformat(date_str)
    except ValueError:
        raise ToolError(
            f"日期格式错: '{date_str}'，期望 YYYY-MM-DD，例如 2026-12-25"
        )
    if flight_date < date.today():
        raise ToolError(f"日期 {flight_date} 已过去")

    # 2. 用户校验
    if user_id not in FAKE_DB:
        raise ToolError(
            f"用户不存在: {user_id}（提示：UID 应为 u_xxx 格式）"
        )
    user = FAKE_DB[user_id]

    # 3. 业务规则
    if user["tier"] == "standard" and seats > 4:
        raise ToolError(
            f"普通用户单次最多订 4 个座位，你的等级 'standard' 申请 {seats} 个"
        )

    await ctx.info(f"为 {user['name']} 订 {flight_no} 共 {seats} 座")
    await asyncio.sleep(0.5)  # 模拟下单

    return BookingResult(
        booking_id="bk_" + str(abs(hash(f"{user_id}{flight_no}{date_str}")))[:8],
        flight=flight_no,
        seats=seats,
        user_name=user["name"],
    )


if __name__ == "__main__":
    mcp.run()
```

在 Inspector 里试：

- `book_flight("u_001", "CA1981", "2026-12-25", 2)` → ✅ 成功
- `book_flight("u_001", "CA1981", "2020-01-01", 2)` → isError: 日期已过去
- `book_flight("u_002", "CA1981", "2026-12-25", 8)` → isError: 标准用户超限
- `book_flight("u_999", "CA1981", "2026-12-25", 2)` → isError: 用户不存在
- `book_flight("u_001", "invalid", "2026-12-25", 2)` → **协议错误**（Pydantic 拦下，pattern 不匹配）

LLM 看到这些错误能学到 → 自动改输入重试。

---

## 10. 重试策略与幂等性

重试是 Client / Host 的事，Server 写代码时要让重试**安全**：

| 工具类型 | 重试安全？ | 怎么做 |
|---------|----------|--------|
| 只读查询 | ✅ 默认安全 | 无需特殊处理 |
| 幂等写（PUT、UPSERT） | ✅ 安全 | annotations 加 `idempotentHint: true` |
| 非幂等写（CREATE、扣费） | ❌ 不能重试 | 接受 client-provided idempotency key |

```python
@mcp.tool(annotations={"idempotentHint": False, "destructiveHint": True})
def create_order(idempotency_key: str, amount: float) -> dict:
    """创建订单（不幂等，但接受 key）"""
    if existing := _check_existing(idempotency_key):
        return existing  # 重放保护
    order = _create(amount)
    _record(idempotency_key, order)
    return order
```

---

## 11. 调试错误的工具

- **Inspector 的 Console**：看完整 JSON-RPC，错误响应一眼看清
- **ctx.error(msg)**：让错误同时进日志通道（不只是返回里）
- **`structlog` 或 `loguru`** 输出到 stderr：本地 stdio Server 别 print

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **业务错误抛 ValueError** | 改成 ToolError，LLM 才能看见 |
| **错误信息泄露内部细节** | 错误消息打日志一份、给 LLM 简化一份 |
| **协议错误 / 工具错误用反了** | 模型错误用 isError；程序 bug 用协议错误 |
| **没区分可重试 / 不可重试** | 临时错误（429、超时）用 ToolError；永久错误（404）也 ToolError 但要说明 "不要重试" |
| **Pydantic 校验被绕开** | FastMCP 自动用类型注解 + Field 做校验；低层 Server 类要自己写 |
| **错误码不规范** | 内部错误用 -32603；自定义业务错码 -32000..-32099 |

---

## 13. 下一步

- 02-server 全部 8 篇结束。下一章进入 03-client。
- 📖 客户端怎么处理错误 → 03-client/01-client-basics
- 📖 安全：错误消息的攻击面 → 05-production/04-security

## 参考资料

- Tools spec - Error Handling：https://modelcontextprotocol.io/specification/2025-11-25/server/tools#error-handling
- Resources error codes：https://modelcontextprotocol.io/specification/2025-11-25/server/resources#error-handling
- JSON-RPC 2.0 Error Codes：https://www.jsonrpc.org/specification#error_object
