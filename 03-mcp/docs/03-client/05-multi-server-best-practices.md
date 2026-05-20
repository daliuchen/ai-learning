# MCP Client 05：多 Server 聚合 与 客户端最佳实践

> **一句话**：真实 Host 会同时连十几个 Server。本篇讲怎么并发管理多个 ClientSession、解决命名冲突、做生命周期监控、以及一组从 Anthropic 官方 Client Best Practices 总结出的工程约定。

---

## 1. 多 Server 架构

Host 通常这样组织：

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

---

## 2. 并发管理多个 ClientSession

最简版本：

```python
import asyncio
from contextlib import AsyncExitStack
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


class MultiServerClient:
    def __init__(self, configs: dict[str, dict]):
        # configs = {"github": {"command":..., "args":[...]}, "db": {...}}
        self.configs = configs
        self.sessions: dict[str, ClientSession] = {}
        self.stack = AsyncExitStack()

    async def __aenter__(self):
        await self.stack.__aenter__()
        for name, cfg in self.configs.items():
            params = StdioServerParameters(command=cfg["command"], args=cfg["args"])
            r, w = await self.stack.enter_async_context(stdio_client(params))
            session = await self.stack.enter_async_context(ClientSession(r, w))
            await session.initialize()
            self.sessions[name] = session
        return self

    async def __aexit__(self, *args):
        await self.stack.__aexit__(*args)

    async def list_all_tools(self):
        """合并所有 Server 工具，带命名空间"""
        all_tools = []
        for name, session in self.sessions.items():
            resp = await session.list_tools()
            for t in resp.tools:
                all_tools.append({
                    "server": name,
                    "name": f"{name}__{t.name}",
                    "original_name": t.name,
                    "description": t.description,
                    "input_schema": t.inputSchema,
                })
        return all_tools

    async def call_tool(self, namespaced_name: str, arguments: dict):
        """按命名空间路由"""
        server_name, original = namespaced_name.split("__", 1)
        session = self.sessions[server_name]
        return await session.call_tool(original, arguments)
```

用法：

```python
async with MultiServerClient({
    "github": {"command": "uvx", "args": ["mcp-server-github"]},
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
}) as client:
    tools = await client.list_all_tools()
    for t in tools:
        print(t["name"])

    result = await client.call_tool("github__search_code", {"q": "asyncio"})
```

---

## 3. 处理跨 Server 命名冲突

两个 Server 都有 `search` → 必须 namespace：

```
github__search
db__search
filesystem__search
```

实现方式：

- **前缀**：`{server_name}__{tool_name}` 是 Claude Code 用的约定
- **后缀**：`{tool_name}_{server_name}`
- **分组**：UI 上按 Server 分组显示，传给 LLM 时只用 original name + server context

> 工具描述里也要带 Server 来源，让 LLM 知道"这个 search 是 GitHub 的还是数据库的"。

---

## 4. 并发初始化（避免顺序串行卡死）

上面顺序启动 N 个 Server，如果某个慢就会拖整个 Host 启动。改成并发：

```python
async def __aenter__(self):
    await self.stack.__aenter__()

    async def start_one(name, cfg):
        params = StdioServerParameters(command=cfg["command"], args=cfg["args"])
        r, w = await self.stack.enter_async_context(stdio_client(params))
        session = await self.stack.enter_async_context(ClientSession(r, w))
        await session.initialize()
        return name, session

    results = await asyncio.gather(
        *(start_one(name, cfg) for name, cfg in self.configs.items()),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, Exception):
            print(f"Server 启动失败: {r}")
            continue
        name, session = r
        self.sessions[name] = session

    return self
```

返回 `return_exceptions=True` 让单个 Server 失败不拖垮其他。

---

## 5. 健康检查与重连

```python
import anyio

class MultiServerClient:
    async def health_check(self):
        """定期 ping 所有 Server，重启挂掉的"""
        for name, session in list(self.sessions.items()):
            try:
                with anyio.fail_after(2):
                    await session.send_ping()
            except Exception:
                print(f"Server {name} 无响应，标记为 dead")
                self.sessions.pop(name, None)
                # 可选：尝试重连
                await self._reconnect(name)
```

重连时要重新 initialize、重新订阅 Resource、清空已知工具 cache。

---

## 6. Anthropic 官方 Client Best Practices 摘要

来自 `docs/develop/clients/client-best-practices`，挑最重要的 10 条：

### 6.1 协议
1. **始终发 initialize / initialized**，不要复用上一次会话
2. **HTTP 必须带 `MCP-Protocol-Version`**
3. **Cursor 透传**，不解析、不修改

### 6.2 工具
4. **工具调用前给用户看输入**，防恶意工具偷数据
5. **destructive 工具默认弹审批**，annotations 仅作提示
6. **检查 isError** 决定是否给模型反馈

### 6.3 Resources
7. **限制 Resource 大小**，避免 100MB 的文档塞进 LLM 上下文
8. **Resource 内容做 sanitize**，特别是 HTML / JS

### 6.4 Server 反向请求
9. **Sampling 请求默认审批**，可对 trusted server 关闭
10. **Elicitation 不可填密码**——Spec 禁止，Host 也要拦

---

## 7. 给 LLM 工具描述加 server 来源

```python
def format_tool_for_llm(server_name: str, tool):
    return {
        "name": f"{server_name}__{tool.name}",
        "description": (
            f"[Server: {server_name}] {tool.description}"
            if tool.description else f"[Server: {server_name}]"
        ),
        "input_schema": tool.inputSchema,
    }
```

LLM 看到 `[Server: github]` 知道这是 GitHub 工具，不会混淆。

---

## 8. 用户感知的 UI 约定

UI 设计建议（参考 Claude Code）：

| 元素 | 怎么做 |
|------|--------|
| **工具调用气泡** | 标注来源 Server 名 + 工具名 + 输入概要 |
| **失败提示** | 区分协议错（Host 解析）vs 工具错（给 LLM 看） |
| **进度** | 进度通知实时刷新百分比 |
| **审批弹窗** | 写操作 / sampling / elicitation 默认弹，对信任 Server 可记忆 |
| **Resource 引用** | 用户能在 UI 上挑 resource 附到对话 |

---

## 9. 性能优化清单

| 优化 | 收益 |
|------|------|
| **并发 initialize** | Host 启动从 N×T 降到 max(T) |
| **工具 schema 缓存** | 不要每次对话都重新拉 tools/list |
| **list_changed 增量更新** | 收通知只更新 diff，不全量重拉 |
| **大 Resource 用 resource_link** | 避免一次性塞 LLM 上下文 |
| **stdio 替代 HTTP（本地）** | 省网络栈，毫秒级 |

---

## 10. 调试技巧

| 工具 | 用法 |
|------|------|
| **MCP Inspector** | 单 Server 调试 |
| **`logging_callback` 把所有日志聚合** | 多 Server 时知道是谁在说话 |
| **`message_handler` 兜底打印** | 不知道某个通知是啥时先打全 |
| **`MCP_DEBUG=1` 环境变量** | 部分 SDK 支持，开 SDK 内部 debug 日志 |
| **抓 stderr 看 Server 日志** | stdio Server 的 stderr 是开发者真正的眼睛 |

---

## 11. 多 Server 完整 demo

```python
# demos/client/05_multi_server.py
"""一次连两个 Server（hello-mcp + 我们自己写的另一个），并列举所有工具"""
import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

DEMOS = Path(__file__).resolve().parents[1]


async def start_server(stack, name, command, args):
    params = StdioServerParameters(command=command, args=args)
    r, w = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(ClientSession(r, w))
    await session.initialize()
    return name, session


async def main():
    async with AsyncExitStack() as stack:
        configs = [
            ("hello", "python", [str(DEMOS / "basics" / "06_first_server.py")]),
            # 假设你已经写了 08-errors-validation 的 demo
            # ("booking", "python", [str(DEMOS / "server" / "08_errors_booking.py")]),
        ]

        results = await asyncio.gather(
            *(start_server(stack, n, c, a) for n, c, a in configs),
            return_exceptions=True,
        )

        sessions = {}
        for r in results:
            if isinstance(r, Exception):
                print(f"❌ Server 启动失败: {r}")
                continue
            name, session = r
            sessions[name] = session
            print(f"✅ {name} 连接成功")

        print("\n=== 所有工具（带命名空间）===")
        for name, session in sessions.items():
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"  {name}__{t.name}: {(t.description or '').splitlines()[0][:60]}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **顺序 init 拖慢启动** | 用 asyncio.gather 并发 |
| **某个 Server 启动失败拖垮整个 Host** | 用 `return_exceptions=True` 隔离 |
| **跨 Server 工具同名** | 强制 namespace，工具描述带来源 |
| **关闭顺序错乱** | 用 AsyncExitStack 保证 LIFO 清理 |
| **日志混在一起** | 给 logging_callback 加 Server 标签 |
| **没监听 list_changed** | Server 动态新增工具时 Host 不知道，要在 message_handler 处理通知 |

---

## 13. 下一步

03-client 全部 5 篇结束。下一章进入 04-integration：把 MCP 接到 Claude Code、Cursor、LangChain、Pydantic AI 等。

## 参考资料

- Client Best Practices：https://modelcontextprotocol.io/docs/develop/clients/client-best-practices
- AsyncExitStack 文档：https://docs.python.org/3/library/contextlib.html#contextlib.AsyncExitStack
- Reference Servers（参考实现）：https://github.com/modelcontextprotocol/servers
