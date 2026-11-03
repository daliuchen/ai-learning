# MCP Server 08：错误处理与参数校验

> Python SDK 2.x 里，工具出错有三种去向：`ToolError` 让模型看到错误并自行纠正；`MCPError` 是协议错误，模型看不到；其它异常算崩溃，模型只知道失败了，堆栈留在 Server 日志里。再加上 schema 校验会在函数被调用之前拦下大部分坏参数。把这几条分清楚，错误处理的大部分工作就完成了。

---

## 1. 错误的三种去向

| 你做的事 | Client 收到什么 | 模型看到什么 | 服务端日志 |
|---------|-----------------|------------|-----------|
| `raise ToolError("...")` | 正常结果，`is_error=True`，消息在 `content` 里（带 `Error executing tool xxx:` 前缀） | 消息原文 | 一行 INFO，无堆栈 |
| `raise MCPError(code, message, data)` | JSON-RPC error，`call_tool` 直接抛出 `MCPError` | 没有结果可读，由 Host 处理 | 视情况而定 |
| 抛出其它任何异常（`KeyError` 等） | `is_error=True`，内容只有 `Error executing tool xxx` | 只知道失败了，不知道原因（内部细节不外泄） | ERROR + 完整堆栈 |
| 参数不符合 schema | SDK 在调用你的函数之前就拒绝，同样是 `is_error=True`，附 Pydantic 校验信息 | 能看到，可以纠正 | INFO |
| 资源不存在 | `-32602`，`data` 里带 URI | 不适用（资源没有 isError 这条路） | INFO |

判断用哪种，只要问一句：**更聪明的模型能避免这个错误吗？** 能，就用 `ToolError`；不能（缺权限、Server 状态不对、请求本身就该被拒绝），就用 `MCPError`。

> 在 v2 SDK 里，Pydantic 校验失败不会变成协议错误 -32602，坏参数得到的是 `is_error=True` 的工具结果，模型读得到，也能重试；工具里抛出真实异常也不会得到 -32603，模型只会收到 `Error executing tool xxx`。只有 `MCPError` 才是协议错误。v1 的 `McpError` 改名为 `MCPError`，导入路径是 `from mcp import MCPError`。

---

## 2. 标准 JSON-RPC 错误码

| Code | 名称 | 在 MCP 里何时出现 |
|------|------|----------------|
| -32700 | Parse error | 收到的不是 JSON，或 JSON 已损坏 |
| -32600 | Invalid Request | 不是合法的 JSON-RPC 2.0 |
| -32601 | Method not found | 调用了 Server 不支持的 method（包括 2026 版已移除的 `ping`；在 `MCPServer` 上调用不存在的工具名不算，那是 `is_error=True`） |
| -32602 | Invalid params | 参数缺失或类型错误；资源不存在也用它（2025 版是 -32002） |
| -32603 | Internal error | Server 内部异常（消息会被抹掉） |
| -32000..-32019 | 实现自定义 | 各 SDK 和 Server 自行使用（已有用法保留）；SDK 的请求超时是 -32001（v1 是 408） |
| -32020..-32099 | MCP 规范保留 | 2026-07-28 起划出的区段 |

2026-07-28 在保留段里定义的错误：

| Code | 含义 |
|------|------|
| -32020 | HeaderMismatch：HTTP 的 `Mcp-Method` / `Mcp-Name` 头与 body 不一致 |
| -32021 | MissingRequiredClientCapability：Server 需要的 Client 能力没有声明 |
| -32022 | UnsupportedProtocolVersion：`data.supported` 里列出支持的版本 |

这些常量在 `mcp.types` 里有定义，比如 `INVALID_PARAMS`、`INTERNAL_ERROR`、`METHOD_NOT_FOUND`，不要在代码里写魔法数字。

---

## 3. Server 端：输入校验

最常见的参数错误，Pydantic 在你的函数被调用之前就拦住了：

```python
from typing import Annotated, Literal
from pydantic import Field
from mcp.server import MCPServer

mcp = MCPServer("orders")

@mcp.tool()
def search_orders(
    user_id: Annotated[str, Field(min_length=1, max_length=64)],
    days: Annotated[int, Field(ge=1, le=90)] = 7,
    status: Literal["paid", "shipped", "refunded"] | None = None,
) -> list[dict]:
    """搜索订单"""
    # 能走到这里，说明：user_id 非空、days 在 1-90、status 是三个之一
    return []
```

校验失败时，模型收到的是类似 `Input should be less than or equal to 90` 的工具错误，它会自己改参数重试。类型注解已经保证的东西，就不要在函数里再校验一遍了。

Pydantic 管不了业务规则，业务规则用 `ToolError`：

```python
from datetime import date
from mcp.server.mcpserver.exceptions import ToolError

@mcp.tool()
def book_flight(date_str: str, seats: int) -> dict:
    """订票"""
    try:
        flight_date = date.fromisoformat(date_str)
    except ValueError:
        raise ToolError(f"日期格式错误: '{date_str}'，请用 ISO 格式 YYYY-MM-DD，比如 2026-12-25") from None
    if flight_date < date.today():
        raise ToolError(f"日期 {flight_date} 已过去，不能预订")
    return {"booking_id": "abc"}
```

> 低层 `Server` 类不会替你校验入参 schema（在 v2 的低层 API 里，schema 只是一个声明）。用低层 API 时要自己校验，而且异常不会自动变成 `is_error`，需要自己返回 `CallToolResult(is_error=True, ...)`。

---

## 4. Server 端：外部 API 失败

```python
import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("external")

@mcp.tool()
async def fetch_user(user_id: str, ctx: Context) -> dict:
    app = ctx.request_context.lifespan_context
    try:
        r = await app.http.get(f"/users/{user_id}")
    except httpx.TimeoutException:
        raise ToolError("上游 API 超时，可以稍后重试一次") from None
    except httpx.NetworkError:
        raise ToolError("网络异常，请稍后重试") from None

    if r.status_code == 404:
        raise ToolError(f"用户不存在: {user_id}")
    if r.status_code == 429:
        raise ToolError("被限流，请稍后再试")
    if r.status_code >= 500:
        # 上游 5xx：不是模型的错、也不是它能修的。让异常冒出去：
        # 模型只会看到 "Error executing tool fetch_user"，堆栈进你的 ERROR 日志，
        # 于是生产日志在 WARNING 级别能「平时安静、真出事才响」
        r.raise_for_status()
    return r.json()
```

（这里的 `httpx` 是你自己代码的依赖；官方 SDK 2.x 内部换成了 `httpx2`，两者可以共存。）

按状态码区分处理：4xx 是输入有问题，用 `ToolError` 告诉 LLM 去改输入；408、429 是临时问题，也用 `ToolError`，提示稍后重试；5xx 是上游服务出了问题，直接抛出真实异常，模型只知道失败了，原因你从日志里查（503 这类明确可以稍后重试的，也可以像 429 一样单独转成 `ToolError`）。

---

## 5. Server 端：内部异常

意料之外的异常由 SDK 自动处理：给模型返回 `Error executing tool xxx`，把堆栈记为 ERROR。SQL、文件路径、token 片段这些内部细节不会泄漏给模型，这是 v2 默认的安全做法。所以没必要为了防泄漏到处写 `except Exception`，只有想给模型一个有用的提示时，才主动转成 `ToolError`：

```python
import logging
log = logging.getLogger(__name__)

@mcp.tool()
async def risky_op(x: int) -> int:
    try:
        return some_business_logic(x)
    except KeyError as e:
        log.exception("missing key in result")
        raise ToolError("内部数据缺失，已记录日志，请换个输入再试") from None   # from None：别把原异常链带出去
```

> 写进 `ToolError` 的消息会原样交给模型，Host 还可能把它展示给最终用户，所以别把数据库 schema、文件路径、token 片段放进去。

---

## 6. Resource 错误

资源没有 `isError`，只有协议错误：

```python
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError

mcp = MCPServer("docs")

@mcp.resource("docs://{name}")
def doc(name: str) -> str:
    path = SAFE_DOCS_DIR / name
    if not path.is_file():
        raise ResourceNotFoundError(f"没有这篇文档: {name}")   # → -32602，data 里带 {"uri": "docs://..."}
    return path.read_text()
```

- `ResourceNotFoundError` 对应 `-32602`，`data.uri` 是请求的 URI
- `ResourceError` 对应 `-32603` 加上你的消息（属于预料之中的失败，记 INFO 日志，不带堆栈）
- 其它异常对应 `-32603`，只带 URI，堆栈写进 ERROR 日志
- 在 Client 侧，资源不存在在 v1 里是 `-32002`，现在统一为 `-32602`；写客户端的兜底逻辑时两个码都要处理

---

## 7. 错误信息怎么写

LLM 读错误信息，是为了知道下一次请求该怎么改。好的错误信息是这样的：

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

写法可以套一个模板：问题是什么、实际值是多少，再加上期望值或纠正方法。

---

## 8. Client 端：处理错误

```python
from mcp import Client, MCPError

result = await client.call_tool("book_flight", {...})
if result.is_error:
    # 工具执行错误（含 ToolError、崩溃、schema 校验失败）：消息在 content 里，交给模型决定是否重试
    print("工具失败:", result.content[0].text)
else:
    print("成功:", result.structured_content or result.content[0].text)
```

协议错误（Server 手动抛出的 `MCPError`、版本不支持、方法不存在、请求超时等）会让 `call_tool` 直接抛出 `MCPError`，不会有 result：

```python
try:
    await client.call_tool("admin_only", {})
except MCPError as e:
    print(f"协议错误: code={e.code}, msg={e}")     # v2：e.code；v1 是 e.error.code
```

> 调用 `MCPServer` 上不存在的工具并不会抛异常，而是返回 `is_error=True` 和 `Unknown tool: xxx`。只有 Server 回的是 JSON-RPC error 时，`Client` 才会抛 `MCPError`。

---

## 9. 综合 demo：带完整错误处理的订票 Server

```python
# demos/server/08_errors_booking.py
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
```

进程内自测（`python demos/server/08_errors_booking.py --selftest`，实际输出）：

```
成功     : False {'booking_id': 'bk_e9b8237e', 'flight': 'CA1981', 'seats': 2, 'user_name': 'Alice'}
幂等重放 : True
日期已过     : is_error=True | Error executing tool book_flight: 日期 2020-01-01 已过去，不能预订
普通用户超限   : is_error=True | Error executing tool book_flight: 普通用户单次最多订 4 个座位，你申请了 8 个
用户不存在    : is_error=True | Error executing tool book_flight: 用户不存在: u_999（提示：UID 应为 u_ + 3 位数字）
pattern 不匹配: is_error=True | Error executing tool book_flight: 1 validation error for book_flightAr
崩溃     : True | Error executing tool buggy
MCPError : -32602 | caller lacks the required scope 'admin'
```

从这段输出可以总结出规则：除了 `MCPError`，其它所有失败都是「调用成功，`is_error=True`」，区别只在模型读到的内容。`ToolError` 和 schema 校验给的是原文，崩溃只给一句 `Error executing tool xxx`。

---

## 10. 重试与幂等

重试由 Client 或 Host 负责，Server 写代码时要保证重试是安全的：

| 工具类型 | 重试安全吗 | 怎么做 |
|---------|----------|--------|
| 只读查询 | 默认安全 | `ToolAnnotations(read_only_hint=True)` |
| 幂等写（PUT、UPSERT） | 安全 | `idempotent_hint=True` |
| 非幂等写（CREATE、扣费） | 不能盲目重试 | 接受客户端提供的幂等键（比如上面 demo 里的 `idempotency_key`） |

2026-07-28 又多了一个必须做幂等的理由：Streamable HTTP 不再支持断点续传，响应流一断，这个请求就丢了，Client 必须用新的请求 ID 重发。写操作如果没有幂等键，就可能被执行两次。

---

## 11. 调试错误的工具

- Inspector 的 Console 和消息历史：能看到完整的 JSON-RPC，错误响应一目了然
- 标准库 `logging` 输出到 stderr（`ctx.error()` 等协议层日志已弃用）：服务端的堆栈都在这里，`ToolError` 是 INFO，崩溃是 ERROR
- `structlog` 或 `loguru` 输出到 stderr：本地 stdio Server 不要用 `print`
- pytest 加 `Client(mcp)`：断言 `result.is_error` 和文本内容，崩溃堆栈用 `caplog` 抓

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| 业务错误抛了 ValueError | 改成 `ToolError`，否则模型只能看到 `Error executing tool xxx` |
| 想让模型读到错误，却 `raise MCPError` | `MCPError` 是协议错误，模型看不到，改用 `ToolError` |
| `return` 了一条错误字符串 | 结果是 `is_error=False`，所有客户端都会认为调用成功、这就是答案；一定要用 `raise` |
| 错误信息泄露内部细节 | `ToolError` 的文本会原样交给模型和最终用户；细节写进日志，给模型的版本要简化 |
| 没区分能不能重试 | 临时错误（429、超时）在消息里写明可以稍后重试，永久错误写明不要重试 |
| 在函数里重复做类型校验 | 类型注解加 `Field` 已经在调用前校验过了，函数里只写业务规则 |
| Pydantic 校验被绕过 | 低层 `Server` 不校验入参，要自己写 |
| 客户端只认 -32002 | 在 2026-07-28 里，资源不存在是 -32602 |

---

## 13. 下一步

Server 部分的 8 篇到这里结束，下一章讲 Client，从[自己写 Client（连接、发现与调用工具）](../03-client/01-client-basics.md)开始。

- 客户端怎么处理错误：[自己写 Client（连接、发现与调用工具）](../03-client/01-client-basics.md)
- 错误消息带来的安全风险：[防范 Prompt 注入、Tool 投毒与路径越权](../05-production/04-security.md)

## 参考资料

- Tools spec - Error Handling：https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- 错误码分配策略：https://modelcontextprotocol.io/specification/2026-07-28/basic/index#error-codes
- Python SDK Handling errors：https://py.sdk.modelcontextprotocol.io/servers/handling-errors/
