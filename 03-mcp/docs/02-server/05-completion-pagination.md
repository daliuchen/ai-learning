# MCP Server 05：参数补全（Completion）与分页（Pagination）

> Completion 让 Prompt 参数和 Resource Template 参数在 UI 上能自动联想，效果类似 IDE 补全；Pagination 让 `tools/list`、`resources/list`、`prompts/list` 在条目多到几千条时可以分页返回。两者都属于 Server 的实用工具（utilities）类能力，用得好能明显改善 Host 端的体验。

---

## 1. Completion：参数自动联想

### 1.1 场景

用户输入 `weather://forecast/Par` 时，希望弹出 Paris、Park City 这样的候选；在 prompt 参数里输入 `repo=anth` 时，希望提示 anthropic/anthropic-sdk-python。这就是 completion 要做的事。

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

Server 回复：

```json
{
  "completion": {
    "values": ["Paris", "Park City"],
    "total": 2,
    "hasMore": false
  }
}
```

`ref` 有两种：

- `ref/resource`：Resource Template 参数补全
- `ref/prompt`：Prompt 参数补全

### 1.3 Python SDK 写法

MCPServer 提供 `@mcp.completion()` 装饰器。每个 Server 只有一个补全处理函数（必须是 `async def`），所有补全请求都进到这里，由你按 `ref` 和 `argument.name` 自己分发：

```python
from mcp.server import MCPServer
from mcp.types import (
    Completion, CompletionArgument, CompletionContext,
    PromptReference, ResourceTemplateReference,
)

mcp = MCPServer("weather-server")

@mcp.resource("weather://forecast/{city}")
def forecast(city: str) -> str:
    return f"{city}: 晴 22°C"

CITIES = ["Paris", "Park City", "Beijing", "Barcelona", "Bangkok"]

@mcp.completion()
async def complete(
    ref: PromptReference | ResourceTemplateReference,
    argument: CompletionArgument,
    context: CompletionContext | None,   # 用户已经填好的其它参数（用于「依赖补全」）
) -> Completion | None:
    # 只补 weather://forecast/{city} 的 city 参数
    if (isinstance(ref, ResourceTemplateReference)
        and ref.uri == "weather://forecast/{city}"
        and argument.name == "city"):
        prefix = argument.value.lower()               # SDK 不会替你过滤，startswith 要自己写
        matches = [c for c in CITIES if c.lower().startswith(prefix)]
        return Completion(values=matches, total=len(matches), has_more=False)
    return None                                       # None = 没有建议（不是错误）
```

几点说明：

- 注册处理函数就等于声明能力。SDK 看到 `@mcp.completion()` 才会在 capabilities 里加上 `completions`；没注册的话，客户端调 `completion/complete` 会得到 `Method not found`
- `Completion` 的字段在 Python 里是 snake_case：`has_more`（线上仍是 `hasMore`）
- 依赖补全：`context.arguments` 是已经确定的参数（比如先选了 `owner`，再按 `owner` 补全 `repo`），客户端通过 `client.complete(ref, argument, context_arguments={...})` 传入
- 客户端调用：`await client.complete(PromptReference(name="search-docs"), {"name": "tag", "value": "py"})`

### 1.4 限制

- `values` 最多 100 条（规范约束）
- 只能是字符串数组，不能附带说明
- 没有官方的分页 completion 机制，`hasMore` 只能表示还有更多，不能直接拉取

---

## 2. Pagination：列表翻页

### 2.1 支持分页的方法
官方 spec 里，下面这些 `list` 方法支持分页：

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

- cursor 是不透明的字符串，编码方式由 Server 决定
- Client 不解析 cursor，只原样传回
- 不返回 `nextCursor` 就表示没有下一页

### 2.3 Python SDK 现状

`MCPServer` 默认把所有工具和资源一次性返回（单页，`next_cursor=None`），`@mcp.resource()` 等装饰器也没有分页钩子。常见的 MCP Server 只有几十个工具，用不着分页。

如果列表背后其实是一个数据库（几千条资源），就得用低层 `Server` 自己写列表处理函数。v2 的低层 API 用构造参数 `on_list_resources=` 等，参数类型是 `PaginatedRequestParams | None`：

```python
from typing import Any
from mcp.server import Server, ServerRequestContext
from mcp.types import ListResourcesResult, PaginatedRequestParams, Resource

BOOKS = [f"book-{n}" for n in range(1, 101)]
PAGE_SIZE = 10

async def list_books(ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None) -> ListResourcesResult:
    start = 0 if params is None or params.cursor is None else int(params.cursor)
    end = start + PAGE_SIZE
    page = [Resource(uri=f"books://catalog/{n}", name=n) for n in BOOKS[start:end]]
    return ListResourcesResult(resources=page, next_cursor=str(end) if end < len(BOOKS) else None)

server = Server("Bookshop", on_list_resources=list_books)
app = server.streamable_http_app()      # uvicorn 模块名:app --port 8000
```

- cursor 的含义由你决定。这里用的是字符串化的 offset，换成时间戳、主键或 base64 都行
- `next_cursor=None` 表示最后一页，没有 total，也没有 `has_more`
- 页大小由 Server 决定，协议里没有 `limit`
- `on_list_tools` / `on_list_prompts` / `on_list_resource_templates` 都可以这样做

> 实际项目里，cursor 最好用不透明编码（比如把 `{offset:50, version:7}` 做 base64），免得 Client 去解析它。2026-07-28 还要求 `tools/list` 等方法「SHOULD 按确定性顺序返回」，这样客户端缓存和 LLM 的 prompt cache 更容易命中。

### 2.4 Client 端翻页

```python
from mcp import Client

async with Client("http://localhost:8000/mcp") as client:
    all_resources = []
    cursor = None
    while True:
        page = await client.list_resources(cursor=cursor)   # list_tools / list_prompts 等同理
        all_resources.extend(page.resources)
        cursor = page.next_cursor
        if cursor is None:
            break
    print(f"总数：{len(all_resources)}")
```

> 在 v2 里，`cursor=` 是 `Client.list_*` 的关键字参数（底层 `ClientSession` 的 list 方法不再接收 `cursor`）。对不分页的 Server，第一页的 `next_cursor` 就是 `None`，循环只跑一次，所以这个循环放在哪里都不会出问题。

---

## 3. Completion 与 Pagination 综合 demo

```python
# demos/server/05_completion_paged.py（完整版含自测 main()，一条命令跑两部分）
from typing import Any

from mcp import Client
from mcp.server import MCPServer, Server, ServerRequestContext
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    ListResourcesResult,
    PaginatedRequestParams,
    PromptReference,
    Resource,
    ResourceTemplateReference,
)

# ============ 1. Completion：MCPServer 上唯一的一个 @mcp.completion() 处理函数 ============
mcp = MCPServer("kb-search")
DOCS = [f"doc_{i:03d}" for i in range(100)]
TAGS = ["python", "javascript", "rust", "go", "java", "swift", "kotlin"]
REPOS_BY_OWNER = {"modelcontextprotocol": ["python-sdk", "typescript-sdk", "inspector"], "pydantic": ["pydantic", "pydantic-ai"]}


@mcp.resource("kb://doc/{doc_id}")
def doc(doc_id: str) -> str:
    return f"内容: {doc_id}"


@mcp.resource("github://repos/{owner}/{repo}")
def repo(owner: str, repo: str) -> str:
    return f"{owner}/{repo}"


@mcp.prompt(name="search-docs")
def search_docs(query: str, tag: str = "all") -> str:
    return f"搜索: {query}（标签: {tag}）"


@mcp.completion()  # 注册这个处理函数 = 声明 completions 能力；必须是 async def
async def complete(
    ref: PromptReference | ResourceTemplateReference,
    argument: CompletionArgument,
    context: CompletionContext | None,  # 已经填好的其它参数（依赖补全）
) -> Completion | None:
    prefix = argument.value.lower()  # SDK 不会替你过滤，startswith 要自己写
    if isinstance(ref, ResourceTemplateReference) and ref.uri == "kb://doc/{doc_id}" and argument.name == "doc_id":
        matches = [d for d in DOCS if d.startswith(prefix)]
        return Completion(values=matches[:100], total=len(matches), has_more=len(matches) > 100)
    if isinstance(ref, PromptReference) and ref.name == "search-docs" and argument.name == "tag":
        return Completion(values=[t for t in TAGS if t.startswith(prefix)])
    if isinstance(ref, ResourceTemplateReference) and argument.name == "repo":
        owner = (context.arguments or {}).get("owner", "") if context else ""
        return Completion(values=[r for r in REPOS_BY_OWNER.get(owner, []) if r.startswith(prefix)])
    return None  # None = 没有建议，不是错误


# ============ 2. Pagination：MCPServer 不暴露分页 hook，用低层 Server 自己写 ============
BOOKS = [f"book-{n}" for n in range(1, 101)]
PAGE_SIZE = 10


async def list_books(ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None) -> ListResourcesResult:
    start = 0 if params is None or params.cursor is None else int(params.cursor)
    end = start + PAGE_SIZE
    page = [Resource(uri=f"books://catalog/{n}", name=n) for n in BOOKS[start:end]]
    return ListResourcesResult(resources=page, next_cursor=str(end) if end < len(BOOKS) else None)


paged = Server("Bookshop", on_list_resources=list_books)
# 要部署的话：app = paged.streamable_http_app()，再 `uvicorn 模块:app`
```

运行（进程内自测）：

```
$ python demos/server/05_completion_paged.py
completions 能力: 
tag=ja -> ['javascript', 'java']
doc_01* -> ['doc_010', 'doc_011', 'doc_012'] total = 10
owner=pydantic repo -> ['pydantic', 'pydantic-ai']
翻页取回 100 条
```

在 Inspector 里可以：
- 输入 `kb://doc/doc_01`，看候选弹出来
- 选 prompt `search-docs`，在 `tag=` 后面输入 `py`，看候选

---

## 4. 设计建议

### 4.1 Completion
- 要快。用户每敲一下键就可能触发一次补全，需要毫秒级响应。别做重 IO，用预加载或内存缓存
- 要稳。函数出错时不要抛异常，返回 `None`（表示没有补全）
- 匹配方式：默认大小写不敏感，`startswith` 优先；可以加模糊匹配，但要注意性能
- `total` 是前缀匹配到的总数，`values` 是这次返回的条目，`hasMore` 表示有没有被截断

### 4.2 Pagination
- 什么时候需要：list 返回超过 100 条，或者用户已经能感觉到卡顿
- cursor 设计：不透明字符串，base64 加版本号，避免 Client 解析
- 客户端：最好在 SDK 层封装一个自动翻页的 `iter_tools()`，别让业务代码自己写 while 循环

---

## 5. 常见坑

| 坑 | 排查 |
|----|------|
| 没声明 `completions` 能力 | 注册了 `@mcp.completion()` 处理函数就等于声明了；没注册的话客户端会得到 `Method not found` |
| 补全函数写成了同步 `def` | 必须是 `async def` |
| 补全返回的条目太多 | 上限 100 条，前端 UI 一般只显示前 10 条 |
| cursor 是数字，Client 直接加 1 | Client 不能解析 cursor，只能原样传回 Server 给的字符串 |
| list 返回了重复条目 | 多半是分页期间数据源在变，要么做快照，要么用稳定排序 |
| 补全函数抛异常 | 整个补全请求失败，UI 上就没反应了；一定要 try/except 后返回 None |

---

## 6. 下一步

- Logging、Progress 与取消：[日志、进度、取消与 Ping](./06-logging-progress-ping.md)
- Tasks 扩展：[用 Tasks 扩展异步执行长请求](./07-tasks.md)
- 错误处理：[错误处理与参数校验](./08-errors-validation.md)

## 参考资料

- Completion spec：https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/completion
- Pagination spec：https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/pagination
- SDK Completions：https://py.sdk.modelcontextprotocol.io/servers/completions/
- SDK Pagination：https://py.sdk.modelcontextprotocol.io/advanced/pagination/
