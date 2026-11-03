# MCP Server 01：用 Tools 暴露可调用的操作

> Tool 是 MCP Server 暴露给模型调用的动作。这篇把 Tool 的各个方面过一遍：参数 schema、返回 content 与 structuredContent、annotations、错误处理，以及安全上要守的约定。

---

## 1. 最小可用代码

```python
from mcp.server import MCPServer

mcp = MCPServer("demo")

@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b
```

MCPServer 会自动完成这些事：

1. 从函数签名生成 `inputSchema`（JSON Schema 2020-12，Python 里对应 `Tool.input_schema`）
2. 用整段 docstring 生成 `description`（`Args:` 段不会被拆成参数说明）
3. 注册到 `tools/list` 的响应里
4. 自动声明 `capabilities.tools`（在 `server/discover` 里；对老客户端则在 `initialize` 里）
5. 处理 `tools/call` 请求：把 JSON 参数反序列化成 Python 类型，调用函数，再把返回值打包成 content

---

## 2. inputSchema 细节

MCPServer 内部用 Pydantic 把函数签名转成 JSON Schema，所以 Pydantic 支持的类型都能用：

```python
from datetime import date
from typing import Annotated, Literal
from pydantic import BaseModel, Field
from mcp.server import MCPServer

mcp = MCPServer("demo")

class Address(BaseModel):
    street: str
    city: str
    country: str = "China"

@mcp.tool()
def order_search(
    user_id: Annotated[str, Field(description="用户 ID（必填）", min_length=1)],
    days: Annotated[int, Field(ge=1, le=90, description="窗口天数 1-90")] = 7,
    status: Literal["paid", "shipped", "refunded"] | None = None,
    shipping: Address | None = None,
    tags: list[str] = Field(default_factory=list),
) -> list[dict]:
    """查询某用户的订单。"""
    return []
```

生成的 JSON Schema（节选）：

```json
{
  "type": "object",
  "properties": {
    "user_id": {"type": "string", "description": "用户 ID（必填）", "minLength": 1},
    "days": {"type": "integer", "default": 7, "minimum": 1, "maximum": 90},
    "status": {"anyOf": [{"enum": ["paid","shipped","refunded"]}, {"type":"null"}]},
    "shipping": {"$ref": "#/$defs/Address"},
    "tags": {"type": "array", "items": {"type": "string"}}
  },
  "required": ["user_id"],
  "$defs": {"Address": {...}}
}
```

> 上面是节选，实际生成的 schema 里还带着 Pydantic 默认的 `title`（如 `"title": "User Id"`、根上的 `"title": "order_searchArguments"`）。schema 里没有 `$schema` 键：MCP 默认把不带 `$schema` 的 schema 当作 JSON Schema 2020-12，2026-07-28 规范还允许 `inputSchema`/`outputSchema` 使用任意 2020-12 关键字。

写参数时的几条约定：
- 推荐用 `Annotated[类型, Field(...)]` 写参数约束（直接写成 `= Field(...)` 默认值也可以）
- `Field(description=...)` 是参数级的 description，LLM 选用工具时主要看它
- `Field(default=..., ge=..., le=...)` 设默认值和数值约束
- `Literal[...]` 生成 enum
- 嵌套 `BaseModel` 生成 JSON Schema 的 `$defs`

---

## 3. 命名与 title

```python
@mcp.tool(name="github_create_issue", title="创建 GitHub Issue")
def create_issue(repo: str, title: str, body: str = "") -> dict:
    """在指定 repo 创建 issue。⚠️ 这是写操作"""
    return {"number": 42, "url": "..."}
```

- `name` 是协议层的标识，LLM 在 tool_use 里报的就是这个名字。规范要求 1–128 个字符，只能是 ASCII 字母数字和 `_-.`，不能有空格
- `title` 是 UI 上给人看的名字，可以写中文
- 不指定时，MCPServer 用函数名

命名时建议加前缀，比如 `github_*`、`db_*`、`fs_*`，避免和其他 Server 冲突。

---

## 4. 返回 content

工具的返回值通过 `result.content`（数组）传回。Python 函数返回不同类型时，MCPServer 会自动转换：

| Python 返回 | 转成 |
|------------|------|
| `str` | `TextContent(text=...)` |
| `int` / `float` / `bool` | `TextContent(text=str(...))`；声明了返回类型时，另外附带 `structuredContent={"result": ...}` |
| `dict` / `list` | `TextContent(text=json.dumps(...))` |
| `BaseModel` / `list[BaseModel]` 等有类型注解的 | 文本 JSON + `structuredContent` + 自动生成的 `outputSchema` |
| `Image(data=..., format="png")` / `Audio(...)`（`mcp.server.mcpserver`） | `ImageContent` / `AudioContent` |
| `mcp.types.TextContent` / `ImageContent` 等 | 原样返回 |
| `list[XxxContent]` | 多个 content 并存 |
| `CallToolResult(content=..., is_error=...)` | 完全自己控制结果（包括「软失败」） |

### 4.1 多个 content（文字加图片）

```python
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import TextContent

mcp = MCPServer("demo")

@mcp.tool()
def render_pie_chart(query: str) -> list:
    """渲染饼图。返回文字描述 + PNG"""
    png_bytes = b"<binary png data>"
    return [
        TextContent(type="text", text=f"查询: {query}, 总数: 100"),
        Image(data=png_bytes, format="png"),   # SDK 会转成 ImageContent（base64）
    ]
```

### 4.2 Resource Link（返回资源引用而不是内容）

```python
from mcp.types import ResourceLink

@mcp.tool()
def find_design_file(name: str) -> list:
    """查找设计稿。返回 Resource 链接，让 Host 按需下载"""
    return [
        ResourceLink(
            type="resource_link",
            uri=f"figma://files/{name}",
            name=name,
            description="Figma 设计稿",
            mime_type="application/figma",
        )
    ]
```

这样大文件不会被塞进对话上下文，什么时候拉取由 Host 决定。

---

## 5. structuredContent 与 outputSchema

`2025-06-18` 规范引入了 `outputSchema` 和 `structuredContent`，工具返回的 JSON 也能做静态校验了；2026-07-28 又进一步放宽，`structuredContent` 可以是任意 JSON 值，`outputSchema` 可以用任意 2020-12 关键字。返回结构化数据的工具，我建议都用上。

```python
from pydantic import BaseModel
from mcp.server import MCPServer

mcp = MCPServer("weather-server")

class WeatherInfo(BaseModel):
    temperature: float
    conditions: str
    humidity: float

@mcp.tool()
def get_weather(location: str) -> WeatherInfo:
    """查询城市天气"""
    return WeatherInfo(temperature=22.5, conditions="Partly cloudy", humidity=65)
```

MCPServer 看到返回类型是 `BaseModel`，会自动：

- 生成 `outputSchema`（来自 `WeatherInfo.model_json_schema()`）
- 在响应里同时放 `content`（为了向后兼容的文本 JSON）和 `structuredContent`（结构化对象）

响应如下（2026-07-28 的结果多了必填的 `resultType`）：

```json
{
  "resultType": "complete",
  "content": [
    {"type": "text", "text": "{\n  \"temperature\": 22.5,\n  \"conditions\": \"Partly cloudy\",\n  \"humidity\": 65.0\n}"}
  ],
  "structuredContent": {
    "temperature": 22.5,
    "conditions": "Partly cloudy",
    "humidity": 65.0
  },
  "isError": false
}
```

Client 端可以直接拿到有类型的对象（Python 里字段是 snake_case）：

```python
result = await client.call_tool("get_weather", {"location": "Beijing"})
print(result.structured_content)  # {"temperature": 22.5, ...}
```

> 返回 `int` / `str` / `list[...]` 这类非对象类型时，SDK 会把它包成 `{"result": ...}`，因为 `outputSchema` 的根必须是 object。返回不带类型参数的 `dict` 时，只有文本 content，没有 structuredContent。

---

## 6. 错误处理：三种结果，含义各不相同

Tool 设计里最容易出错的就是这一块。v2 SDK 的规则很明确，只问一个问题：**更聪明的模型能避免这个错误吗？** 能，就让模型看到（`ToolError`）；不能，就是协议错误（`MCPError`）。

| 你做的事 | Client 收到 | 模型看到 |
|---------|------------|---------|
| `raise ToolError("...")` | 正常的结果，`is_error=True`，文本在 `content` 里（带 `Error executing tool xxx:` 前缀） | 能看到消息，可以自己纠正后重试 |
| `raise MCPError(code, message)`（`from mcp import MCPError`） | JSON-RPC error（整个 `tools/call` 失败） | 看不到，由 Host 处理 |
| 抛出其它任何异常（`KeyError`、`ValueError` 等） | `is_error=True`，内容只有 `Error executing tool xxx` | 只知道失败了，不知道原因；堆栈只在 Server 日志里（ERROR 级） |
| 参数不符合 schema | SDK 在调用你的函数之前就拒绝，同样是 `is_error=True` 加校验信息 | 能看到，可以纠正 |

```python
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("calc")

@mcp.tool()
def divide(a: float, b: float) -> float:
    """两数相除"""
    if b == 0:
        # ✅ ToolError：模型能读到这句话并改参数重试
        raise ToolError("除数不能为 0，请提供非零值。")
    return a / b
```

几点说明：

- 不要用 `return` 返回错误信息。返回的字符串 `is_error=False`，模型和所有客户端 UI 都会认为工具成功了、这就是答案。要用 `raise`。
- `ToolError` 在服务端只记一行 INFO 日志，意料之外的异常才记带堆栈的 ERROR。这样生产环境把日志级别设成 WARNING，平时很安静，真出问题才有输出。
- 想精确控制结果（比如带结构化内容的「软失败」），可以直接返回 `CallToolResult(content=[...], is_error=True)`。
- `MCPError` 用在请求本身就该被拒绝的场合：缺少必需的 Client 能力、Server 状态不对、调用方跳过了必要步骤。错误码常量在 `mcp.types` 里：`INVALID_PARAMS`（-32602）、`INTERNAL_ERROR` 等。
- v1 到 v2 有行为变化：v1 里在工具中 `raise McpError` 会被转成 `isError`；v2 里 `MCPError` 是协议错误，模型看不到。v1 的 `McpError` 也改名成了 `MCPError`。

> v1 文档里常见的 `return {"_error": True, "content": [...]}` 并不是合法的错误返回方式，不要照抄。

---

## 7. Tool Annotations：给 Host 的使用提示

Server 可以通过 annotations 告诉 Host 一个工具有什么特性，Host 据此决定要不要弹审批框。

| Annotation（JSON 里 camelCase / Python 里 snake_case） | 含义 | 默认 |
|------------|------|------|
| `readOnlyHint` / `read_only_hint` | 只读、无副作用 | false |
| `destructiveHint` / `destructive_hint` | 破坏性（删除等），只对非只读工具有意义 | true |
| `idempotentHint` / `idempotent_hint` | 多次调用结果相同，只对非只读工具有意义 | false |
| `openWorldHint` / `open_world_hint` | 与外部世界交互（API） | true |

给人看的名字用 `@mcp.tool(title="搜索代码")`（也兼容 `annotations.title`）。

```python
from mcp.types import ToolAnnotations

@mcp.tool(
    title="搜索代码",
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def search_code(pattern: str, path: str = ".") -> list[str]:
    """在指定路径下用 ripgrep 搜代码"""
    return []
```

> 安全上要注意：来自不受信任 Server 的 annotations，Client 必须当作不可信。一个工具有没有副作用，要看它实际做了什么，不能只看 annotations。

---

## 8. 长时间运行的工具

工具要跑很久时（爬虫、视频转码、长文本生成），用 progress 通知汇报进度：

```python
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

mcp = MCPServer("scraper")

@mcp.tool()
async def scrape_site(url: str, ctx: Context) -> str:
    """爬整个网站"""
    pages = await discover_pages(url)

    for i, page in enumerate(pages):
        await fetch(page)
        # 上报进度：progress 必须单调递增；total、message 可选
        await ctx.report_progress(i + 1, total=len(pages), message=f"已抓 {page}")

    return f"已爬取 {len(pages)} 页"
```

Client 端在每次调用时传回调（`call_tool(..., progress_callback=...)`）。没传的话 `report_progress` 什么都不做，所以 Server 端可以无条件上报。详见[日志、进度、取消与 Ping](06-logging-progress-ping.md)。

如果是「先提交、过一会儿再来取结果」的异步长任务，在 2026-07-28 里属于 Tasks 扩展，见[用 Tasks 扩展异步执行长请求](07-tasks.md)。

---

## 9. 注入上下文（Context 对象）

给工具加一个类型标注为 `Context` 的参数（参数名随意，SDK 靠类型注解识别），SDK 就会把它注入进来。这个参数不会出现在 `inputSchema` 里，模型看不到。它提供：

- `ctx.report_progress(...)`：进度
- `ctx.request_id`：当前请求 ID
- `ctx.read_resource(uri)`：读本 Server 的 Resource
- `ctx.headers`：HTTP 请求头（stdio 下为 `None`；这是客户端可控的输入，别拿来当身份）
- `ctx.request_context.lifespan_context`：启动时 `lifespan` 产出的对象（见[用 Lifespan 和 Context 向工具注入共享资源](04-lifespan-context.md)）
- `ctx.session`：底层会话，用来发 list_changed 等通知
- `ctx.notify_tools_changed()` / `ctx.notify_resource_updated(uri)`：通知 2026 版 `subscriptions/listen` 的订阅者（见[用 Resources 提供上下文数据](02-resources.md)）
- `ctx.elicit(...)`：在 2025 版连接上反过来问用户；2026 版连接请改用 `Resolve(...)`（见[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)）
- ~~`ctx.info()` / `ctx.warning()` / `ctx.error()`~~：协议层日志，已弃用（SEP-2577），请改用 Python 标准库 `logging`（见[日志、进度、取消与 Ping](06-logging-progress-ping.md)）
- ~~`ctx.sample(...)`~~：Sampling 已弃用（见[Sampling 让 Server 借用 Host 的 LLM（已弃用）](../03-client/03-sampling.md)）

> 从 v1 迁移：`MCPServer.get_context()` 已移除，统一改成声明一个 `ctx: Context` 参数；`ctx.fastmcp` 改名为 `ctx.mcp_server`。注入只对你注册的那个函数生效，下游的辅助函数要把 `ctx` 当普通参数传下去。

同步函数（`def`）在 v2 里跑在工作线程上，不会阻塞事件循环；`async def` 不受影响。

---

## 10. 低层 API（`Server` 类）

`MCPServer` 覆盖不到的场景，可以用低层的 `Server`。v2 重写了低层 API：不再用 `@app.list_tools()` 这样的装饰器，而是把 `on_*` 处理函数传给构造函数。处理函数统一是 `async (ctx, params) -> 完整 Result` 的形式，SDK 不再替你包装返回值，也不再校验入参 schema。

```python
# demos/server/01_tools_lowlevel.py
from mcp import MCPError
from mcp.server import Server, ServerRequestContext
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

ADD = Tool(
    name="add",
    description="两数相加",
    input_schema={  # 低层 API 里这只是「声明」，调用前不会被校验！
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
)


async def list_tools(ctx: ServerRequestContext, params: PaginatedRequestParams | None) -> ListToolsResult:
    return ListToolsResult(tools=[ADD])  # 必须自己构造完整的 ListToolsResult


async def call_tool(ctx: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
    if params.name != "add":
        raise MCPError(INVALID_PARAMS, f"Unknown tool: {params.name}")  # 协议错误，带原 code
    args = params.arguments or {}  # 可能为 None，也没校验过
    try:
        total = int(args["a"]) + int(args["b"])
    except (KeyError, ValueError, TypeError):
        # 想让模型读到的错误：自己返回 is_error=True（抛普通异常只会变成 -32603）
        return CallToolResult(content=[TextContent(type="text", text="需要两个整数 a 和 b")], is_error=True)
    return CallToolResult(content=[TextContent(type="text", text=str(total))])


server = Server("low-level-demo", on_list_tools=list_tools, on_call_tool=call_tool)
app = server.streamable_http_app()  # 普通 ASGI app，可交给 uvicorn

# 跑：uvicorn 模块名:app --port 8000；测试时可 `async with Client(server)` 进程内直连
```

v1 到 v2 的主要变化（只影响低层 API，`MCPServer` 用户不受影响）：

- 处理函数从装饰器改成构造参数 `on_list_tools=` / `on_call_tool=` 等
- 类型字段全部是 snake_case（`input_schema`、`is_error`）
- 返回值不再自动包装，必须返回完整的 `ListToolsResult` / `CallToolResult`
- 不再用 jsonschema 校验工具入参。声明了 schema 也不会在调用前检查，要在处理函数里自己校验
- 异常会变成协议错误（`-32603`，消息被抹掉），不再自动转成 `is_error=True`；想让模型读到错误，就自己返回 `CallToolResult(is_error=True, ...)`
- 请求上下文是处理函数的第一个参数，`server.request_context` 这个 ContextVar 没了
- 新增 `middleware` 列表（标注为 provisional）

需要完全控制 schema 生成（手写复杂 schema、动态注册工具、跨语言生成等），或者要加带厂商前缀的自定义方法（`add_request_handler`）时，才用低层 API。绝大多数场景用 `MCPServer` 就够了。

---

## 11. 跨 Server 重名

两个 Server 都暴露了 `search`，Host 怎么处理？Claude Code 给所有 MCP 工具都加命名空间，变成 `mcp__<server-name>__search`；有些客户端则在 UI 上让用户选用哪一个。

Server 端最好自己加前缀（`github_search`、`db_search`），不要依赖 Client 去处理。

---

## 12. 完整 demo：weather server

下面这个 demo 把 schema、structuredContent、annotations、错误处理和进度通知都用上了：

```python
# demos/server/01_tools_weather.py（节选；完整版见 demos/server/，配套 01_tools_weather_client.py）
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
```

运行并连上 Inspector：

```bash
npx @modelcontextprotocol/inspector python demos/server/01_tools_weather.py
# 或者直接用配套的 Python Client：
python demos/server/01_tools_weather_client.py
```

在 Inspector 里可以试：
- `get_forecast(city="Atlantis")`：返回 `isError: true` 和一条可读的错误信息
- `get_forecast(city="Beijing", days=3)`：返回 3 条 structuredContent，以及 progress 通知

配套 Client 的实际输出（节选）：

```
== 正常调用（带进度）==
   [progress] 1.0/2.0 第 1 天
   [progress] 2.0/2.0 第 2 天
is_error: False
structured_content: {'result': [{'city': 'Beijing', 'day': 1, 'temperature_c': 20.0, ...}, ...]}

== ToolError：is_error=True，消息给模型 ==
is_error: True | Error executing tool get_forecast: 未知城市: Atlantis（提示：尝试 'Beijing' / 'Tokyo' / 'NYC'）
```

---

## 13. 常见坑

| 坑 | 排查 |
|----|------|
| LLM 总是不调你的工具 | description 写得不够清楚，可以参考 GitHub MCP、Sentry MCP 的工具描述 |
| 参数类型错 | 类型注解一定要写。`def f(x)` 没有注解时 MCPServer 不报错，但会把 `x` 声明成 `string`、不做任何类型转换 |
| 返回的大对象 LLM 看不懂 | 用 `outputSchema` + `BaseModel`，让 Client 拿到有类型的对象 |
| 写操作没标 destructive | 用 annotations 标出来，让 Host 弹确认 |
| 用 `raise ValueError` 报业务错误 | 业务错误用 `ToolError`，LLM 能据此纠正；其它异常模型只会看到 `Error executing tool xxx` |
| 用 `print()` 调试 | 用标准库 `logging`（写 stderr）。`ctx.info()` 等协议层日志已弃用 |
| 在 `MCPServer` 工具里 `raise MCPError`，想让模型看到 | v2 里 `MCPError` 是协议错误，模型看不到；要让模型读到请用 `ToolError` |
| 不同 Server 有同名工具 | Server 端加前缀，别指望 Client 自动加命名空间 |

---

## 14. 下一步

- Resources 设计：[用 Resources 提供上下文数据](./02-resources.md)
- 错误处理细节：[错误处理与参数校验](./08-errors-validation.md)
- 进度通知、取消与日志：[日志、进度、取消与 Ping](./06-logging-progress-ping.md)
- 让 Tool 中途问用户：[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)

## 参考资料

- Tools spec：https://modelcontextprotocol.io/specification/2026-07-28/server/tools
- Python SDK Tools：https://py.sdk.modelcontextprotocol.io/servers/tools/
- Python SDK Handling errors：https://py.sdk.modelcontextprotocol.io/servers/handling-errors/
- MCPServer 源码：https://github.com/modelcontextprotocol/python-sdk/tree/main/src/mcp/server/mcpserver
- JSON Schema 2020-12：https://json-schema.org/draft/2020-12
