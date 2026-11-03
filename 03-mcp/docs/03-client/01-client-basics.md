# MCP Client 01：自己写 Client（连接、发现与调用工具）

> MCP Client 不只是 Claude Code 里的那个组件。脚本、Agent 框架、自己做的产品，只要想用 MCP Server 的能力，都可以拿 `mcp` SDK 当 Client。mcp 2.x 把 v1 里的三层嵌套（transport → `ClientSession` → `initialize()`）合并成了一个 `mcp.Client`：进入 `async with` 时连接并协商协议，之后每个协议动词都是它的一个 async 方法。

---

## 1. `Client` 总览

```python
import asyncio
from mcp import Client, StdioServerParameters


async def main():
    params = StdioServerParameters(command="python", args=["server.py"])
    async with Client(params) as client:
        tools = await client.list_tools()
        print([t.name for t in tools.tools])


asyncio.run(main())
```

几个要点：

- 一个对象对应一个生命周期：构造，进入 `async with`，调用方法。没有 `connect()` / `close()`，退出 `async with` 时断开连接，用过的 `Client` 不能再用
- 构造时什么都不做。`Client(...)` 只是选定传输方式，进入 `async with` 才真正打开连接（构造之后、进入之前调用方法，会得到 `RuntimeError: Client must be used within an async context manager`）
- 第一个位置参数决定传输方式（见[传输方式 stdio、Streamable HTTP 与进程内](02-transports.md)）：

| 传入 | 传输 |
|------|------|
| URL 字符串 `"http://localhost:8000/mcp"` | Streamable HTTP（生产用） |
| `StdioServerParameters(...)` | 起本地子进程，走 stdin/stdout |
| `MCPServer` / `Server` 实例 | 进程内连接，不起子进程，不占端口（测试用） |
| 其它传输对象（如 `streamable_http_client(url, http_client=...)`、`sse_client(url)`） | 直接使用 |

- 进入之后，连接信息就是普通属性：

| 属性 | 含义 |
|------|------|
| `client.server_info` | Server 身份；2026 版里身份是可选元数据，可能为 `None` |
| `client.server_capabilities` | Server 能力（没有的能力为 `None`） |
| `client.protocol_version` | 协商出的协议版本，如 `"2026-07-28"` |
| `client.instructions` | Server 通过 `instructions=` 给的使用说明，没设置则为 `None` |
| `client.session` | 底层 `ClientSession`（留给特殊情况用；它在 v2 里签名也变了，谨慎使用） |

> 从 v1 迁移：`ClientSession`、`stdio_client`、`session.initialize()` 都还在，但一般用法统一换成 `Client`。所有类型字段改成了 snake_case（`tool.input_schema`、`result.is_error`、`result.structured_content`、`listing.next_cursor`），线上的 JSON 仍是 camelCase。`McpError` 改名为 `MCPError`（异常对象用 `e.code` 取错误码）。超时参数是以秒为单位的 `float`，不再是 `timedelta`。

---

## 2. 所有可调用的方法

### 2.1 连接与协商
不再有手动握手的方法。要控制协商方式，用构造参数 `mode=`（详见[协议生命周期（JSON-RPC、版本发现与能力协商）](../01-basics/04-protocol-lifecycle.md) 第 8 节）：`"auto"` 是默认值，先发 `server/discover`，失败则回退到 `initialize`；`"legacy"` 强制使用 2025 式握手，需要 Server 发反向请求时用；`"2026-07-28"` 固定版本，没有任何协商流量。也没有 `send_ping()`，`ping` 在 2026-07-28 已被删除。

### 2.2 Tools
| 方法 | 用途 |
|------|------|
| `await client.list_tools(cursor=None)` | 列出工具，可分页 |
| `await client.call_tool(name, arguments, read_timeout_seconds=None, progress_callback=None, ...)` | 调用工具；Server 返回「需要更多输入」时，会自动用回调回答并重试 |

### 2.3 Resources
| 方法 | 用途 |
|------|------|
| `await client.list_resources(cursor=None)` | 列出具体的 Resource |
| `await client.list_resource_templates(cursor=None)` | 列出 Resource 模板 |
| `await client.read_resource(uri)` | 读取 Resource（`uri` 就是 `str`） |
| `client.listen(...)` | 2026 版的订阅，见第 6 节（旧的 `subscribe_resource` / `unsubscribe_resource` 只对 2025 版连接有效，已弃用） |

### 2.4 Prompts 与补全
| 方法 | 用途 |
|------|------|
| `await client.list_prompts(cursor=None)` | 列出 prompt |
| `await client.get_prompt(name, arguments)` | 获取 prompt（参数是 `str → str`） |
| `await client.complete(ref, argument, context_arguments=None)` | 触发补全 |

> 协议层日志的 `set_logging_level` 已随 `logging/setLevel` 一起在 2026 版中删除。如果还想要日志，在构造时传 `log_level="info"`，每个请求会在 `_meta` 里带上它，Server 才会发日志通知。

---

## 3. 跑一遍看实际输出

下面是一个完整的 Client 示例，Server 和 Client 写在同一个文件里，进程内连接，不占端口。它依次演示：连接属性、`list_tools`、`call_tool` 返回的三个字段、资源、Prompt、补全和回调。

```python
import asyncio

from pydantic import BaseModel

from mcp import Client, MCPError
from mcp.client import ClientRequestContext
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import (
    Completion,
    CompletionArgument,
    CompletionContext,
    ElicitRequestParams,
    ElicitResult,
    PromptReference,
    ResourceTemplateReference,
    TextContent,
    TextResourceContents,
)

mcp = MCPServer("Bookshop", instructions="Search the catalog before recommending a book.")
GENRES = ["fiction", "non-fiction", "poetry"]


class Book(BaseModel):
    title: str
    author: str
    year: int


@mcp.tool(title="Search the catalog")
def search_books(query: str, limit: int = 10) -> str:
    """Search the catalog by title or author."""
    return f"Found 3 books matching {query!r} (showing up to {limit})."


@mcp.tool()
def lookup_book(title: str) -> Book:
    """Look up a book by its exact title."""
    if title != "Dune":
        raise ToolError(f"No book titled {title!r} in the catalog.")
    return Book(title="Dune", author="Frank Herbert", year=1965)


@mcp.resource("catalog://genres")
def genres() -> list[str]:
    """The genres the catalog is organised by."""
    return GENRES


@mcp.resource("catalog://genres/{genre}")
def books_in_genre(genre: str) -> str:
    """Every title we stock in one genre."""
    return f"3 books filed under {genre}."


@mcp.prompt(title="Recommend a book")
def recommend(genre: str) -> str:
    """Ask for a recommendation in a genre."""
    return f"Recommend one {genre} book from the catalog and say why."


@mcp.completion()
async def complete_genre(ref: PromptReference | ResourceTemplateReference, argument: CompletionArgument, context: CompletionContext | None) -> Completion | None:
    return Completion(values=[g for g in GENRES if g.startswith(argument.value)])


library = MCPServer("Library")


class CardHolder(BaseModel):
    name: str


@library.tool()
async def issue_card(ctx: Context) -> str:
    """Server 在工具执行中途主动向用户要输入（ctx.elicit，2025 风格）"""
    answer = await ctx.elicit("What name should go on the card?", schema=CardHolder)
    return f"Card issued to {answer.data.name}." if answer.action == "accept" else "No card issued."


async def main() -> None:
    async with Client(mcp) as client:
        # ---- 连接信息：进入 async with 时协商已完成 ----
        print("server_info     :", client.server_info.name)
        print("protocol_version:", client.protocol_version)
        print("instructions    :", client.instructions)
        print("capabilities    :", sorted(client.server_capabilities.model_dump(exclude_none=True)))

        # ---- list_tools ----
        for t in (await client.list_tools()).tools:
            print("tool            :", t.name, "|", t.title, "|", t.input_schema["required"])

        # ---- call_tool：content（给模型）/ structured_content（给代码）/ is_error ----
        r = await client.call_tool("lookup_book", {"title": "Dune"})
        print("content         :", [b.type for b in r.content], "| structured:", r.structured_content, "| is_error:", r.is_error)
        r = await client.call_tool("lookup_book", {"title": "Solaris"})
        print("ToolError       :", r.is_error, "|", r.content[0].text)
        r = await client.call_tool("does_not_exist", {})
        print("unknown tool    :", r.is_error, "|", r.content[0].text)  # 不是异常，也是 is_error=True

        # ---- 资源 ----
        print("resources       :", [x.uri for x in (await client.list_resources()).resources])
        print("templates       :", [x.uri_template for x in (await client.list_resource_templates()).resource_templates])
        for c in (await client.read_resource("catalog://genres/poetry")).contents:
            if isinstance(c, TextResourceContents):
                print("read            :", c.text)

        # ---- Prompt / 补全 ----
        p = await client.get_prompt("recommend", {"genre": "poetry"})
        print("prompt          :", p.messages[0].role, p.messages[0].content.text)
        c = await client.complete(PromptReference(name="recommend"), {"name": "genre", "value": "p"})
        print("complete        :", c.completion.values)

    # ---- 回调 = 能力声明：注册了 elicitation_callback，Client 才声明 elicitation ----
    async def on_elicit(context: ClientRequestContext, params: ElicitRequestParams) -> ElicitResult:
        print("callback        :", params.message)
        return ElicitResult(action="accept", content={"name": "Ada Lovelace"})

    # 老式「Server 主动问」（ctx.elicit）只在 legacy 连接上有效，所以这里 mode="legacy"
    async with Client(library, mode="legacy", elicitation_callback=on_elicit) as client:
        r = await client.call_tool("issue_card", {})
        print("legacy elicit   :", r.content[0].text)
    async with Client(library, mode="legacy") as client:  # 没注册回调 = 没声明能力
        try:
            await client.call_tool("issue_card", {})
        except MCPError as e:
            print("no callback     :", e.code, e)


if __name__ == "__main__":
    asyncio.run(main())
```

实际输出：

```
server_info     : Bookshop
protocol_version: 2026-07-28
instructions    : Search the catalog before recommending a book.
capabilities    : ['completions', 'prompts', 'resources', 'tools']
tool            : search_books | Search the catalog | ['query']
tool            : lookup_book | None | ['title']
content         : ['text'] | structured: {'title': 'Dune', 'author': 'Frank Herbert', 'year': 1965} | is_error: False
ToolError       : True | Error executing tool lookup_book: No book titled 'Solaris' in the catalog.
unknown tool    : True | Unknown tool: does_not_exist
resources       : ['catalog://genres']
templates       : ['catalog://genres/{genre}']
read            : 3 books filed under poetry.
prompt          : user Recommend one poetry book from the catalog and say why.
complete        : ['poetry']
callback        : What name should go on the card?
legacy elicit   : Card issued to Ada Lovelace.
no callback     : -32600 Elicitation not supported
```

### `call_tool` 返回的三个字段

| 字段 | 谁读 | 说明 |
|------|------|------|
| `content` | 模型 | 内容块列表：`TextContent` / `ImageContent` / `AudioContent` / `ResourceLink` / `EmbeddedResource`；读 `.text` 之前先用 `isinstance` 收窄类型 |
| `structured_content` | 你的代码 | 工具返回值的 JSON，符合声明的 `output_schema`，不用自己解析字符串 |
| `is_error` | 判断成败 | 工具失败（`ToolError`、崩溃、参数校验失败，甚至工具不存在）都是正常返回、`is_error=True`；先检查它，再相信 `structured_content` |

只有 Server 回的是 JSON-RPC error 时，`Client` 的方法才会抛出 `MCPError`（见[错误处理与参数校验](../02-server/08-errors-validation.md)）。

### 给 UI 选显示名
`title` 是可选字段，面向用户的 UI 要自己做兜底。可以用 `from mcp.shared.metadata_utils import get_display_name`：先取 `title`，没有再用 `name`，对 tool、resource、template、prompt 都适用。

---

## 4. 回调：回答 Server 需要你提供的信息

2026-07-28 之后，Server 不再主动给 Client 发请求，而是在工具结果里说明还需要哪些信息（`InputRequiredResult`）。`Client` 用你注册的回调来回答，然后自动重试原来的调用（详见[Sampling 让 Server 借用 Host 的 LLM（已弃用）](03-sampling.md) 和 [Elicitation、Roots 与多轮请求（MRTR）](04-roots-elicitation.md)）。你要做的只是注册回调：

| 传给 `Client(...)` 的参数 | 回答什么 | 同时声明的 capability |
|---|---|---|
| `elicitation_callback=` | 向用户要输入（表单 / URL） | `elicitation: {form, url}` |
| `sampling_callback=` | 用你的模型做补全（已弃用） | `sampling` |
| `list_roots_callback=` | 工作目录（已弃用） | `roots` |
| 都不传 | 无 | `{}` |

**注册回调就等于声明能力。** 没注册就是没声明，Server 想追问就会失败。在 2026 版里，SDK 不会向你发请求，Server 本该先用 `check_client_capability` 检查再提问；在 2025 版（`mode="legacy"`）里，表现就是上面输出的最后一行 `-32600 Elicitation not supported`。

还有两个回调只接收通知，不声明能力：

```python
# 协议层日志（已弃用）。2026 版里还要给 Client 传 log_level="info"，Server 才会发
async def on_log(params):  # LoggingMessageNotificationParams: level / logger / data
    print(f"[{params.level}/{params.logger}] {params.data}")

# 兜底：Server 的每条通知都会到这里（notifications/cancelled 由 SDK 自己处理，不会到）
from mcp.client import IncomingMessage
async def on_any(message: IncomingMessage):
    if isinstance(message, Exception):   # 传输层异常也会到这里
        raise message
    print("通知:", message.method)

async with Client(url, logging_callback=on_log, message_handler=on_any, log_level="info") as client: ...
```

> 进度回调不挂在 `Client` 上，而是每次调用 `call_tool` 时单独传：`progress_callback=on_progress`（签名为 `async (progress, total, message)`）。

---

## 5. 给 Agent 框架提供工具：最小 ReAct Agent

下面把 MCP Server 暴露的工具交给大模型，写一个最小的对话循环。Claude Code、Cursor 内部做的也是这件事：拉取工具，交给模型，调用工具，把结果放回对话，如此循环。例子用的是 OpenAI 兼容接口（默认千问 `qwen-flash`，换成别的 OpenAI 兼容服务只要改环境变量）。MCP 的 `tool.input_schema` 本身就是 JSON Schema，原样放进 `parameters` 就行：

```python
# demos/client/01_minimal_agent.py
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from openai import AsyncOpenAI

from mcp import Client, StdioServerParameters
from mcp.types import TextContent

SERVER_PATH = Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py"
MODEL = os.environ.get("QWEN_MODEL", "qwen-flash")


async def main(question: str) -> None:
    llm = AsyncOpenAI()  # 读 OPENAI_API_KEY / OPENAI_BASE_URL
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER_PATH)])

    async with Client(params) as mcp_client:
        # 1. 从 MCP 拉所有工具，转成 OpenAI 的 tools 格式（input_schema 原样就是 JSON Schema）
        mcp_tools = (await mcp_client.list_tools()).tools
        tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema}}
            for t in mcp_tools
        ]

        messages: list[dict] = [{"role": "user", "content": question}]
        for _ in range(6):  # 最多 6 轮，防死循环
            resp = await llm.chat.completions.create(model=MODEL, messages=messages, tools=tools)
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                print(f"\n[模型]: {msg.content}")
                return

            # 2. 执行所有 tool_call，把结果塞回对话
            for call in msg.tool_calls:
                args = json.loads(call.function.arguments or "{}")
                print(f"[Tool] {call.function.name}({args})")
                result = await mcp_client.call_tool(call.function.name, args)
                text = "\n".join(c.text for c in result.content if isinstance(c, TextContent))
                if result.is_error:
                    text = f"[工具报错] {text}"  # 让模型看到并自我纠正
                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})


if __name__ == "__main__":
    asyncio.run(main("现在纽约几点？再算一下 7 加 8。"))
```

```bash
export OPENAI_API_KEY=...        # 不要写进代码
export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export QWEN_MODEL=qwen-flash
python demos/client/01_minimal_agent.py
```

输出示例（模型回复的措辞每次会不同）：

```
[Tool] current_time({'timezone': 'America/New_York'})
[Tool] add({'a': 7, 'b': 8})

[模型]: 现在纽约的时间是 2026 年 11 月 2 日，上午 11:28（EST，UTC-5）。
7 加 8 等于 15。
```

---

## 6. 监听变更：`client.listen()`

2025 版用 `subscribe_resource()` 加 `message_handler`；2026-07-28 改用 `subscriptions/listen`，SDK 把它封装成了一个异步上下文管理器：

```python
from mcp.client.subscriptions import ResourceUpdated, ToolsListChanged

async with Client("http://localhost:8000/mcp") as client:
    async with client.listen(tools_list_changed=True, resource_subscriptions=["metrics://qps"]) as sub:
        print("Server 同意的订阅:", sub.honored)
        async for event in sub:
            match event:
                case ResourceUpdated(uri=uri):
                    print("重新拉", uri, await client.read_resource(uri))
                case ToolsListChanged():
                    print("工具变了", [t.name for t in (await client.list_tools()).tools])
```

事件只是提示「该重新读了」，不带内容。流断了就是断了，要重新 listen，订阅流不是重放日志。可运行的 Server + Client 例子见 `demos/server/02_resources_subscribe.py`。

---

## 7. 超时、重试与取消

```python
from mcp import MCPError

# 单次调用超时：read_timeout_seconds 是 float 秒（v1 是 timedelta）
try:
    await client.call_tool("slow", {}, read_timeout_seconds=10)
except MCPError as e:
    if e.code == -32001:          # REQUEST_TIMEOUT（v1 是 408）
        print("调用超时，SDK 已发 notifications/cancelled，Server 端的处理函数也会被真正取消")

# 重试只读操作
async def list_with_retry(client, max_attempts=3):
    for i in range(max_attempts):
        try:
            return await client.list_tools()
        except Exception as e:
            if i == max_attempts - 1:
                raise
            await asyncio.sleep(2 ** i)
```

> 写操作不要自动重试，除非 Server 支持幂等键。2026-07-28 删除了 SSE 断点续传，流一断请求就丢了，必须用新的请求 ID 重发，所以写操作更需要幂等键。

### 响应缓存

2026-07-28 要求 `tools/list`、`resources/list`、`prompts/list`、`resources/read` 等结果带上 `ttlMs` 和 `cacheScope` 缓存提示。`Client` 内置了响应缓存，会遵守这些提示；对于不带提示、每次都发新请求的老 Server，行为完全不变。Server 端用 `MCPServer(cache_hints={...})` 按方法配置，详见官方 Caching 文档。

---

## 8. 资源管理

`async with Client(...)` 会保证：

- 退出时取消仍在执行的请求（SDK 自动发送 `notifications/cancelled`）
- 关闭传输层（stdio 下依次是关闭 stdin、等待退出、SIGTERM、SIGKILL）
- 释放内部的 task group

不要手动调用 `__aenter__`/`__aexit__` 来管理，那样无法保证清理完整。

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| 照着 v1 写 `ClientSession` + `await session.initialize()` | v2 里换成 `Client`；底层 `ClientSession` 的签名也变了（见迁移指南） |
| 字段还写成 camelCase（`result.isError`、`tool.inputSchema`） | v2 的 Python 属性全是 snake_case：`is_error`、`input_schema`、`structured_content`、`next_cursor` |
| `client.server_info` 是 `None` | 2026 版里身份是可选元数据；用 `mode="2026-07-28"` 固定版本、又没传 `prior_discover` 时也会是 `None` |
| 用 `Client(mcp)` 测试时，工具里的 `ctx.elicit()` 失败 | 进程内连接默认协商到 2026 版，没有反向通道；把提问改成 `Resolve(...)`，或者给测试 Client 传 `mode="legacy"` |
| 没注册回调，Server 却要追问 | 注册回调就是声明能力；没声明时，2025 连接会返回 `-32600`，2026 下 Server 不应该发起追问 |
| 回调里抛了异常 | 会导致这次调用失败；回调里用 try 包住，或者返回 `ErrorData(...)` 明确拒绝 |
| `call_tool` 返回大对象却只看 text | 用 `result.structured_content` 拿有类型的数据 |
| 把 `Client` 当长期对象复用 | 一次 `async with` 就是一个生命周期，要重连就新建一个 |
| 子进程读不到你的环境变量 | stdio 子进程只继承一个最小白名单（`HOME`/`PATH` 等），密钥要通过 `StdioServerParameters(env={...})` 显式传入 |

---

## 10. 下一步

- 传输细节（stdio / Streamable HTTP）：[传输方式 stdio、Streamable HTTP 与进程内](./02-transports.md)
- Sampling 反向回调：[Sampling 让 Server 借用 Host 的 LLM（已弃用）](./03-sampling.md)
- Roots 与 Elicitation（多轮请求 MRTR）：[Elicitation、Roots 与多轮请求（MRTR）](./04-roots-elicitation.md)
- 多 Server 聚合：[多 Server 聚合与客户端最佳实践](./05-multi-server-best-practices.md)

## 参考资料

- Python SDK The Client：https://py.sdk.modelcontextprotocol.io/client/
- Client callbacks：https://py.sdk.modelcontextprotocol.io/client/callbacks/
- Build a Client 教程：https://modelcontextprotocol.io/docs/2026-07-28/develop/build-client
