# MCP Server 02：用 Resources 提供上下文数据

> Resource 是 MCP Server 暴露给 AI 应用读取的只读数据，读它的是应用而不是模型。它用 URI 寻址，支持模板和订阅。不变或很少变的上下文用 Resource 暴露，比硬塞进 Tool 清爽得多。

---

## 1. Tool 和 Resource 的区别

| 维度 | Tool | Resource |
|------|------|----------|
| 谁触发 | 模型在对话中决定 | 应用（Host）在 UI 上或按策略决定 |
| 副作用 | 可以有 | 不应该有（幂等只读） |
| 寻址 | name + arguments | URI |
| 类比 | 函数调用 | HTTP GET |

「查订单」适合做成 Tool，参数多变，由模型自己决定什么时候调；「订单表 schema」适合做成 Resource，应用启动时一次性放进 system prompt。

---

## 2. 最小可用代码

```python
from mcp.server import MCPServer

mcp = MCPServer("docs-server")

# 直接 Resource：固定 URI
@mcp.resource("docs://readme")
def readme() -> str:
    """项目 README"""
    with open("README.md") as f:
        return f.read()

# 资源模板：URI 含占位符
@mcp.resource("docs://chapter/{number}")
def chapter(number: str) -> str:
    """读第 N 章"""
    return f"# 第 {number} 章\n..."
```

MCPServer 会自动：

1. 声明 `resources` capability（在 `server/discover` 里，或老客户端的 `initialize` 里）
2. 注册到 `resources/list`（直接 Resource）或 `resources/templates/list`（带 `{...}` 的）
3. 处理 `resources/read` 请求

---

## 3. 选择 URI Scheme

URI 可以随意定，但要符合 [RFC3986](https://datatracker.ietf.org/doc/html/rfc3986)。约定俗成的标准 scheme：

| Scheme | 用途 |
|--------|------|
| `file://` | 本地文件系统 |
| `https://` | Web 资源（仅当 Client 能直接 GET 时用） |
| `git://` | Git 集成 |
| 自定义 | `db://`、`gh://`、`figma://`、`s3://` 等 |

选哪种取决于 Client 能不能直接通过 HTTP 拿到内容。能的话用 `https://`，相当于告诉 Client 自己去取；需要鉴权或者要做业务转换的，用自定义 scheme，让 Client 通过 `resources/read` 走 MCP 通道拿。

---

## 4. 直接 Resource 与 Resource Template

### 4.1 直接 Resource
URI 固定，列在 `resources/list` 里，用户可以直接在客户端 UI 上选。

```python
@mcp.resource("config://app")
def app_config() -> str:
    return "log_level=info\ntimeout=30"
```

### 4.2 Resource Template
URI 里带占位符 `{x}`，遵循 RFC 6570 URI Template。v2 SDK 完整实现了 RFC 6570：`{+path}` 能匹配跨多段的路径，`{?q,lang}` 是可选查询参数，匹配是精确的，默认还会拒绝 `..` 之类的路径穿越。模板列在 `resources/templates/list` 里，不会出现在 `resources/list` 中，客户端要先拼出具体 URI 再读。模板占位符必须和函数参数名一致，否则装饰时就会抛 `ValueError`，Server 起不来。

```python
@mcp.resource("db://tables/{table}")
def table_data(table: str) -> str:
    """读某张表前 100 行"""
    # ⚠️ 生产代码要做表名白名单！
    rows = db.fetch(f"SELECT * FROM {table} LIMIT 100")
    return "\n".join(map(str, rows))
```

参数补全（completion API）：用户在 UI 里输入 `db://tables/u` 时，Client 可以问 Server 有哪些以 u 开头的表。详见[参数补全（Completion）与分页（Pagination）](05-completion-pagination.md)。

---

## 5. 返回内容的类型

`resources/read` 返回的 `contents` 数组里，每一项可以是文本或二进制。

### 5.1 文本

```python
@mcp.resource("notes://daily/{date}")
def daily_note(date: str) -> str:
    """读某天日记"""
    return f"# {date}\n今天写了 MCP 手册..."
```

MCPServer 自动用 MIME `text/plain` 包装。

### 5.2 自定义 MIME

```python
@mcp.resource("docs://api", mime_type="text/markdown")
def api_docs() -> str:
    return "# API\n..."
```

### 5.3 二进制（图片、PDF、任意 bytes）

```python
@mcp.resource("img://logo", mime_type="image/png")
def logo() -> bytes:
    with open("logo.png", "rb") as f:
        return f.read()
```

返回 `bytes` 时，MCPServer 会转成 base64，放进 `blob` 字段（`BlobResourceContents`）。如果只想给一个指针而不是内容本身，可以在 Tool 里返回 `ResourceLink`（见[用 Tools 暴露可调用的操作](01-tools.md)）。

### 5.4 JSON / dict

```python
@mcp.resource("api://users/{user_id}")
def user(user_id: str) -> dict:
    return {"id": user_id, "name": "Alice"}
```

返回 `dict` / `list` 时，MCPServer 会把它序列化成 JSON 字符串放进 `text`；建议显式写上 `mime_type="application/json"`。

---

## 6. Resource 元数据与 annotations

Resource、ResourceTemplate 和内容块都支持 `annotations`，用来提示 Client 怎么使用：

```python
from mcp.server import MCPServer

mcp = MCPServer("demo")

@mcp.resource(
    "docs://readme",
    name="readme",
    title="项目 README",
    mime_type="text/markdown",
)
def readme() -> str:
    """项目根目录的 README"""
    return open("README.md").read()
```

元数据字段：

| 字段 | 含义 |
|------|------|
| `name` | 协议级唯一名 |
| `title` | UI 显示名（可以是中文） |
| `description` | 描述 |
| `mime_type` | MIME 类型 |
| `size` | 字节数（可选） |
| `icons` | 显示图标（可选） |

`annotations` 附加在 resource content 上，需要用低层 API 设置（JSON 里是 camelCase，Python 类型里是 snake_case）：

| Annotation | 含义 |
|-----------|------|
| `audience` | `["user"]` / `["assistant"]` / `["user","assistant"]`，表示给谁看 |
| `priority` | 0–1，越大越重要 |
| `lastModified` / `last_modified` | ISO 8601 时间戳，方便 Client 按时间排序 |

另外，从 2026-07-28 起，`resources/list`、`resources/read`、`resources/templates/list` 的结果必须带缓存提示 `ttlMs` 和 `cacheScope`。在 `MCPServer(cache_hints={...})` 里按方法配置即可，Client 内置的响应缓存会遵守它（见[自己写 Client（连接、发现与调用工具）](../03-client/01-client-basics.md)）。

---

## 7. 订阅 Resource 变化（`subscriptions/listen`）

2026-07-28 用一个新方法 `subscriptions/listen` 取代了 `resources/subscribe` / `resources/unsubscribe` 和独立的 HTTP GET 通知流。Client 只发一个请求，这个请求的响应就是一条长连接流。Client 在请求里声明想监听哪几类变化（`toolsListChanged`、`promptsListChanged`、`resourcesListChanged`、`resourceSubscriptions: [uri...]`），Server 先发一条确认（`notifications/subscriptions/acknowledged`），之后在这条流上推送变化，每一帧的 `_meta` 里都带着订阅 ID。

通知本身不带内容，只是提示「该重新读了」。进度、日志这类请求级通知不走这条流，仍然随各自请求的响应流返回。

### 7.1 Server 端：发布变更

`MCPServer` 默认就支持 `subscriptions/listen`，发布变更只要一行：

```python
from mcp.server import MCPServer
from mcp.server.mcpserver import Context

mcp = MCPServer("Sprint Board")
BOARDS = {"sprint": {"design": False, "build": False, "ship": False}}

@mcp.resource("board://{name}")
def board(name: str) -> str:
    tasks = BOARDS[name]
    return "\n".join(f"[{'x' if done else ' '}] {task}" for task, done in tasks.items())

@mcp.tool()
async def complete_task(board: str, task: str, ctx: Context) -> str:
    BOARDS[board][task] = True
    await ctx.notify_resource_updated(f"board://{board}")   # 只送达订阅了这个 URI 的流
    return f"{task}: done"
```

`Context` 上的发布方法有 `notify_resource_updated(uri)`、`notify_resources_changed()`、`notify_tools_changed()`、`notify_prompts_changed()`。没人订阅时调用它们什么也不做，不用先判断有没有人在听。

几个要注意的地方：

- 过滤规则是约定好的：一条流只会收到它声明过的那几类变化。URI 按字符串精确匹配，订阅 `board://sprint` 收不到 `board://sprint/tasks/1` 的变化
- 流不是重放日志。断了就断了，断线期间的事件不会排队，Client 应该重新 listen 并重新拉取
- 老的 `ctx.session.send_resource_updated(uri)` 只服务调过 `resources/subscribe` 的 2025 版老客户端，`notify_*` 只服务 `subscriptions/listen` 流。想同时照顾两代，就两个都调
- 默认任何调用者都能订阅任何 URI（只能知道「变了」，看不到内容）。多租户场景可以用 middleware 在 `subscriptions/listen` 上做鉴权（见 SDK 文档的 Middleware 一节）
- 多副本部署时，默认的订阅总线在进程内存里，副本 A 上发布的变更到不了连在副本 B 上的流。要么给所有副本实现一个共享的 `SubscriptionBus`（比如用 Redis pub/sub），要么用 `MCPServer(subscriptions=False)` 关掉订阅

### 7.2 Client 端：listen

```python
from mcp import Client
from mcp.client.subscriptions import ResourceUpdated, ToolsListChanged

async with Client("http://localhost:8000/mcp") as client:
    async with client.listen(tools_list_changed=True, resource_subscriptions=["board://sprint"]) as sub:
        print("server 同意的订阅：", sub.honored)
        async for event in sub:
            match event:
                case ResourceUpdated(uri=uri):
                    text = (await client.read_resource(uri)).contents[0].text   # 事件只是信号，重新读
                case ToolsListChanged():
                    tools = await client.list_tools()
```

完整可运行的版本在 `demos/server/02_resources_subscribe.py`，Server 和 Client 在一个文件里，一条命令就能看到事件到达。连接 2025 版的老 Server 时，Client 还可以用 `subscribe_resource()`（已标记弃用）。

实时仪表盘、日志流、协作编辑同步这类场景适合用订阅。

---

## 8. list_changed 通知

Server 声明了 `listChanged: true` 之后，Resource 列表一旦变化（新增、删除、改名），就要通知 Client 重新调 `resources/list`：

- 2026 版订阅者：`await ctx.notify_resources_changed()`
- 2025 版老客户端：`await ctx.session.send_resource_list_changed()`（`send_tool_list_changed()`、`send_prompt_list_changed()` 同理）

```python
@mcp.tool()
async def add_doc(ctx: Context) -> str:
    mcp.add_resource(...)                     # 动态新增一个 Resource 对象（可用 FunctionResource/TextResource）
    await ctx.notify_resources_changed()
    return "ok"
```

`MCPServer.get_context()` 在 v2 里已移除，拿上下文统一用 `ctx: Context` 参数。

---

## 9. 综合 demo：项目知识库 Server

```python
# demos/server/02_resources_kb.py（节选，完整版含 --selftest）
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError

mcp = MCPServer("kb-server")
ROOT = Path(__file__).resolve().parents[2]  # 指向 03-mcp/


@mcp.resource("kb://readme", title="项目 README", mime_type="text/markdown")
def readme() -> str:
    return (ROOT / "README.md").read_text()


@mcp.resource("kb://tree", title="项目文件树", mime_type="text/plain")
def file_tree() -> str:
    return "\n".join(str(p.relative_to(ROOT)) for p in sorted(ROOT.rglob("*.md")))


@mcp.resource("kb://doc/{+path}", mime_type="text/markdown")  # {+path} 是 RFC 6570：可跨多段路径
def doc(path: str) -> str:
    """读 docs/ 下的任意 markdown 文件，path 是 docs/ 后的相对路径"""
    docs_root = (ROOT / "docs").resolve()
    target = (docs_root / path).resolve()
    # 双保险：SDK 已默认拒绝 `..` 之类的路径穿越，这里再确认一次
    if not target.is_relative_to(docs_root) or not target.is_file():
        raise ResourceNotFoundError(f"没有这篇文档: {path}")
    return target.read_text()
```

用 Inspector 试一下：

- `kb://readme`：整本手册的 README
- `kb://tree`：列出所有文档
- `kb://doc/01-basics/03-primitives.md`：读这一篇

第三个资源做了路径越权防御，这是 Resource 模板最常见的安全问题（详见[防范 Prompt 注入、Tool 投毒与路径越权](../05-production/04-security.md)）。v2 的模板匹配本身就会拒绝 `..` 之类的路径穿越（`kb://doc/../README.md` 会得到 `Unknown resource`），代码里的 `is_relative_to` 是第二道保险。资源不存在时抛 `ResourceNotFoundError`，Client 收到的是 `-32602`，`data` 里带着 URI（2025 版是 `-32002`）。

---

## 10. 什么时候不该用 Resource

| 场景 | 更好的选择 |
|------|-----------|
| 数据需要参数化查询，可能慢，有成本 | Tool（让模型显式调用） |
| 数据高度动态，读的同时还有写 | Tool |
| 用户主动启动一段流程 | Prompt |

一个反例：把 SQL 查询做成 Resource Template `db://query/{sql}`。SQL 放进 URI 很别扭，注入风险高，而且每次都是一次新计算，应该做成 Tool。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| 没填模板参数就 read | `db://tables/{table}` 必须填具体值，URI 要完整 |
| Resource 里有副作用 | Resource 应当始终幂等只读。「读完要改状态」的需求请用 Tool |
| Resource 太大 | 大文件考虑分页，或者用 Tool 按需查；也可以用 `resource_link` 返回链接而不是内容 |
| 二进制忘了 base64 | MCPServer 会自动处理；用低层 API 时要自己用 `BlobResourceContents` 并做 base64 |
| 多副本下订阅悄悄失效 | 默认总线在进程内，多副本要共享 `SubscriptionBus`，或者关掉订阅 |
| 以为 `notify_*` 能通知老客户端 | `notify_*` 只发给 2026 版的 listen 流；2025 版订阅者要用 `ctx.session.send_*` |
| 路径越权 | `file://` 或自定义路径模板一定要做沙盒检查 |
| 没声明 `listChanged` 却动态增删 | 必须在 capability 里声明，否则 Client 不知道要刷新 |

---

## 12. 下一步

- Prompt 设计：[用 Prompts 封装可复用的对话模板](./03-prompts.md)
- 生命周期与上下文注入（在 Resource 里访问 DB 连接等）：[用 Lifespan 和 Context 向工具注入共享资源](./04-lifespan-context.md)
- 参数补全与分页：[参数补全（Completion）与分页（Pagination）](./05-completion-pagination.md)
- 安全与路径越权：[防范 Prompt 注入、Tool 投毒与路径越权](../05-production/04-security.md)

## 参考资料

- Resources spec：https://modelcontextprotocol.io/specification/2026-07-28/server/resources
- Subscriptions（SDK）：https://py.sdk.modelcontextprotocol.io/handlers/subscriptions/
- URI templates（SDK）：https://py.sdk.modelcontextprotocol.io/servers/uri-templates/
- URI Template (RFC 6570)：https://datatracker.ietf.org/doc/html/rfc6570
- RFC 3986 URI：https://datatracker.ietf.org/doc/html/rfc3986
