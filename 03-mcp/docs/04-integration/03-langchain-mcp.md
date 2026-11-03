# MCP Integration 03：在 LangChain 中使用 MCP

> 把 MCP Server 的工具变成 LangChain 工具，有两条路。一条是 LangChain 1.4 起内置的 `langchain.mcp.MCPAdapter`，它基于独立的 `fastmcp` 包，和官方 `mcp` 2.x SDK 兼容；另一条是老牌的 `langchain-mcp-adapters`（0.3.x），它依赖 `mcp<2`，装不进有 mcp 2.x 的环境。这篇先讲新路，再讲老路怎么和 v2 写的 Server 共存。

---

## 1. 为什么要把 MCP 接进 LangChain

写 MCP Server 不只是为了给 Claude Code 用。你可能还想在 LangChain Agent 里复用同一份工具，在 LangGraph 的多 Agent 工作流里调用某个外部能力，或者在生产服务里把公共能力只实现一次、到处使用。

无论哪种情况，方向都是从 MCP Server 到 LangChain Tool。

---

## 2. 先弄清版本关系

这是最容易出问题的地方：

| 包 | 当前版本 | 依赖 | 与 `mcp` 2.x |
|----|----------------|------|-------------|
| `mcp`（官方 SDK） | 2.3.0 | 无 | 不适用 |
| `fastmcp`（独立第三方包） | 4.1.5 | `mcp>=2.3,<3`（server/client extra） | 兼容 |
| `langchain` | 1.4.7 | `langchain.mcp.MCPAdapter` 需要 `pip install fastmcp` | 兼容 |
| `langchain-mcp-adapters` | 0.3.2 | `mcp>=1.24,<2` | 不能装在同一个环境里：pip 会把 `mcp` 降回 1.x；强行装 2.x 的话，`import langchain_mcp_adapters` 会报 `ImportError: cannot import name 'RequestContext' from 'mcp.shared.context'` |

所以：

- 新项目用 `langchain.mcp.MCPAdapter`（`pip install "langchain>=1.4" fastmcp`）
- 已有基于 `langchain-mcp-adapters` 的代码，就把它放在单独的环境里（它会带上 `mcp` 1.x）。协议层是互通的：1.x 的 Client 能连 2.x 写的 Server（Server 同时应答两代协议），Server 和 Client 不需要在同一个环境
- 想在同一个环境里同时用 `mcp` 2.x 和 LangChain 的 MCP 功能，只能走 `MCPAdapter`

---

## 3. 安装与最小代码（`MCPAdapter`）

```bash
pip install "langchain>=1.4" langchain-openai fastmcp "mcp>=2.3"
```

```python
# demos/integration/03_langchain_mcp.py
import asyncio
import os
from pathlib import Path

from langchain.agents import create_agent
from langchain.mcp import MCPAdapter
from langchain_openai import ChatOpenAI

SERVER = Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py"


async def main() -> None:
    # target 可以是：http(s) URL / 本地脚本 Path（走 stdio）/ fastmcp Client / MCPConfig / 进程内 Server
    # 注意：裸字符串只能是 URL，本地脚本必须写 Path(...)，防止配置里的字符串悄悄变成「启动子进程」
    async with MCPAdapter(SERVER) as adapter:
        tools = await adapter.list_tools()
        print("加载工具:", [t.name for t in tools])

        # 显式传 base_url：langchain-openai 会优先读 OPENAI_API_BASE（可能指向别的网关），别指望 OPENAI_BASE_URL 一定生效
        model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash"), base_url=os.environ["OPENAI_BASE_URL"])
        agent = create_agent(model, tools)
        result = await agent.ainvoke({"messages": [{"role": "user", "content": "现在纽约几点？再算一下 17 加 25。"}]})
        for m in result["messages"]:
            text = m.content if isinstance(m.content, str) else str(m.content)  # 工具消息的 content 可能是内容块列表
            print(f"[{m.type}]", text[:120].replace("\n", " "), getattr(m, "tool_calls", "") or "")


if __name__ == "__main__":
    asyncio.run(main())
```

输出示例（这里两个工具被并行调用，模型回复的措辞每次会不同）：

```
加载工具: ['add', 'current_time']
[human] 现在纽约几点？再算一下 17 加 25。
[ai]  [{'name': 'current_time', 'args': {'timezone': 'America/New_York'}, ...}, {'name': 'add', 'args': {'a': 17, 'b': 25}, ...}]
[tool] [{'type': 'text', 'text': '2026-11-02 11:37:46 EST-0500', ...}]
[tool] [{'type': 'text', 'text': '42', ...}]
[ai] 现在纽约的时间是 2026 年 11 月 2 日，11:37:46 EST（UTC-5）。   17 加 25 等于 42。
```

`MCPAdapter(target)` 做了这些事：

1. 用 FastMCP 建立连接，并协商协议版本（新旧 Server 都可以）
2. 调用 `list_tools()` 获取工具，把每个 MCP 工具转成异步的 LangChain `BaseTool`（`as_langchain_tool`）
3. 返回的工具持有 adapter 的 client，退出 `async with` 之后仍然可以调用
4. 默认支持 Elicitation：Server 中途需要用户输入时，adapter 通过 LangGraph 的 `interrupt()` 把问题交给人，人回答后继续执行（见 `langchain.mcp.elicitation`）；不用 elicitation 的 Server 不受影响
5. `list_tools(cache_mode="use" | "refresh" | "bypass")` 遵守 2026 版的 `ttlMs` 缓存提示

`target` 可以是：`http(s)` URL、本地脚本 `Path(...)`（走 stdio）、`fastmcp.Client`、`fastmcp` 的传输对象、`MCPConfig`（多 Server 配置，见下文）、进程内的 `FastMCP` 或官方 SDK 的 `MCPServer` 实例、`ClientGroup`。要注意的是，裸字符串只能是 URL，本地脚本必须写成 `Path("server.py")`。`fastmcp.Client` 本来会把恰好是文件名的字符串当作子进程启动，adapter 特意禁掉了这一点，防止来自配置、请求体甚至模型输出的字符串悄悄变成「执行本地文件」。

> 还有一个和 MCP 无关的坑：`langchain-openai` 会优先读取旧名字的环境变量 `OPENAI_API_BASE`，而不是 `OPENAI_BASE_URL`。如果你的环境里还残留着前者，请求会被发到别的网关并返回 401。demo 里直接显式传了 `base_url=`。

---

## 4. 多 Server：`MCPConfig`

```python
import sys
from langchain.mcp import MCPAdapter

cfg = {"mcpServers": {
    "hello":   {"command": sys.executable, "args": ["demos/basics/06_first_server.py"]},
    "booking": {"command": sys.executable, "args": ["demos/server/08_errors_booking.py"]},
    # 远程：{"url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer ..."}}
}}
async with MCPAdapter(cfg) as adapter:
    tools = await adapter.list_tools()
    print([t.name for t in tools])
    # ['hello_add', 'hello_current_time', 'booking_book_flight', 'booking_admin_only', 'booking_buggy']
```

多 Server 时，工具名会自动加上 `<server>_` 前缀（单下划线），不用自己处理重名。

---

## 5. 工具的转换规则

| MCP | LangChain Tool |
|-----|---------------|
| `name` | `name`（多 Server 时带前缀） |
| `description` | `description` |
| `input_schema`（线上是 `inputSchema`） | `args_schema` |
| `tools/call` | 异步 `coroutine`（`ainvoke`） |
| `result.is_error` | 抛出 `ToolException`（`MCPToolExecutionError`），消息交给模型；是否重试由 `create_agent` 的工具错误处理决定 |
| `result.content` | 内容块列表（`[{"type":"text","text":"42"}]`）加上结构化结果 `MCPToolArtifact`（artifact） |

工具消息的 `content` 是内容块列表，不是字符串，打印的时候要注意。

---

## 6. 老路：`langchain-mcp-adapters`（放在单独环境）

如果你已经有基于它的代码，或者需要它的 `load_mcp_resources` / `load_mcp_prompts` 这类辅助函数：

```bash
conda create -n lc-adapters python=3.12 -y && conda activate lc-adapters
pip install "langchain-mcp-adapters==0.3.2" langchain langchain-openai   # 会带 mcp 1.x
```

```python
from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "hello": {"transport": "stdio", "command": "/path/to/mcp2-env/bin/python", "args": ["06_first_server.py"]},
    "remote": {"transport": "http", "url": "http://localhost:8000/mcp"},   # 0.3.x 里 http 与 streamable_http 都可
})
tools = await client.get_tools()      # 默认工具名不带 server 前缀；同名冲突用 tool_name_prefix=True
agent = create_agent(model, tools)
```

这个组合可以用：客户端环境装 `langchain-mcp-adapters 0.3.2`（mcp 1.30），由它把另一个环境里用 `mcp` 2.3 写的 Server 作为子进程启动，两边能正常完成工具调用。完整文件在 `demos/integration/03_langchain_mcp_adapters_legacy.py`。

0.3.x 里还保留着这些老接口：

```python
from langchain_mcp_adapters.tools import load_mcp_tools            # 单 Server：已有 ClientSession 时
from langchain_mcp_adapters.resources import load_mcp_resources    # Resource → LangChain Blob
from langchain_mcp_adapters.prompts import load_mcp_prompts        # Prompt → LangChain 消息
```

> 这些代码跑在 mcp 1.x 上，里面的 `ClientSession` / `stdio_client` 是 v1 的写法；在 mcp 2.x 的环境里没有能直接替换的对应物。要读 Resource 或 Prompt，请直接用官方的 `Client`（`read_resource` / `get_prompt`，见[自己写 Client（连接、发现与调用工具）](../03-client/01-client-basics.md)），再自己转成 LangChain 对象。

---

## 7. 在 LangGraph 里使用

`create_agent` 内部就是 LangGraph。如果要自己搭图，把 `MCPAdapter.list_tools()` 返回的工具交给 `ToolNode` 就行：

```python
from langchain.agents import create_agent          # 多数场景直接用它
# 需要自定义图时：
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.prebuilt import ToolNode

async with MCPAdapter(cfg) as adapter:
    tools = await adapter.list_tools()
    model = ChatOpenAI(model="qwen-flash", base_url=...).bind_tools(tools)
    builder = StateGraph(MessagesState)
    builder.add_node("agent", lambda s: {"messages": [model.invoke(s["messages"])]})
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    # ... 条件边：有 tool_calls 就去 tools，否则结束
```

（旧的 `langgraph.prebuilt.create_react_agent` 已被 `langchain.agents.create_agent` 取代。）

---

## 8. 注意事项

### 8.1 异步与同步
MCP 工具全是 async 的，所以 LangChain 这边也要用 `ainvoke` / `astream`。

### 8.2 连接的生命周期
`MCPAdapter` 是一个异步上下文管理器：在 `async with` 里建立连接，`list_tools()` 返回的工具持有 client，退出之后仍然能用。生产环境里，把 adapter 的创建和工具加载放在应用启动阶段。

### 8.3 错误处理
MCP 的 `is_error=True` 会变成 `ToolException`，模型能看到；协议错误（`MCPError`）则会直接抛出，需要自己 catch。

### 8.4 stdio 子进程的环境
stdio 子进程只继承一个最小的环境变量白名单（HOME、PATH 等），Server 需要的密钥要通过 `env=` 显式传入。

---

## 9. 和直接写 LangChain `@tool` 比较

| 维度 | LangChain `@tool` | MCP Tool（通过 adapter） |
|------|-------------------|----------------------|
| 写一次能用在哪 | 只能在 LangChain 里用 | Claude Code、Cursor、LangChain 都能用 |
| 部署 | 同一进程 | 子进程或远程 HTTP |
| 类型安全 | 直接用 Pydantic | 经过 adapter 转换 |
| 适合 | 框架内部工具，耦合紧 | 公共能力，跨产品复用 |

我的建议是：只在内部用的工具写 `@tool`；想分发给多个产品、写一次到处用，就做成 MCP Server 再接 adapter。

---

## 10. 常见坑

| 坑 | 排查 |
|----|------|
| `pip install langchain-mcp-adapters` 把 mcp 降到了 1.x | 它依赖 `mcp<2`；需要 mcp 2.x 就用 `langchain.mcp.MCPAdapter`，或者把 adapters 放进单独的环境 |
| `ImportError: cannot import name 'RequestContext' from 'mcp.shared.context'` | 原因同上：adapters 0.3.x 和 mcp 2.x 不兼容 |
| `MCPAdapter("server.py")` 报「not a valid URL」 | 裸字符串只能是 http(s) URL，本地脚本要写成 `Path("server.py")` |
| 没装 fastmcp | 会报 `ImportError: Please install FastMCP to use MCPAdapter — pip install fastmcp` |
| `openai.AuthenticationError 401` | 检查是不是 `OPENAI_API_BASE` 把请求带到了别的网关；显式传 `base_url` |
| 工具消息的 `content` 不是字符串 | 它是内容块列表，取 `[0]["text"]` |
| 多个 Server 有同名工具 | `MCPConfig` 会自动加 `<server>_` 前缀；老的 adapters 要设 `tool_name_prefix=True` |
| stdio Server 启动慢 | 提前预热，或者调大连接超时 |
| 同步代码里混用异步 | 用 `await agent.ainvoke(...)` |
| 全局开着 LangSmith tracing，干扰测试 | 跑验证脚本前设置 `LANGCHAIN_TRACING_V2=false LANGSMITH_TRACING=false` |

---

## 11. 下一步

- Pydantic AI 接 MCP：[在 Pydantic AI 中使用 MCP](./04-pydantic-ai-mcp.md)
- 与 Function Calling、OpenAPI 的对比：[MCP 与 Function Calling、OpenAPI、LangChain Tools 的对比](./05-comparison.md)
- 另一本手册里关于 LangChain 的完整内容：[LangChain 全家桶系列教程](../../../01-langchain/README.md)

## 参考资料

- LangChain MCP 文档：https://docs.langchain.com/oss/python/langchain/mcp
- langchain-mcp-adapters：https://github.com/langchain-ai/langchain-mcp-adapters
- FastMCP（独立包）：https://gofastmcp.com
- LangGraph 文档：https://langchain-ai.github.io/langgraph/
