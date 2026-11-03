# MCP Client 05：多 Server 聚合与客户端最佳实践

> 实际的 Host 往往同时连着十几个 Server。这篇讲怎么并发管理多个 `Client`（以及官方的 `ClientSessionGroup`）、怎么解决工具重名、怎么做健康检查，最后介绍官方 Client Best Practices 里的两个重点：渐进式工具发现（progressive discovery）和程序化工具调用。

---

## 1. 多 Server 架构

Host 一般这样组织：

```
┌─────────────────────── Host ──────────────────────┐
│                                                    │
│  ConnectionManager（一个）                          │
│   ├── Connection 1 → MCP Server A                  │
│   ├── Connection 2 → MCP Server B                  │
│   └── Connection 3 → MCP Server C                  │
│                                                    │
│  ToolRegistry（一个）                               │
│   └── 所有 Server 的工具合并，做 namespace 处理     │
│                                                    │
│  Approval / UI / 日志路由 → 标注来源是哪个 Server   │
└────────────────────────────────────────────────────┘
```

一个 `Client` 只对应一个 Server，要连多个就得自己管理多个 `Client`，或者用官方的 `ClientSessionGroup`。

---

## 2. 方案 A：自己聚合多个 `Client`

最容易踩的坑是 anyio 的取消作用域规则：`async with Client(...)` 在哪个 task 里进入，就必须在同一个 task 里退出。v1 时代常见的 `asyncio.gather(*(stack.enter_async_context(...)))` 写法，在这里会报 `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`。

正确的做法是每个 Server 一个常驻 task，由它自己 `async with Client(...)`，用 `anyio.Event` 通知「已就绪」并等待「停止」信号。这个结构同时解决了三个问题：N 个 Server 并发启动，启动时间从 N×T 降到 max(T)；某个 Server 起不来时只打印错误，不会拖垮整个 Host；`__aexit__` 里调 `stop.set()`，各个 task 在自己的上下文里退出，清理顺序不会乱。

完整可运行的代码见第 11 节 demo（`MultiServer` 类，大约 40 行）。核心骨架如下：

```python
async def _run_one(self, name, params, ready):
    try:
        async with Client(params) as client:      # 进入和退出都在这个 task 里
            self.clients[name] = client
            ready.set()
            await self._stop.wait()               # 活到 __aexit__ 发出停止信号
    except Exception as e:
        print(f"❌ {name} 启动失败: {type(e).__name__}")
        ready.set()
```

合并工具、路由调用：

```python
async def list_tools(self):
    out = []
    for server, client in self.clients.items():
        for t in (await client.list_tools()).tools:
            out.append({"name": f"{server}__{t.name}",                              # 命名空间
                        "description": f"[Server: {server}] {t.description or ''}",  # 描述带来源
                        "input_schema": t.input_schema})                             # v2：snake_case
    return out

async def call_tool(self, namespaced, arguments):
    server, original = namespaced.split("__", 1)
    return await self.clients[server].call_tool(original, arguments)
```

---

## 3. 方案 B：官方 `ClientSessionGroup`

```python
from mcp import ClientSessionGroup, StdioServerParameters
from mcp.types import Implementation

def by_server(name: str, server_info: Implementation) -> str:
    return f"{server_info.name}.{name}"

async with ClientSessionGroup(component_name_hook=by_server) as group:
    await group.connect_to_server(StdioServerParameters(command="python", args=["library_server.py"]))
    await group.connect_to_server(StdioServerParameters(command="python", args=["web_server.py"]))
    print(sorted(group.tools))                       # ['Library.search', 'Web.search']
    result = await group.call_tool("Web.search", {"query": "mcp"})
```

- `group.tools` / `group.resources` / `group.prompts` 都是 `dict[str, ...]`；`group.call_tool(name, arguments)` 会自动路由到拥有这个工具的 Server
- `connect_to_server` 接收的是传输参数（`StdioServerParameters`、`StreamableHttpParameters`、`SseServerParameters`），而不是 `Client` 能接收的 URL 字符串
- 名字在整个 group 里必须唯一。两个 Server 都有 `search` 时会抛 `MCPError: {'search'} already exist in group tools.`。可以用 `component_name_hook` 改字典的键（线上调用时用的仍是原始的 `Tool.name`，前缀不会传出进程）。这个钩子对每个名字都生效，没有「只在冲突时才加前缀」的模式
- 已经有一个连好的会话，想并进 group，用 `connect_with_session(server_info, session)`；2026 版连接上 `client.server_info` 可能是 `None`，这时要自己给一个 `Implementation(name=..., version=...)`
- `disconnect_from_server(session)` 会移除这个 Server 的所有组件
- 要注意，它底层用的是 `ClientSession`，走经典的 `initialize` 握手，不会发 `server/discover`。兼容性没问题，只是对支持新路径的 Server 会稍慢一点；同时它也没有 2026 版多轮请求自动重试这些 `Client` 级的能力。需要这些就用方案 A

---

## 4. 跨 Server 工具重名

两个 Server 都有 `search` 时，必须加命名空间：

```
github__search
db__search
filesystem__search
```

常见的做法有三种：前缀 `{server_name}__{tool_name}`（Claude Code 在前面再加一段 `mcp__`，即 `mcp__{server_name}__{tool_name}`）；后缀 `{tool_name}_{server_name}`；或者在 UI 上按 Server 分组显示，传给 LLM 时用原始名字并附上 Server 信息。

> 工具描述里也要写明来源，让 LLM 分得清这个 search 是 GitHub 的还是数据库的。有些模型 API 对工具名的字符集和长度有限制（通常是 `[a-zA-Z0-9_-]`，不超过 64 个字符），所以分隔符不要用 `.` 或 `:`。上面 `ClientSessionGroup` 示例里用 `.` 做前缀，只适合内部路由，传给模型之前要转换。

---

## 5. 健康检查与重连

2026-07-28 删掉了 `ping`，不能再用 `session.send_ping()` 探活了。替代做法：

```python
import anyio
from mcp import MCPError

async def health_check(self):
    """定期探测所有 Server；用便宜的只读请求（比如 list_tools，有 ttlMs 缓存时更便宜）"""
    for name, client in list(self.clients.items()):
        try:
            with anyio.fail_after(2):
                await client.list_tools()
        except (TimeoutError, MCPError, Exception):
            print(f"Server {name} 无响应，标记为 dead")
            self.clients.pop(name, None)
            # 可选：用新的 Client 重连（用完的 Client 不能复用）
```

- 用过的 `Client` 不能复用，重连就是新建一个 Client：重新协商，重新 listen 订阅，清空已缓存的工具列表。想省掉协商往返，可以保存上一次的 `client.session.discover_result`，下次用 `Client(target, mode="2026-07-28", prior_discover=saved)`
- stdio 下子进程退出会直接抛错，比轮询更及时
- HTTP 下很多时候直接发请求就能发现问题。如果确实要主动探测，Server 端提供一个普通的 `/health` 端点更合适（见[用 Streamable HTTP 部署远程 MCP](../05-production/01-remote-mcp.md)）

---

## 6. 官方 Client Best Practices：渐进式发现与程序化调用

官方的 Client Best Practices（2026-07-28 版）讨论的是：当 Agent 连上很多 Server、累积了成百上千个工具之后，最直接的做法为什么会失效。把所有工具定义一次性放进上下文，光定义就要占掉十几万 token，延迟变高，选工具的准确率也会下降。文档给出了两种模式。

### 6.1 渐进式发现（Progressive Tool Discovery）

这种模式控制的是工具定义什么时候进入上下文。Host 照常调用 `tools/list`，但不马上把结果注入模型上下文，只给模型一个轻量的 `search_tools` 元工具；模型找到候选之后，再按需加载某个工具的完整定义。

通常分三层：Catalog 层的 `search_tools(query)` 只返回名字和一句简短描述；Inspect 层的 `get_tool_details(name)` 只返回这个工具的完整 schema；最后在 Execute 层带着完整的信息调用工具。

什么时候启用？工具定义占上下文窗口的比例超过某个阈值（官方建议 1%–5%）时再切换；工具不多时全量加载完全没问题。

检索方式可以用关键词（BM25 或正则，简单有效），可以用向量（对同义词更友好），可以交给子 Agent（用小模型挑工具，效果好但成本高），也可以混合使用。OpenAI、Anthropic 等模型厂商已经内置了 tool search，可以先看厂商文档。渐进式发现还能扩展到整个 Server 级别：维护一份 Server 注册表，用到时才连接。

### 6.2 程序化工具调用（Programmatic Tool Calling）

这种模式控制的是怎么调用工具：让模型在沙盒里写代码，批量调用多个工具、过滤和汇总结果，而不是每一步都经过一次模型往返，中间产生的大结果也不进入上下文。

### 6.3 依然适用的基本约定

1. 每个请求都带协议版本；HTTP 请求必须带 `MCP-Protocol-Version` 头（SDK 自动处理）
2. Cursor 原样传回，不解析也不修改
3. 调用工具前把输入展示给用户，防止恶意工具窃取数据；destructive 工具默认弹出审批，annotations 只作参考
4. 检查 `is_error`，决定要不要把结果反馈给模型
5. 限制 Resource 的大小，别把 100MB 的文档塞进上下文；对 Resource 内容做清洗（HTML、JS）
6. Elicitation 不能用来填密码，规范禁止，Host 也要拦截；如果还支持 Sampling（已弃用），默认要审批
7. 遵守 `ttlMs` / `cacheScope`：`Client` 已内置响应缓存，自己写 Host 时也别每次对话都重新拉 `tools/list`

---

## 7. 在工具描述里标明来源 Server

```python
def format_tool_for_llm(server_name: str, tool):
    return {
        "name": f"{server_name}__{tool.name}",
        "description": (
            f"[Server: {server_name}] {tool.description}" if tool.description else f"[Server: {server_name}]"
        ),
        "input_schema": tool.input_schema,    # v2：snake_case（v1 是 inputSchema）
    }
```

LLM 看到 `[Server: github]` 就知道这是 GitHub 的工具，不会搞混。

---

## 8. 面向用户的 UI 约定

可以参考 Claude Code 的做法：

| 元素 | 怎么做 |
|------|--------|
| 工具调用气泡 | 标明来源 Server 名、工具名和输入概要 |
| 失败提示 | 区分协议错误（`MCPError`，由 Host 处理）和工具错误（`is_error=True`，给 LLM 看） |
| 进度 | 用 `progress_callback` 实时刷新百分比 |
| 审批弹窗 | 写操作和 elicitation 默认弹窗，对信任的 Server 可以记住选择 |
| Resource 引用 | 用户可以在 UI 上挑选 resource 附加到对话里 |

---

## 9. 性能优化清单

| 优化 | 收益 |
|------|------|
| 并发启动 Server | Host 启动时间从 N×T 降到 max(T) |
| 按 `ttlMs` 缓存工具 schema | 不用每次对话都重新拉 `tools/list`（`Client` 已内置缓存） |
| 用 `subscriptions/listen` 增量更新 | 收到「工具列表变了」才重新拉取，不用轮询 |
| 渐进式工具发现 | 工具多到成百上千时，上下文 token 从十几万降到几千 |
| 大 Resource 用 resource_link | 避免一次性塞进 LLM 上下文 |
| 本地场景用 stdio 代替 HTTP | 省掉网络栈，延迟在毫秒级 |
| 保存并复用 `discover_result` | 重连时不需要协商往返 |

---

## 10. 调试技巧

| 工具 | 用法 |
|------|------|
| MCP Inspector | 调试单个 Server；新版有 `--cli`（适合脚本和 CI）和 `mcpdo`（连一次、发多条命令） |
| `message_handler` 兜底打印 | 遇到不认识的通知时，先全部打印出来 |
| `logging_callback`（协议日志已弃用） | 多 Server 时给日志加上 Server 标签；2026 版下要给 `Client` 传 `log_level` 才会有日志 |
| OpenTelemetry | SDK 默认带 span；Client 和 Server 都用 SDK 时，trace 会串在一起（见[调试与可观测（Inspector、日志与追踪）](../05-production/05-debugging-inspector.md)） |
| 抓 stderr 看 Server 日志 | stdio Server 的 stderr 是开发时最主要的信息来源 |

---

## 11. 多 Server 完整 demo

```python
# demos/client/05_multi_server.py
import asyncio
import sys

import anyio
import anyio.abc
from pathlib import Path

from mcp import Client, ClientSessionGroup, StdioServerParameters
from mcp.types import Implementation

DEMOS = Path(__file__).resolve().parents[1]
CONFIGS = {
    "hello": StdioServerParameters(command=sys.executable, args=[str(DEMOS / "basics" / "06_first_server.py")]),
    "booking": StdioServerParameters(command=sys.executable, args=[str(DEMOS / "server" / "08_errors_booking.py")]),
    "broken": StdioServerParameters(command=sys.executable, args=["-c", "import sys; sys.exit(1)"]),  # 故意起不来
}


class MultiServer:
    """方案 A：自己聚合——每个 Server 一个 Client。

    关键：anyio 要求「在哪个 task 里 enter，就必须在同一个 task 里 exit」。
    所以不能 asyncio.gather 里 enter、再在别处 exit（会报 'Attempted to exit cancel scope in a different task'）。
    正确做法：每个 Server 一个长驻 task，由它自己 `async with Client(...)`，用事件通知「就绪」并等待「停止」。
    """

    def __init__(self, configs: dict[str, StdioServerParameters]) -> None:
        self.configs = configs
        self.clients: dict[str, Client] = {}
        self._tg: anyio.abc.TaskGroup | None = None
        self._stop = anyio.Event()

    async def _run_one(self, name: str, params: StdioServerParameters, ready: anyio.Event) -> None:
        try:
            async with Client(params) as client:
                self.clients[name] = client
                ready.set()
                await self._stop.wait()  # 一直活到 __aexit__
        except Exception as e:  # noqa: BLE001  单个 Server 失败不拖垮整个 Host
            print(f"❌ {name} 启动失败: {type(e).__name__}")
            ready.set()

    async def __aenter__(self) -> "MultiServer":
        self._tg = anyio.create_task_group()
        await self._tg.__aenter__()
        readies = []
        for name, params in self.configs.items():  # 并发启动：全部先起 task，再统一等就绪
            ev = anyio.Event()
            readies.append(ev)
            self._tg.start_soon(self._run_one, name, params, ev)
        for ev in readies:
            await ev.wait()
        return self

    async def __aexit__(self, *exc) -> None:
        self._stop.set()  # 让各 task 在自己的上下文里退出 Client
        assert self._tg is not None
        await self._tg.__aexit__(*exc)

    async def list_tools(self) -> list[dict]:
        out = []
        for server, client in self.clients.items():
            for t in (await client.list_tools()).tools:
                out.append({
                    "name": f"{server}__{t.name}",  # 命名空间（Claude Code 的写法是 mcp__server__tool）
                    "description": f"[Server: {server}] {t.description or ''}",  # 描述里带来源，LLM 不会混淆
                    "input_schema": t.input_schema,
                })
        return out

    async def call_tool(self, namespaced: str, arguments: dict):
        server, original = namespaced.split("__", 1)
        return await self.clients[server].call_tool(original, arguments)


async def main() -> None:
    async with MultiServer(CONFIGS) as ms:
        print("✅ 已连接:", list(ms.clients))
        for t in await ms.list_tools():
            print(f"  {t['name']:28} {t['description'][:40].splitlines()[0]}")
        r = await ms.call_tool("hello__add", {"a": 2, "b": 3})
        print("hello__add(2,3) =", r.content[0].text)

    # 方案 B：官方 ClientSessionGroup（注意：它底层是 ClientSession，用经典 initialize 握手）
    def by_server(name: str, server_info: Implementation) -> str:
        return f"{server_info.name}.{name}"

    async with ClientSessionGroup(component_name_hook=by_server) as group:
        for name in ("hello", "booking"):
            await group.connect_to_server(CONFIGS[name])
        print("\nClientSessionGroup:", sorted(group.tools))
        r = await group.call_tool("hello-mcp.add", {"a": 40, "b": 2})
        print("group.call_tool  =", r.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
```

实际输出（`broken` 是故意设计成起不来的 Server，它被隔离开了）：

```
❌ broken 启动失败: ExceptionGroup
✅ 已连接: ['hello', 'booking']
  hello__add                   [Server: hello] 两个整数相加。
  hello__current_time          [Server: hello] 获取当前时间（按指定时区）。
  booking__book_flight         [Server: booking] 订机票：校验日期 / 用户 / 座位数；相同
  ...
hello__add(2,3) = 5

ClientSessionGroup: ['booking.admin_only', 'booking.book_flight', 'booking.buggy', 'hello-mcp.add', 'hello-mcp.current_time']
group.call_tool  = 42
```

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| 在 `gather` 里进入、在别处退出 | 会报 `RuntimeError: Attempted to exit cancel scope in a different task`；每个 Server 一个常驻 task，自己进入、自己退出 |
| 顺序启动拖慢 Host | 用 task group 并发启动，并隔离失败 |
| 一个 Server 启动失败拖垮整个 Host | 在 `_run_one` 里捕获异常，只标记这一个 Server |
| 不同 Server 有同名工具 | 强制加命名空间，工具描述里写明来源；`ClientSessionGroup` 遇到同名会直接抛 `MCPError` |
| 用 `send_ping()` 做健康检查 | `ping` 已移除；改用开销小的只读请求或 `/health` |
| 重连时复用旧的 `Client` | 用过的 `Client` 不能复用，新建一个 |
| 把所有工具定义一次性交给模型 | 工具多了就用渐进式发现 |
| 没监听列表变化 | Server 动态新增工具时 Host 不知道；用 `client.listen(tools_list_changed=True)` |
| 指望 `ClientSessionGroup` 自动处理 elicitation 重试 | 它走经典握手；需要 MRTR 自动循环请用 `Client` |

---

## 13. 下一步

Client 部分的 5 篇到此结束。下一章从[在 Claude Code 中接入 MCP](../04-integration/01-claude-code.md)开始，讲怎么把 MCP 接进 Claude Code、Cursor、LangChain、Pydantic AI 等。

## 参考资料

- Client Best Practices：https://modelcontextprotocol.io/docs/2026-07-28/develop/clients/client-best-practices
- SDK Session groups：https://py.sdk.modelcontextprotocol.io/client/session-groups/
- AnyIO 取消作用域：https://anyio.readthedocs.io/en/stable/cancellation.html
- Reference Servers（参考实现）：https://github.com/modelcontextprotocol/servers
