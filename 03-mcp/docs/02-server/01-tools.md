# MCP Server 01：Tools —— 让模型动手干活

> **一句话**：Tool 是 MCP Server 暴露给模型调用的"动词"。这一篇把 Tool 的完整面貌——参数 schema、返回 content / structuredContent、annotations、错误处理、安全约定——一次性讲透。

---

## 1. 最小可用代码

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")

@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b
```

FastMCP 自动做的事：

1. 从函数签名生成 `inputSchema`（JSON Schema 2020-12）
2. 从 docstring 第一行（或全文）生成 `description`
3. 注册到 `tools/list` 响应
4. 在 `initialize` 阶段声明 `capabilities.tools.listChanged: true`
5. 处理 `tools/call` 请求，把 JSON 参数反序列化成 Python 类型、调用函数、把返回值打包成 content

---

## 2. inputSchema 细节

FastMCP 内部用 Pydantic 把函数签名转 JSON Schema。**所有 Pydantic 支持的类型都能用**：

```python
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")

class Address(BaseModel):
    street: str
    city: str
    country: str = "China"

@mcp.tool()
def order_search(
    user_id: str = Field(description="用户 ID（必填）", min_length=1),
    days: int = Field(default=7, ge=1, le=90, description="窗口天数 1-90"),
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

**约定**：
- `Field(description=...)` → 参数级 description（**LLM 选用工具时主要靠它**）
- `Field(default=..., ge=..., le=...)` → 默认值 + 数值约束
- `Literal[...]` → enum
- `BaseModel` 嵌套 → JSON Schema `$defs`

---

## 3. 命名与 title

```python
@mcp.tool(name="github_create_issue", title="创建 GitHub Issue")
def create_issue(repo: str, title: str, body: str = "") -> dict:
    """在指定 repo 创建 issue。⚠️ 这是写操作"""
    return {"number": 42, "url": "..."}
```

- `name`：协议级标识，**LLM 在 tool_use 里就报这个名字**。规范要求 1-128 字符、ASCII 字母数字 + `_-.`，不允许空格
- `title`：UI 上展示给人的名字（中文可以）
- 没指定时 FastMCP 用函数名

**命名规范建议**：带 prefix 避免跨 Server 冲突，如 `github_*`、`db_*`、`fs_*`。

---

## 4. 返回结构化 content

工具的返回值通过 `result.content`（数组）传回。Python 函数返回不同类型时 FastMCP 自动转：

| Python 返回 | 转成 |
|------------|------|
| `str` | `TextContent(text=...)` |
| `int` / `float` / `bool` | `TextContent(text=str(...))` |
| `dict` / `list` | `TextContent(text=json.dumps(...))` |
| `bytes` | `ImageContent`（需配 `Image()` 包装） |
| `mcp.types.TextContent` / `ImageContent` / 等 | 原样返回 |
| `list[XxxContent]` | 多个 content 并存 |

### 4.1 多 content（文字 + 图）

```python
from mcp.server.fastmcp import FastMCP, Image
from mcp.types import TextContent

mcp = FastMCP("demo")

@mcp.tool()
def render_pie_chart(query: str) -> list:
    """渲染饼图。返回文字描述 + PNG"""
    png_bytes = b"<binary png data>"
    return [
        TextContent(type="text", text=f"查询: {query}, 总数: 100"),
        Image(data=png_bytes, format="png").to_image_content(),
    ]
```

### 4.2 Resource Link（返回资源引用而非内容）

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
            mimeType="application/figma",
        )
    ]
```

好处：不把大文件塞进对话上下文，让 Host 决定何时拉。

---

## 5. structuredContent + outputSchema（**强类型工具**）

`2025-03-26` 规范引入了 `outputSchema` + `structuredContent`，让工具返回的 JSON 也能被静态校验。**强烈建议**所有返回结构化数据的工具都用上。

```python
from pydantic import BaseModel
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather-server")

class WeatherInfo(BaseModel):
    temperature: float
    conditions: str
    humidity: float

@mcp.tool()
def get_weather(location: str) -> WeatherInfo:
    """查询城市天气"""
    return WeatherInfo(temperature=22.5, conditions="Partly cloudy", humidity=65)
```

FastMCP 看到返回类型是 `BaseModel`，会自动：

- 生成 `outputSchema`（来自 `WeatherInfo.model_json_schema()`）
- 在响应里既塞 `content`（向后兼容的 text JSON）又塞 `structuredContent`（结构化对象）

响应长这样：

```json
{
  "result": {
    "content": [
      {"type": "text", "text": "{\"temperature\":22.5,\"conditions\":\"Partly cloudy\",\"humidity\":65}"}
    ],
    "structuredContent": {
      "temperature": 22.5,
      "conditions": "Partly cloudy",
      "humidity": 65
    }
  }
}
```

Client 端能直接拿到 typed object：

```python
result = await session.call_tool("get_weather", {"location": "Beijing"})
print(result.structuredContent)  # {"temperature": 22.5, ...}
```

---

## 6. 错误处理：两种错误，含义完全不同

这是 Tool 设计最容易踩坑的地方。MCP 把错误分两类：

### 6.1 协议错误（Protocol Error）
**JSON-RPC error 字段**，错误码 `-32xxx`。用于：

- 工具名不存在
- 请求格式错（参数缺失、类型错）
- Server 内部异常

```python
@mcp.tool()
def divide(a: float, b: float) -> float:
    """两数相除"""
    if b == 0:
        # ❌ 错误：raise 普通异常会变成协议错误
        raise ValueError("除数为 0")
    return a / b
```

抛 Python 异常 → SDK 包成协议错误 → LLM **看不到错误细节**（多数 Host 不会把协议错误转给 LLM）。

### 6.2 工具执行错误（Tool Execution Error）
**响应 `result.isError = true`**。用于：

- 业务逻辑失败（参数虽合法但执行不通）
- 外部 API 错误
- 输入验证失败（值越界等）

```python
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

mcp = FastMCP("calc")

@mcp.tool()
def divide(a: float, b: float) -> list:
    """两数相除"""
    if b == 0:
        # ✅ 正确：返回 isError，LLM 能看到、能自我纠正
        return {
            "_error": True,
            "content": [TextContent(type="text", text="除数不能为 0，请提供非零值。")],
        }
    return a / b
```

FastMCP 推荐写法（更简洁）：

```python
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("calc")

@mcp.tool()
def divide(a: float, b: float, ctx: Context) -> float:
    if b == 0:
        # 直接 raise ToolError，SDK 会包成 isError=true
        from mcp.server.fastmcp.exceptions import ToolError
        raise ToolError("除数不能为 0，请提供非零值。")
    return a / b
```

**判断标准**：错误信息对 LLM 有帮助、希望 LLM 看到并重试 → ToolError；属于程序 bug 或攻击 → 普通异常。

---

## 7. Tool Annotations：给 Host 的"使用提示"

Annotations 让 Server 告诉 Host "这个工具的脾性"，Host 可以据此决定弹不弹审批框。

| Annotation | 含义 | 默认 |
|------------|------|------|
| `title` | 人类可读名 | 函数名 |
| `readOnlyHint` | 只读、无副作用 | false |
| `destructiveHint` | 破坏性（删除等） | true |
| `idempotentHint` | 多次调用结果相同 | false |
| `openWorldHint` | 与外部世界交互（API） | true |

```python
@mcp.tool(
    annotations={
        "title": "搜索代码",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
def search_code(pattern: str, path: str = ".") -> list[str]:
    """在指定路径下用 ripgrep 搜代码"""
    return []
```

**⚠️ 安全注意**：Client **必须**把 untrusted Server 的 annotations 视为不可信。**真正的副作用判断要看实际行为**，不能仅靠 annotations。

---

## 8. 长时间运行的工具

如果工具要跑很久（爬虫、视频转码、长文本生成），用 progress notification：

```python
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("scraper")

@mcp.tool()
async def scrape_site(url: str, ctx: Context) -> str:
    """爬整个网站"""
    pages = await discover_pages(url)
    await ctx.info(f"发现 {len(pages)} 个页面")

    for i, page in enumerate(pages):
        await fetch(page)
        # 上报进度，progress: 当前进度，total: 总数
        await ctx.report_progress(progress=i + 1, total=len(pages))

    return f"已爬取 {len(pages)} 页"
```

Client 端能拿到进度通知更新 UI / 重置超时。详见 06-logging-progress-ping。

---

## 9. 上下文注入（Context 对象）

`ctx: Context` 是 FastMCP 自动注入的，提供：

- `ctx.info(msg)` / `ctx.warning(msg)` / `ctx.error(msg)` — 发日志通知
- `ctx.report_progress(...)` — 进度
- `ctx.sample(...)` — 反向请求 LLM（详见 03-client/03-sampling）
- `ctx.elicit(...)` — 反向问用户（详见 03-client/04-roots-elicitation）
- `ctx.read_resource(uri)` — 读自己 Server 的 Resource
- `ctx.session` — 底层 session，用于高级操作

Context 是 FastMCP 的杀手锏，让 Tool 能用 MCP 的全部能力。

---

## 10. 低层 API（`Server` 类）

FastMCP 处理不了的场景可以用低层：

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

app = Server("low-level-demo")

@app.list_tools()
async def handle_list_tools() -> list[Tool]:
    return [
        Tool(
            name="add",
            description="两数相加",
            inputSchema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"}
                },
                "required": ["a", "b"]
            }
        )
    ]

@app.call_tool()
async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "add":
        return [TextContent(type="text", text=str(arguments["a"] + arguments["b"]))]
    raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (r, w):
        await app.run(r, w, app.create_initialization_options())

import asyncio
asyncio.run(main())
```

**什么时候用低层**：你需要完全控制 schema 生成（比如手工写复杂 schema、动态注册工具、跨语言生成等）。

99% 场景下用 FastMCP。

---

## 11. 跨 Server 命名冲突

两个 Server 都暴露 `search`，Host 怎么办？

- **Claude Code**：自动加 namespace，变成 `<server-name>__search`
- **某些客户端**：UI 上让用户选用哪个

**Server 端最佳实践**：自己加前缀（`github_search`、`db_search`），避免依赖 Client 自动处理。

---

## 12. 完整 demo：weather server

来一个综合用了 schema / structuredContent / annotations / 错误处理 / 进度的 demo：

```python
# demos/server/01_tools_weather.py
import asyncio
import random
from typing import Literal

from pydantic import BaseModel, Field
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP("weather-pro")


class Forecast(BaseModel):
    city: str
    date: str
    temperature_c: float = Field(description="摄氏温度")
    conditions: str
    humidity: float = Field(ge=0, le=1, description="0-1 之间")


@mcp.tool(
    annotations={
        "title": "获取天气预报",
        "readOnlyHint": True,
        "openWorldHint": True,  # 调外部 API
    }
)
async def get_forecast(
    city: str = Field(min_length=1, description="城市名（中文/英文）"),
    days: int = Field(default=1, ge=1, le=7),
    unit: Literal["c", "f"] = "c",
    ctx: Context = None,
) -> list[Forecast]:
    """查询某城市未来 N 天天气预报。"""
    if city.lower() == "atlantis":
        raise ToolError(f"未知城市: {city}（提示：尝试 'Beijing' / 'Tokyo' / 'NYC'）")

    await ctx.info(f"查询 {city} 未来 {days} 天天气")
    out = []
    for i in range(days):
        await ctx.report_progress(progress=i + 1, total=days)
        await asyncio.sleep(0.1)  # 模拟网络
        out.append(
            Forecast(
                city=city,
                date=f"2026-05-{20+i:02d}",
                temperature_c=random.uniform(15, 30),
                conditions=random.choice(["晴", "多云", "雨"]),
                humidity=random.random(),
            )
        )
    return out


if __name__ == "__main__":
    mcp.run()
```

跑 + Inspector：

```bash
npx @modelcontextprotocol/inspector python demos/server/01_tools_weather.py
```

在 Inspector 里试：
- `get_forecast(city="Atlantis")` → 看到 `isError: true` 和友好错误信息
- `get_forecast(city="Beijing", days=3)` → 看到 3 条 structuredContent + progress 通知

---

## 13. 常见坑

| 坑 | 排查 |
|----|------|
| **LLM 总不调你的工具** | description 写得不够"诱导"。学一下 GitHub MCP / Sentry MCP 的工具描述 |
| **参数类型错** | 类型注解必填。`def f(x)` 没注解 FastMCP 会拒绝注册 |
| **返回值大对象 LLM 看不懂** | 用 `outputSchema` + `BaseModel` 让 Client 拿到 typed object |
| **写操作没标 destructive** | 用 annotations 标，让 Host 帮你弹确认 |
| **`raise ValueError`** | 业务错误请用 `ToolError`，LLM 能自我纠正 |
| **`print()` 调试** | 用 `await ctx.info(...)` 或 `logging` 到 stderr |
| **跨 Server 同名工具** | Server 端加前缀，别赌 Client 自动 namespace |

---

## 14. 下一步

- 📖 Resources 设计 → [02-resources.md](./02-resources.md)
- 📖 错误细节 → [08-errors-validation.md](./08-errors-validation.md)
- 📖 进度通知 / 取消 → [06-logging-progress-ping.md](./06-logging-progress-ping.md)
- 📖 Sampling（让 Tool 反过来调 LLM）→ 03-client/03-sampling

## 参考资料

- Tools spec：https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- FastMCP 源码：https://github.com/modelcontextprotocol/python-sdk/tree/main/src/mcp/server/fastmcp
- JSON Schema 2020-12：https://json-schema.org/draft/2020-12
