# MCP Server 05：Completion 与 Pagination —— 让参数能联想、让列表能翻页

> **一句话**：Completion 让 Prompt 参数和 Resource Template 参数在 UI 上自动联想（像 IDE 补全），Pagination 让 `tools/list` / `resources/list` / `prompts/list` 在数千条目时不会一次性炸开。两个都是「Server 实用工具（utilities）」类能力，配合得当能极大提升 Host 端体验。

---

## 1. Completion：参数自动联想

### 1.1 场景

用户输入 `weather://forecast/Par` 时，希望弹出 "Paris / Park City" 候选；输入 prompt 参数 `repo=anth` 时，希望提示 "anthropic/anthropic-sdk-python"。

这就是 completion。

### 1.2 协议

Server 声明能力：

```json
{"capabilities": {"completions": {}}}
```

Client 发请求：

```json
{
  "method": "completion/complete",
  "params": {
    "ref": {"type": "ref/resource", "uri": "weather://forecast/{city}"},
    "argument": {"name": "city", "value": "Par"}
  }
}
```

Server 回：

```json
{
  "completion": {
    "values": ["Paris", "Park City"],
    "total": 2,
    "hasMore": false
  }
}
```

`ref` 支持两种：

- `ref/resource` — Resource Template 参数补全
- `ref/prompt` — Prompt 参数补全

### 1.3 Python SDK 落地

FastMCP 暴露 `@mcp.completion()` 装饰器：

```python
from mcp.server.fastmcp import FastMCP
from mcp.types import Completion, CompletionArgument, ResourceTemplateReference

mcp = FastMCP("weather-server")

@mcp.resource("weather://forecast/{city}")
def forecast(city: str) -> str:
    return f"{city}: 晴 22°C"

# 城市数据源
CITIES = ["Paris", "Park City", "Beijing", "Barcelona", "Bangkok"]

@mcp.completion()
async def complete(
    ref: ResourceTemplateReference | PromptReference,
    argument: CompletionArgument,
) -> Completion | None:
    # 只补 weather://forecast/{city} 的 city 参数
    if (isinstance(ref, ResourceTemplateReference)
        and ref.uri == "weather://forecast/{city}"
        and argument.name == "city"):
        prefix = argument.value.lower()
        matches = [c for c in CITIES if c.lower().startswith(prefix)]
        return Completion(values=matches, total=len(matches), hasMore=False)
    return None
```

> SDK 当前的 `@mcp.completion()` 是单点回调（一个函数处理所有 ref + argument），需要自己 if-else 分发。

### 1.4 限制

- `values` 最多 100 条（规范约束）
- 字符串数组（不能附带 description）
- 没有官方"分页 completion"机制——用 `hasMore` 指示有更多但不直接拉

---

## 2. Pagination：列表翻页

### 2.1 哪些方法支持
官方 spec 里这些 `list` 方法支持分页：

- `tools/list`
- `resources/list`
- `resources/templates/list`
- `prompts/list`

### 2.2 协议（基于 cursor）

```json
// 第一页
{"method":"tools/list","params":{"cursor":null}}
// 响应
{
  "tools":[...],
  "nextCursor":"opaque-string"
}

// 第二页
{"method":"tools/list","params":{"cursor":"opaque-string"}}
// 响应
{
  "tools":[...],
  "nextCursor":"another-cursor"
}

// 最后一页：不返回 nextCursor
```

- cursor 是不透明字符串（Server 自定义编码）
- Client 不解析 cursor，只透传
- 不返回 `nextCursor` ⇒ 没有下一页

### 2.3 Python SDK 现状

FastMCP 当前**默认把所有工具/资源一次性返回**，没暴露 pagination hook 给应用层——因为常见 MCP Server 工具数量都是几十个，不需要分页。

如果你写一个 1000+ 工具的 Server（例如把整个数据库的几百张表都做成工具，或动态生成工具），需要用低层 `Server` 类自己处理分页：

```python
from mcp.server import Server
from mcp.types import Tool, ListToolsResult

PAGE_SIZE = 50
ALL_TOOLS = [Tool(name=f"tool_{i}", inputSchema={"type": "object"}) for i in range(1000)]

app = Server("paged-server")

@app.list_tools()
async def handle_list_tools(cursor: str | None = None) -> ListToolsResult:
    start = int(cursor) if cursor else 0
    page = ALL_TOOLS[start:start + PAGE_SIZE]
    next_cursor = str(start + PAGE_SIZE) if start + PAGE_SIZE < len(ALL_TOOLS) else None
    return ListToolsResult(tools=page, nextCursor=next_cursor)
```

> 实际工程里 cursor 推荐用 opaque 编码（如 base64 of `{offset:50, version:7}`），避免 Client 解析它。

### 2.4 Client 端翻页

```python
async with ClientSession(read, write) as session:
    await session.initialize()

    all_tools = []
    cursor = None
    while True:
        resp = await session.list_tools(cursor=cursor)
        all_tools.extend(resp.tools)
        cursor = resp.nextCursor
        if not cursor:
            break
    print(f"总工具数：{len(all_tools)}")
```

---

## 3. Completion + Pagination 综合 demo

```python
# demos/server/05_completion_paged.py
"""演示 completion + 大量工具（不分页时只是慢）"""
from mcp.server.fastmcp import FastMCP
from mcp.types import Completion, CompletionArgument, ResourceTemplateReference, PromptReference

mcp = FastMCP("kb-search")

# 模拟一个有 100 个文档的知识库
DOCS = [f"doc_{i:03d}" for i in range(100)]
TAGS = ["python", "javascript", "rust", "go", "java", "swift", "kotlin"]

@mcp.resource("kb://doc/{doc_id}")
def doc(doc_id: str) -> str:
    return f"内容: {doc_id}"

@mcp.prompt(name="search-docs")
def search_docs(query: str, tag: str = "all") -> str:
    return f"搜索: {query}（标签: {tag}）"

@mcp.completion()
async def complete(ref, argument: CompletionArgument):
    prefix = argument.value.lower()

    # 1. Resource Template 的 doc_id 补全
    if isinstance(ref, ResourceTemplateReference) and ref.uri == "kb://doc/{doc_id}":
        if argument.name == "doc_id":
            matches = [d for d in DOCS if d.startswith(prefix)][:100]
            return Completion(
                values=matches,
                total=len([d for d in DOCS if d.startswith(prefix)]),
                hasMore=len(matches) >= 100,
            )

    # 2. Prompt 的 tag 参数补全
    if isinstance(ref, PromptReference) and ref.name == "search-docs":
        if argument.name == "tag":
            matches = [t for t in TAGS if t.startswith(prefix)]
            return Completion(values=matches, total=len(matches), hasMore=False)

    return None

if __name__ == "__main__":
    mcp.run()
```

在 Inspector 里：
- 输入 `kb://doc/doc_01`，看候选弹出
- 选 prompt `search-docs`，在 `tag=` 后输入 `py` 看候选

---

## 4. 设计建议

### 4.1 Completion
- **要快**：补全请求一秒触发一次，要求毫秒级响应。重 IO 不要用，用预加载 / 内存缓存
- **要稳**：函数失败别抛异常，返回 `None`（无补全）
- **prefix 匹配**：默认大小写不敏感、`startswith` 优先；可以加 fuzzy 但要小心性能
- **总数 vs 返回数**：`total` 是 prefix 匹配的总数；`values` 是这次返回的数量；`hasMore` 标识是否截断

### 4.2 Pagination
- **何时需要**：list 返回 > 100 条目；体验上能感知卡顿就要分页
- **cursor 设计**：opaque 字符串、base64 + 版本号，避免 Client 解析
- **客户端代码量**：建议 SDK 封装一个 `iter_tools()` 自动翻页，业务代码别让用户自己写 while

---

## 5. 常见坑

| 坑 | 排查 |
|----|------|
| **没声明 `completions` 能力** | Server 端必须在 capabilities 里加，FastMCP 在第一个 `@mcp.completion()` 时会自动声明 |
| **补全返回太多** | 限制 100；前端 UI 通常只展示前 10 |
| **cursor 是数字让 Client 直接 +1** | Client 不能解析 cursor，必须只透传 Server 给的字符串 |
| **list 返回了重复条目** | 通常是分页时数据源在变，要么快照、要么用稳定排序 |
| **补全函数抛异常** | 整个补全请求失败，UI 体验崩；务必 try/except 返回 None |

---

## 6. 下一步

- 📖 Logging / Progress / Ping → [06-logging-progress-ping.md](./06-logging-progress-ping.md)
- 📖 Tasks 扩展 → [07-tasks.md](./07-tasks.md)
- 📖 错误处理 → [08-errors-validation.md](./08-errors-validation.md)

## 参考资料

- Completion spec：https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion
- Pagination spec：https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination
