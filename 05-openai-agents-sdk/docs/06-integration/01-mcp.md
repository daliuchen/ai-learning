# MCP 集成：让 Agent 消费 MCP Server

> **一句话**：通过 `MCPServerStdio` / `MCPServerSse` 连到任意 MCP Server，Server 暴露的 tools / resources / prompts 自动变成 Agent 可用工具——一处 Server 实现，OpenAI / Claude Code / Cursor 共享。

---

## 1. 为啥要接 MCP

跨手册关联：MCP 详见 [03-mcp 手册](../../../03-mcp/README.md)。

简言之：

- MCP = Anthropic 提出的"AI 与外部世界连接协议"
- 一个 MCP Server 写一次，OpenAI Agents / Claude Code / Cursor / LangChain 都能用
- 比 LangChain 的 community tools 更"协议化"

---

## 2. 三种 Transport

```python
from agents.mcp import MCPServerStdio, MCPServerSse, MCPServerStreamableHttp
```

| Transport | 适合 |
|-----------|------|
| Stdio | 本地工具（启个子进程） |
| SSE | 远程（旧版） |
| StreamableHttp | 远程（推荐，2025-11-25 规范） |

---

## 3. Stdio：连本地 Server

假设你写了一个 MCP Server（参考 [03-mcp/02-server](../../../03-mcp/docs/02-server/)）：

```python
# my_mcp_server.py - FastMCP server
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-tools")


@mcp.tool()
def get_internal_data(table: str) -> str:
    """从公司内部 DB 查"""
    return f"data from {table}"


if __name__ == "__main__":
    mcp.run()
```

在 Agent 里消费：

```python
import asyncio
from agents import Agent, Runner
from agents.mcp import MCPServerStdio


async def main():
    async with MCPServerStdio(
        params={
            "command": "python",
            "args": ["my_mcp_server.py"],
        },
    ) as server:
        agent = Agent(
            name="A",
            instructions="用 mcp tools 回答",
            mcp_servers=[server],
            model="gpt-4o-mini",
        )

        result = await Runner.run(agent, "查 users 表")
        print(result.final_output)


asyncio.run(main())
```

SDK 自动把 Server 的 tools 加到 Agent 的 tools 列表里。

---

## 4. StreamableHttp：连远程

```python
from agents.mcp import MCPServerStreamableHttp


async with MCPServerStreamableHttp(
    params={
        "url": "https://my-mcp.example.com/mcp",
        "headers": {"Authorization": "Bearer ..."},
    },
) as server:
    agent = Agent(name="A", mcp_servers=[server])
    await Runner.run(agent, "...")
```

适合：

- 公司内部 MCP Server 部署到 K8s
- 第三方提供的 MCP（Anthropic registry 等）

---

## 5. SSE（兼容旧版）

```python
from agents.mcp import MCPServerSse


async with MCPServerSse(
    params={"url": "https://my-mcp.example.com/sse"},
) as server:
    ...
```

新项目用 StreamableHttp，SSE 留给已有部署。

---

## 6. 多个 MCP Server

```python
async with MCPServerStdio(params={"command": "...", "args": [...]}) as fs_server, \
           MCPServerStdio(params={"command": "...", "args": [...]}) as db_server:

    agent = Agent(
        name="A",
        mcp_servers=[fs_server, db_server],
    )
```

两个 Server 的 tools 全暴露给 Agent。**注意 tool 名冲突**——MCP Server 应该用有特色的命名（`fs_read`, `db_query`）避免冲突。

---

## 7. MCP Server 的 Prompts / Resources

MCP 还有两个原语：

| 原语 | OpenAI Agents SDK 怎么用 |
|------|--------------------------|
| Tools | 自动接到 Agent.tools |
| Resources | 通过 `await server.list_resources()` + `await server.read_resource()` 手动用 |
| Prompts | 通过 `await server.get_prompt()` 手动用 |

```python
async with MCPServerStdio(...) as server:
    # 拿 resources
    resources = await server.list_resources()
    content = await server.read_resource("config://app.json")
    # ...

    # 拿 prompts
    prompts = await server.list_prompts()
    prompt = await server.get_prompt("greeting", arguments={"name": "Alice"})
    # 把 prompt content 拼到 instructions / user message
```

---

## 8. cache_tools_list：避免重复拉

```python
async with MCPServerStdio(
    params={"command": "..."},
    cache_tools_list=True,  # 默认 False
) as server:
    ...
```

`cache_tools_list=True` → Agent 启动时拉一次 tools list，之后复用。适合 tools 列表稳定的 Server。

⚠️ Server 重启 / 新增 tool 后要重连（或显式 invalidate）。

---

## 9. 实战：内部知识库 MCP

参考 [03-mcp/07-practice/01-project-internal-kb.md](../../../03-mcp/docs/07-practice/01-project-internal-kb.md)：

```python
# 1. 你写了 internal_kb MCP Server，部署到 k8s
# 2. OpenAI Agent 消费它


async with MCPServerStreamableHttp(
    params={
        "url": "https://kb-mcp.internal/mcp",
        "headers": {"Authorization": f"Bearer {os.getenv('KB_TOKEN')}"},
    },
) as kb_server:

    agent = Agent(
        name="EmployeeBot",
        instructions="""你是员工助手。
- 用 mcp tools 查知识库
- 引用文档来源
""",
        mcp_servers=[kb_server],
        model="gpt-4o-mini",
    )

    result = await Runner.run(agent, "公司年假怎么休？")
```

---

## 10. 跟 OpenAI Hosted Tools 混用

```python
from agents import Agent, WebSearchTool, FileSearchTool


async with MCPServerStreamableHttp(...) as mcp_server:
    agent = Agent(
        name="Hybrid",
        instructions="...",
        tools=[
            WebSearchTool(),           # OpenAI hosted
            FileSearchTool(...),       # OpenAI hosted
        ],
        mcp_servers=[mcp_server],      # 公司内部 MCP
        model="gpt-4o-mini",
    )
```

外部 web_search + 内部 MCP——两边都用。

---

## 11. 错误处理

MCP Server 挂了 / 网络断了：

```python
try:
    async with MCPServerStreamableHttp(...) as server:
        agent = Agent(mcp_servers=[server], ...)
        result = await Runner.run(agent, "...")
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
| OpenAI Agents | `mcp_servers=[...]` 原生 |
| LangChain | `langchain-mcp-adapters` 包，把 MCP tools 转成 LangChain Tool |
| Pydantic AI | 通过 `mcp` Python 库 + 手动桥接 |
| Claude Code / Cursor | 配置文件直接连 |

OpenAI Agents SDK 的 MCP 集成最**第一手**——`mcp_servers=` 参数。

---

## 13. 完整 demo

```python
# demos/integration/01_mcp.py
import asyncio
from agents import Agent, Runner
from agents.mcp import MCPServerStdio


async def main():
    # 假设你有一个 demo MCP server: demos/integration/demo_mcp_server.py
    async with MCPServerStdio(
        params={
            "command": "python",
            "args": ["demos/integration/demo_mcp_server.py"],
        },
    ) as server:

        agent = Agent(
            name="Bot",
            instructions="用 MCP server 提供的工具回答用户",
            mcp_servers=[server],
            model="gpt-4o-mini",
        )

        result = await Runner.run(agent, "查 users 表数据")
        print(result.final_output)


asyncio.run(main())
```

配套 `demo_mcp_server.py`：

```python
# demos/integration/demo_mcp_server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def query_table(table: str) -> str:
    return f"data from {table}: row1, row2"


if __name__ == "__main__":
    mcp.run()
```

---

## 14. 下一步

- 📖 接观测平台 → [02-observability.md](./02-observability.md)
- 📖 部署 FastAPI → [03-fastapi-deploy.md](./03-fastapi-deploy.md)
- 📖 完整 MCP 手册 → [03-mcp/README.md](../../../03-mcp/README.md)
