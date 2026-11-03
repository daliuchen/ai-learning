# MCP 集成：让 Agent 消费 MCP Server

> Agents SDK 用 `MCPServerStdio`、`MCPServerStreamableHttp`（或旧的 `MCPServerSse`）连上任意 MCP Server，Server 暴露的工具会自动变成 Agent 能调的工具；公网可达的服务还可以交给 OpenAI 代连（`HostedMCPTool`）。同一个 Server 实现，OpenAI、Claude Code、Cursor 都能用。
>
> stdio、Streamable HTTP、工具过滤、审批、Manager、Prompts 这几节的例子只需要本地起 MCP 服务，模型用 OpenAI 兼容端点（比如千问）即可。`HostedMCPTool` 依赖 OpenAI Responses，需要 OpenAI 账号。

---

## 1. 为什么要接 MCP

MCP 本身在 [03-mcp 手册](../../../03-mcp/README.md) 里讲过，这里只交代背景：MCP 是 Anthropic 提出的协议，规定 AI 应用怎么连外部工具和数据。一个 MCP Server 写一次，OpenAI Agents、Claude Code、Cursor、LangChain 都能接，比 LangChain 的 community tools 那种各写各的适配更统一。

官方给的接法选择：

| 需求 | 用 |
|------|----|
| 让 OpenAI 的 Responses API 替你调一个公网可达的 MCP 服务 | `HostedMCPTool`（hosted，见第 10 节） |
| 连你自己跑的（本地或远程）Streamable HTTP 服务 | `MCPServerStreamableHttp` |
| 连只支持 SSE 的旧服务 | `MCPServerSse`（MCP 项目已弃用 SSE 传输，新项目别用） |
| 本地起一个子进程，走 stdin/stdout | `MCPServerStdio` |

官方也提醒了安全问题：MCP 工具能暴露数据，也能拿你给的凭证去做事。所以只连信任的服务，凭证给最小权限，token 放 headers 而不是 URL，敏感操作加审批。

---

## 2. 两个 MCP Python SDK 版本

`openai-agents` 依赖 `mcp>=1.19.0,<3`，v1 和 v2 都支持，stdio / SSE / Streamable HTTP 的连接由 SDK 自动适配，客户端配置不用改。麻烦在服务端：写 MCP 服务的代码在两个版本里不一样（下面的例子用 `mcp 2.3.0`）：

```python
# mcp v1
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("demo")

# mcp v2：FastMCP 改名 MCPServer，模块也变了
from mcp.server.mcpserver import MCPServer
mcp = MCPServer("demo")
# 装饰器 @mcp.tool() / @mcp.prompt() / @mcp.resource() 用法不变
```

装了 v2 时，Agents SDK 会用 `mcp.Client(mode="auto")` 先发 `server/discover` 探测最新协议，老服务不认再回落到传统的 `initialize` 握手。自定义 HTTP（`params["auth"]`、`httpx_client_factory`）在 v1 用 `httpx`，在 v2 要用 `httpx2`。想钉死一个版本，就 `pip install "mcp>=1.19,<2"` 或 `"mcp>=2,<3"`。

---

## 3. Stdio：连本地 Server

先写一个服务端（mcp v2 写法）：

```python
# demo_mcp_server.py
from mcp.server.mcpserver import MCPServer      # v1 用: from mcp.server.fastmcp import FastMCP

mcp = MCPServer("demo")


@mcp.tool()
def query_table(table: str) -> str:
    """从内部 DB 查表"""
    return f"data from {table}: row1, row2"


if __name__ == "__main__":
    mcp.run()          # 默认 stdio
```

在 Agent 里用它：

```python
import asyncio
from agents import Agent, Runner
from agents.mcp import MCPServerStdio


async def main():
    async with MCPServerStdio(
        params={"command": "python", "args": ["demo_mcp_server.py"]},
        cache_tools_list=True,
    ) as server:
        agent = Agent(
            name="A",
            instructions="用 mcp tools 回答",
            mcp_servers=[server],
            model="gpt-6-luna",
        )

        result = await Runner.run(agent, "查 users 表")
        print(result.final_output)


asyncio.run(main())
```

每次 run 开始时，SDK 会对每个 server 调一次 `list_tools()`，把拿到的工具加进 Agent。问 users 表里有什么时，模型会调用 `query_table`；hooks 里 `on_tool_end` 收到的 `result` 是 `{'type': 'text', 'text': 'data from users: row1, row2'}` 这样的 dict，tracing 里多出一个 `mcp_tools` span。

---

## 4. StreamableHttp：连远程

```python
import os
from agents.mcp import MCPServerStreamableHttp
from agents.model_settings import ModelSettings


async with MCPServerStreamableHttp(
    name="internal-kb",
    params={
        "url": "https://my-mcp.example.com/mcp",
        "headers": {"Authorization": f"Bearer {os.environ['MCP_TOKEN']}"},
        "timeout": 10,
    },
    cache_tools_list=True,
    max_retry_attempts=3,                # list_tools / call_tool 自动重试
    client_session_timeout_seconds=5,    # MCP ClientSession 读超时，默认 5 秒；None / 0 关闭
) as server:
    agent = Agent(name="A", mcp_servers=[server], model_settings=ModelSettings(tool_choice="required"))
    await Runner.run(agent, "...")
```

构造参数里还常用到：`use_structured_content`（优先用 `structured_content` 而非文本）、`retry_backoff_seconds_base/max`、`max_list_pages`、`tool_filter`、`require_approval`、`failure_error_function`、`tool_meta_resolver`、`tool_input_guardrails` / `tool_output_guardrails`。0.13 起 `MCPServerStreamableHttp` 还多了 `session_id`，断线重连或无状态 worker 可以靠它恢复会话。

想在本地试，可以起一个 Streamable HTTP 服务（mcp v2）：

```python
# mcp_http.py
import uvicorn
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("demo-http")

@mcp.tool()
def query_table(table: str) -> str:
    """查表"""
    return f"data from {table}: row1, row2"

uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=18765)   # 端点路径是 /mcp
```

客户端写 `params={"url": "http://127.0.0.1:18765/mcp"}` 即可。实际场景里，这种方式对应公司内部部署在 K8s 上的 MCP Server，或者从官方 MCP Registry 找到的第三方服务。

---

## 5. SSE（兼容老服务）

```python
from agents.mcp import MCPServerSse


async with MCPServerSse(params={"url": "https://my-mcp.example.com/sse"}) as server:
    ...
```

API 和 Streamable HTTP 一样，只是传输不同。MCP 项目已经弃用 SSE 传输，新项目用 StreamableHttp 或 stdio，SSE 留给已有部署。

---

## 6. 多个 MCP Server：`MCPServerManager`

```python
from agents.mcp import MCPServerManager, MCPServerStreamableHttp

servers = [
    MCPServerStreamableHttp(name="calendar", params={"url": "http://localhost:8000/mcp"}),
    MCPServerStreamableHttp(name="docs", params={"url": "http://localhost:8001/mcp"}),
]

async with MCPServerManager(servers) as manager:
    agent = Agent(name="A", mcp_servers=manager.active_servers)     # 只含连接成功的
```

拿一个正常、一个连不上（`:9`）的组合来看：`manager.active_servers == [ok]`，`manager.failed_servers == [bad]`，run 照常进行。设 `strict=True` 则第一个失败就抛异常；`reconnect(failed_only=True)` 只重试失败的那些。连接和清理的超时默认都是 10 秒（`connect_timeout_seconds` / `cleanup_timeout_seconds`），0.20 起生命周期操作是串行执行的。

和手写两层 `async with ... , ...` 相比，它的好处是某个 MCP 挂了不会拖垮整个 Agent。多个 Server 要留意工具重名，Server 端最好起有区分度的名字（`fs_read`、`db_query`）；SDK 这边也能用 `RunConfig(tool_name_collision_policy=...)` 让冲突时警告或报错。

---

## 7. MCP Server 的 Prompts / Resources

除了 Tools，MCP 还有两个原语：

| 原语 | OpenAI Agents SDK 怎么用 |
|------|--------------------------|
| Tools | 自动接到 Agent（`mcp_servers=`） |
| Resources | `await server.list_resources()` / `list_resource_templates()` / `read_resource(uri)`（0.13 起）手动用；分页需自己传 `cursor` |
| Prompts | `await server.list_prompts()` + `await server.get_prompt(name, args)` 手动用（工具列表 / prompts 列表的分页 SDK 会自动跟随 `nextCursor`） |

```python
async with MCPServerStdio(...) as server:
    # prompts：用服务端模板动态生成 instructions
    prompt = await server.get_prompt("review_instructions", {"focus": "安全"})
    instructions = prompt.messages[0].content.text
    agent = Agent(name="Reviewer", instructions=instructions, mcp_servers=[server])

    # resources
    resources = await server.list_resources()
    content = await server.read_resource("config://app.json")
```

---

## 8. 缓存与失效

```python
async with MCPServerStdio(params={...}, cache_tools_list=True) as server:   # 默认 False
    ...
    await server.invalidate_tools_cache()      # 工具集变了，强制下次重新拉
```

不开缓存时，每次 run 都会对每个 server 调一次 `list_tools()`，远程服务能明显感到这点延迟。工具列表稳定的 Server 适合开 `cache_tools_list=True`。缓存里的工具定义是 detached 副本，改了也不会污染缓存。

---

## 9. 过滤、审批、守卫

静态过滤（屏蔽 `delete_table` 后，`list_tools()` 只剩 `query_table`）：

```python
from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter

server = MCPServerStreamableHttp(
    name="db", params={"url": "..."},
    tool_filter=create_static_tool_filter(allowed_tool_names=[...], blocked_tool_names=["delete_table"]),
)   # 先 allow 再 block
```

动态过滤则传一个 `async def f(context: ToolFilterContext, tool) -> bool`，`context` 里有 `run_context`、`agent`、`server_name`，可以按 agent 名或用户身份决定放行还是隐藏。

审批（`delete_table` 设为 `always` 后，run 暂停，调用出现在 `result.interruptions` 里）：

```python
MCPServerStreamableHttp(
    name="db", params={"url": "..."},
    require_approval={"always": {"tool_names": ["delete_table"]}},     # 也可 "always" / "never" / {"tool": "always"} / 回调
)
```

回调的签名是 `(run_context, agent, tool) -> bool`，能读 `tool.annotations`，但拿不到本次调用的参数，这一点和函数工具的 `needs_approval` 不同。服务端给的 annotations 只能当提示，别全信。批准或拒绝的流程按 [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](../02-tools/04-tool-choice.md) 里的 `RunState` 走。

工具守卫：`tool_input_guardrails=[...]` / `tool_output_guardrails=[...]` 会挂到过滤后留下的每个 MCP 工具上（见 [Tripwire 与异常处理](../04-guardrails/03-tripwire.md)），hosted MCP 不适用。

要给每次调用附带元数据，用 `tool_meta_resolver=lambda ctx: {"tenant_id": ...}`，它会往 MCP 请求的 `_meta` 里注入租户 ID、trace 上下文之类的信息。

---

## 10. Hosted MCP：让 OpenAI 代你连（需要 OpenAI 账号）

```python
from agents import Agent, HostedMCPTool, Runner

agent = Agent(
    name="Assistant",
    instructions="用 DeepWiki 查 openai/openai-agents-python",
    tools=[
        HostedMCPTool(tool_config={
            "type": "mcp",
            "server_label": "deepwiki",
            "server_url": "https://mcp.deepwiki.com/mcp",
            "require_approval": "never",      # 或 "always" + HostedMCPTool(on_approval_request=回调)
        })
    ],
)
```

这种方式下，列工具和调用的往返都在 OpenAI 的基础设施里完成，你的代码不再逐次介入。限制有几条：服务必须公网可达；只支持 OpenAI Responses 模型，千问这类不行。hosted MCP 也能 `defer_loading`，配合 `ToolSearchTool` 懒加载。另外还可以用 `connector_id` 接 OpenAI 的 connector（Google Drive 等）。

---

## 11. 错误处理

从 0.8 开始，本地 MCP 工具出错时，默认把一条错误信息返回给模型，整个 run 不会失败，所以不必再用 try/except 包住整个 run 来兜底。想要 fail-fast：

```python
# agent 级
agent = Agent(name="A", mcp_servers=[server], mcp_config={"failure_error_function": None})
# 注意：server 自己设了 failure_error_function 时，server 级的优先 → 每个 server 也要设 None
```

服务根本连不上（进入 `async with` 时就失败）仍然会抛异常。可以用第 6 节的 `MCPServerManager`，或者：

```python
try:
    async with MCPServerStreamableHttp(...) as server:
        ...
except Exception as e:
    log.error("MCP server unavailable", e=e)
    # fallback：不用 MCP server
    agent = Agent(...)
    result = await Runner.run(agent, "...")
```

---

## 12. 跟 LangChain MCP 对比

| 框架 | 怎么接 |
|------|--------|
| OpenAI Agents | `mcp_servers=[...]` 原生（本地）+ `HostedMCPTool`（OpenAI 代连） |
| LangChain | 1.4 起内置 `langchain.mcp.MCPAdapter`（旧的 `langchain-mcp-adapters` 包被替代） |
| Pydantic AI | 原生 MCP 客户端 toolset |
| Claude Code / Cursor | 配置文件直接连 |

Agents SDK 把 MCP 做成了 Agent 的构造参数 `mcp_servers=`，本地 MCP 工具可以直接用上它的审批、守卫、tracing 和 hooks，这是我觉得它比其他框架接得更顺的地方。

---

## 13. 跟 OpenAI Hosted Tools 混用

```python
from agents import Agent, WebSearchTool, FileSearchTool


async with MCPServerStreamableHttp(...) as mcp_server:
    agent = Agent(
        name="Hybrid",
        instructions="...",
        tools=[
            WebSearchTool(),           # OpenAI hosted（仅 Responses 模型）
            FileSearchTool(...),       # OpenAI hosted
        ],
        mcp_servers=[mcp_server],      # 公司内部 MCP
        model="gpt-6-luna",
    )
```

外部搜索走 web_search，内部数据走 MCP。前提是用 OpenAI Responses 模型，否则 hosted tools 用不了。

---

## 14. 完整 demo

```python
import asyncio
from agents import Agent, Runner
from agents.mcp import MCPServerStdio


async def main():
    # 配套 demo_mcp_server.py 见第 3 节
    async with MCPServerStdio(
        params={"command": "python", "args": ["demo_mcp_server.py"]},
    ) as server:

        agent = Agent(
            name="Bot",
            instructions="用 MCP server 提供的工具回答用户",
            mcp_servers=[server],
            model="gpt-6-luna",
        )

        result = await Runner.run(agent, "查 users 表数据")
        print(result.final_output)


asyncio.run(main())
```

配套的 `demo_mcp_server.py`：

```python
from mcp.server.mcpserver import MCPServer      # mcp v1 用 from mcp.server.fastmcp import FastMCP

mcp = MCPServer("demo")


@mcp.tool()
def query_table(table: str) -> str:
    """查表"""
    return f"data from {table}: row1, row2"


if __name__ == "__main__":
    mcp.run()
```

---

## 15. 下一步

- 接观测平台 → [与 LangSmith / Langfuse / Logfire 集成](./02-observability.md)
- 部署 FastAPI → [FastAPI / Lambda 部署](./03-fastapi-deploy.md)
- 完整 MCP 手册 → [MCP 手册](../../../03-mcp/README.md)
