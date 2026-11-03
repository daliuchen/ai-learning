# MCP Integration 04：在 Pydantic AI 中使用 MCP

> Pydantic AI 原生支持 MCP，不需要额外的 adapter。2.x 把这部分 API 整个换掉了：旧的 `MCPServerStdio`、`MCPServerStreamableHTTP`、`MCPServerSSE` 三个类合并成了 `MCPToolset`（建立在独立的 `fastmcp` 包之上），另外新增了推荐使用的入口 `MCP` capability（`pydantic_ai.capabilities.MCP`），用一个开关就能在「本地连接」和「模型厂商原生 MCP」之间切换。

---

## 1. Pydantic AI 的 MCP 集成为什么更直接

Pydantic AI 是 Pydantic 团队做的 Agent 框架，主打类型安全。在它的设计里，MCP 是和其它工具来源平级的原语，而不是一个外挂。

两条路线对比一下：LangChain 是 MCP → adapter → LangChain Tool → Agent；Pydantic AI 则直接把 MCP 当作 `toolsets` 之一（或者一个 capability），类型信息一路保留。

```bash
pip install "pydantic-ai-slim[mcp,openai]"      # mcp extra 会装 FastMCP 4
```

> 版本说明（2026 年 11 月）：`pydantic-ai-slim` 2.64。它的 MCP 客户端不再直接依赖官方 `mcp` SDK，而是建立在独立的第三方包 `fastmcp`（4.1.x）之上，所以能和官方 `mcp` 2.x 共存。本篇 demo 的 Server 用官方 `mcp` 2.3 编写，Client 端用 Pydantic AI 加 FastMCP 4，端到端能跑通。FastMCP 4 基于 `httpx2`，传给 `MCPToolset` 的 `auth` / `http_client` 要用 `httpx2` 构造。

---

## 2. 推荐入口：`MCP` capability

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import MCP

agent = Agent(
    "openai:gpt-6.1-sol",     # 任意模型
    capabilities=[
        MCP("https://mcp.example.com/api"),                  # 默认在本地进程里连这个 MCP Server：凭据、hook、追踪都在你手里
        MCP("https://mcp.example.com/other", native=True),   # 优先用模型厂商的原生 MCP（厂商替你连）；不支持就回退到本地
    ],
)
```

- 第一个参数是 URL，它会同时启用本地回退，以及（`native=True` 时）厂商原生 MCP。不是 URL 的本地来源用 `local=` 传入，它接受任何 `MCPToolset` 能接受的输入（脚本路径、FastMCP 传输、`fastmcp.Client`、进程内的 FastMCP Server）
- `native=True, local=False` 表示严格只用厂商原生 MCP（这时不需要 `mcp` extra）
- 不写 `id=` 时，用 URL 的主机名作为 id（只取主机名，因为路径和查询参数里可能带着凭据）
- 工具前缀：`tool_prefix="docs"`，或者 `tool_prefix=True`（用 `id=` 作前缀，没传 `id` 会报 `UserError`）。目前默认不加前缀，计划在 v3 里改成默认加
- 按用户使用凭据：`authorization_token` / `headers` 可以传函数，每次 run 开始时用 `RunContext` 取当前用户的 token。这样一个 agent 可以服务所有用户，并发的 run 之间也不会共享会话
- 例如 `Agent(TestModel(), capabilities=[MCP("http://127.0.0.1:18900/mcp", id="hello", tool_prefix=True)])` 连到本手册的 HTTP Server，工具名变成了 `hello_add` / `hello_current_time`

---

## 3. 底层：`MCPToolset`

如果需要自己管理生命周期，或者多个 agent 共享一个 Server，又或者要用 capability 没有暴露的传输配置，就直接用 `MCPToolset`，通过 `toolsets=[...]` 传入：

| 第一个参数 | 传输 |
|-----------|------|
| URL 字符串 | Streamable HTTP（路径以 `/sse` 结尾时按 SSE 处理，已弃用） |
| 本地 Python / Node 脚本路径 | stdio（自动启动子进程） |
| FastMCP 传输对象（`StdioTransport` / `StreamableHttpTransport` / `SSETransport`） | 对应的传输；需要指定解释器、参数、环境变量时用它 |
| 已有的 `fastmcp.Client` | OAuth、工具变换等进阶配置 |
| 进程内的 `FastMCP` Server | 测试或单进程部署，没有网络往返 |

```python
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

toolset = MCPToolset(StdioTransport(command="python", args=["server.py"]), id="hello")
agent = Agent("openai:gpt-6.1-sol", toolsets=[toolset.prefixed("hello")])    # .prefixed：hello_add、hello_current_time
```

- `async with agent:` 会打开和关闭所有 `MCPToolset` 的连接（stdio 下会启动和停止子进程），让多次 `agent.run` 复用同一个连接；不写的话，每次 run 都会自动连接一次
- 共享的 `MCPToolset` 实例用同一个身份连接 Server；每个用户有各自凭据的情况，见官方文档的 "Per-user authentication"
- `.prefixed("weather")` 是所有 toolset 都有的方法，多个 Server 有同名工具时靠它区分
- 旧 API 对照：`MCPServerStdio("python", args=[...])` 换成 `MCPToolset(StdioTransport(command="python", args=[...]))`；`MCPServerStreamableHTTP(url=...)` 换成 `MCPToolset("https://...")`

---

## 4. 完整 demo（千问 qwen-flash）

```python
# demos/integration/04_pydantic_ai_mcp.py
import asyncio
import os
import sys
from pathlib import Path

from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

SERVER = Path(__file__).resolve().parents[1] / "basics" / "06_first_server.py"


def qwen() -> OpenAIChatModel:
    return OpenAIChatModel(
        os.environ.get("QWEN_MODEL", "qwen-flash"),
        provider=OpenAIProvider(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"]),
    )


async def main() -> None:
    # 1) MCPToolset：第一个参数可以是 URL / 脚本路径 / FastMCP transport / fastmcp.Client / 进程内 FastMCP Server
    #    脚本路径会直接用 stdio 启动；要指定解释器、参数、环境变量就用 StdioTransport
    toolset = MCPToolset(StdioTransport(command=sys.executable, args=[str(SERVER)]), id="hello")
    # .prefixed("hello")：给所有工具名加前缀，避免多 Server 同名冲突 → hello_add / hello_current_time
    agent = Agent(qwen(), toolsets=[toolset.prefixed("hello")])

    async with agent:  # 打开/关闭所有 MCPToolset 的连接（stdio 会启动/停止子进程），复用同一连接
        result = await agent.run("现在纽约几点？再算一下 17 加 25。")
        print(result.output)
        # 查看这次运行里实际发生的工具调用
        for msg in result.all_messages():
            for part in getattr(msg, "parts", []):
                if part.part_kind == "tool-call":
                    print("  [tool-call]", part.tool_name, part.args)


if __name__ == "__main__":
    asyncio.run(main())
```

输出示例（模型回复的措辞每次会不同）：

```
现在纽约的时间是 2026 年 11 月 2 日 11:38:54（美国东部标准时间，EST-0500）。

17 加 25 等于 42。
  [tool-call] hello_current_time {"timezone": "America/New_York"}
  [tool-call] hello_add {"a": 17, "b": 25}
```

---

## 5. 类型安全：结构化输出加 MCP 工具

Pydantic AI 最大的特点是 typed output，从 MCP 工具拿到的数据，最后也会受到强类型约束：

```python
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

class TripPlan(BaseModel):
    destination: str
    flight: str
    hotel: str
    total_cost: float = Field(le=5000)         # 强制不超预算

agent = Agent(
    "openai:gpt-6.1-sol",
    output_type=TripPlan,
    toolsets=[
        MCPToolset("preferences_server.py").prefixed("pref"),
        MCPToolset("https://flight-api.com/mcp").prefixed("flight"),
        MCPToolset("https://hotel-api.com/mcp", tool_error_behavior="failed").prefixed("hotel"),
    ],
    instructions="先读用户偏好，再找航班和酒店，返回完整 TripPlan，total_cost 必须 <= 5000。",
)
result = await agent.run("规划去巴塞罗那 7 天")
print(result.output)       # TripPlan(...)
```

`tool_error_behavior` 决定 MCP Server 报告工具错误时怎么处理：

| 取值 | 行为 |
|------|------|
| `"retry"`（默认） | 抛出 `ModelRetry`，把 Server 的错误作为「需要重试」的工具结果交回模型，适合模型能自己纠正的错误（对应 Server 端的 `ToolError`） |
| `"failed"` | 抛出 `ToolFailed`，记录为 `outcome='failed'` 的工具结果，下一步由模型决定 |
| `"error"` | 让底层的 MCP 异常直接抛出，整个 run 失败，交给应用代码处理 |

在 `retry` 和 `failed` 两种模式下，结构化的错误内容会以 JSON 形式交给模型，所以「可以重试」之类的提示不会丢失。协议错误和传输错误不会被当作「已完成但失败的工具调用」上报。

> 这类接线可以先用 `TestModel` 离线检查（不调真实模型）：`TestModel` 会按 schema 生成合法的输出，`total_cost` 上的 `le=5000` 约束同样会校验。

---

## 6. Resource、Prompt 与 Server 指令

- Resource：`MCPToolset` 提供 `list_resources()`、`list_resource_templates()`、`read_resource(uri)`（文本返回 `str`，二进制返回 `BinaryContent`）和 `read_resource_for_app(uri)`。Resource 由应用驱动，Pydantic AI 不会自动把它注入上下文，需要你自己读出来，写进 instructions 或消息里。
- Server 指令：`MCPToolset(..., include_instructions=True)` 会把 Server 的 `instructions` 加进 agent 的指令。这个选项默认关闭，因为那段文本是 Server 写的，模型会把它当作和你写的指令同等权威的内容，所以只对信任的 Server 打开（`MCP` capability 为它构建的本地 toolset 默认是打开的）。
- 工具元数据：工具的 `meta` 和 `annotations` 放在工具定义的 `metadata` 里，可以用来过滤工具（`FilteredToolset`）。带有 MCP Apps 的 `_meta.ui.visibility` 且其中不含 `"model"` 的工具不会暴露给模型，这表示 Server 只想把它留给自己的 UI 用。
- 超时：每个 `MCPToolset` 可以单独设置 timeout，整个 run 也有总超时（见官方 Timeouts 页）。

---

## 7. Sampling 与 Elicitation（只对老协议连接有效）

`MCPToolset` 支持 `sampling_handler=`、`elicitation_handler=` 和 `roots`，对应 Server 发来的反向请求。但官方文档明确提醒，FastMCP 4 的「现代协议模式」不支持由 Server 发起的 sampling / elicitation（`MCPToolset` 配置了这些选项时会发出警告），这和 2026-07-28 删除所有 Server → Client 请求的变化是一致的。

这意味着：连接 2025 版的老 Server 时，handler 照常工作（需要 FastMCP 3，或者回退到老协议）；连接 2026 版 Server 并走现代模式时，Server 的追问要通过 MRTR（多轮请求），从文档的描述看，Pydantic AI 的 handler 机制覆盖不到这条路径。如果需要在 Agent 里可靠地处理 Server 的追问，目前比较稳的做法是用官方的 `mcp.Client`（它的回调两代协议都支持，见[Elicitation、Roots 与多轮请求（MRTR）](../03-client/04-roots-elicitation.md)）自己包一层工具，或者让 Server 使用 `Resolve`、由 Host 实现对应的回调。

---

## 8. 后台任务（Tasks）

`MCPToolset` 支持 MCP Tasks。对于 FastMCP 3 / SEP-1686（2025-11-25 的实验版）的 Server，它按工具的 `execution.taskSupport`（`required` / `optional` / `forbidden`）决定是否以任务模式调用，`prefer_tasks=False` 可以强制使用普通调用。FastMCP 4 用的是新的 Tasks 扩展（SEP-2663），由 Server 决定是否创建任务，上面说的 `task` 元数据和 `prefer_tasks` 对它不适用，普通调用会自动把任务驱动到完成。官方 `mcp` Python SDK 2.3 本身还没有实现 Tasks（见[用 Tasks 扩展异步执行长请求](../02-server/07-tasks.md)），所以用官方 SDK 写的 Server 不会走到这条路径。

---

## 9. 在 MCP Server 里使用 Pydantic AI Agent

反过来，也可以把 Agent 放进 MCP Server 里当作一个工具（Server 持有模型 Key，对外只暴露一个工具）：

```python
from mcp.server import MCPServer          # 官方 SDK 2.x（Pydantic AI 文档里的示例还停留在 v1 的 FastMCP 写法）
from pydantic_ai import Agent

server = MCPServer("Pydantic AI Server")
poet_agent = Agent("openai:gpt-6-luna", instructions="always reply in rhyme")

@server.tool()
async def poet(theme: str) -> str:
    """Poem generator"""
    r = await poet_agent.run(f"write a poem about {theme}")
    return r.output

if __name__ == "__main__":
    server.run()
```

[Sampling 让 Server 借用 Host 的 LLM（已弃用）](../03-client/03-sampling.md)一篇推荐用这种方式替代 Sampling：Server 直接持有并调用自己的 LLM。

---

## 10. 和 LangChain 路线对比

| 维度 | LangChain | Pydantic AI |
|------|-----------|-------------|
| 入口 | `langchain.mcp.MCPAdapter`（或老的 `langchain-mcp-adapters`） | `MCP` capability / `MCPToolset` |
| 底层 MCP 客户端 | FastMCP（新）/ 官方 `mcp` 1.x（老） | FastMCP 4 |
| 能否与官方 `mcp` 2.x 装在同一环境 | `MCPAdapter` 可以 / adapters 不行 | 可以 |
| 多 Server 同名工具 | `MCPConfig` 自动加 `<server>_` 前缀 | `.prefixed(...)` / `tool_prefix=` |
| 工具错误 | 抛 `ToolException`，由 agent 的工具错误处理决定 | `tool_error_behavior`：retry / failed / error |
| Elicitation | adapter 用 LangGraph 的 `interrupt()` 直接支持 | 只有老协议连接上的 handler |
| 类型化输出 | 靠 `with_structured_output` 或 agent 的结构化输出 | `output_type=` 原生支持 |
| 厂商原生 MCP | 无 | `MCP(url, native=True)` |

---

## 11. 调试与可观测性

Pydantic AI 自带 Logfire / OpenTelemetry 集成：

```python
import logfire
logfire.configure()
logfire.instrument_pydantic_ai()
# 现在 agent.run 会把模型请求、MCP 调用、工具执行都记录下来
```

官方的 MCP Server（`mcp` 2.x）默认也带 OpenTelemetry span，Client 和 Server 都用 SDK 时，trace 会串在一起（见[调试与可观测（Inspector、日志与追踪）](../05-production/05-debugging-inspector.md)）。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| `ImportError: cannot import name 'MCPServerStdio'` | 2.x 已经换成 `MCPToolset` 加 FastMCP 传输 |
| stdio Server 启动失败 | 用 `StdioTransport(command=sys.executable, args=[绝对路径])`；先单独运行一次 Server 看报错 |
| 工具没有出现 | Pydantic AI 默认加载全部工具；检查是不是被 `allowed_tools` 过滤了，或者 MCP Apps 的 `visibility` 把 `model` 排除了 |
| `output_type` 约束太严导致 LLM 失败 | 放宽约束或者补充 instructions；`ToolError` 对应的 `retry` 会让模型重试 |
| Sampling / Elicitation 不生效 | FastMCP 4 的现代模式不支持 Server 主动请求，见第 7 节 |
| Resource 没有注入 | 要手动 `read_resource`，再写进 instructions 或消息 |
| 多个 Server 有同名工具 | 用 `.prefixed(...)` 或 `tool_prefix=`；`MCP` capability 默认不加前缀，v3 才会改成默认加 |
| `include_instructions` 带来的提示注入风险 | Server 的 instructions 和你写的指令权威相同，只对可信的 Server 打开 |
| 自带的 httpx 客户端报类型错误 | FastMCP 4 用的是 `httpx2`，`auth` / `http_client` 要用 `httpx2` 构造 |

---

## 13. 相关手册

本系列的 [Pydantic AI 学习手册](../../../02-pydantic-ai/README.md) 是一本完整的 Pydantic AI 教程，其中的 [MCP（Model Context Protocol）集成](../../../02-pydantic-ai/docs/04-modules/01-mcp.md) 一篇同样以 2.64 为准，Server 端用 FastMCP 写；本篇的 Server 用官方 `mcp` 2.x 写。

---

## 14. 下一步

- 与 Function Calling、OpenAPI 的整体对比：[MCP 与 Function Calling、OpenAPI、LangChain Tools 的对比](./05-comparison.md)
- 远程部署 MCP Server：[用 Streamable HTTP 部署远程 MCP](../05-production/01-remote-mcp.md)
- [Pydantic AI 学习手册](../../../02-pydantic-ai/README.md)

## 参考资料

- Pydantic AI MCP 概览：https://pydantic.dev/docs/ai/mcp/overview/
- MCP capability：https://pydantic.dev/docs/ai/capabilities/mcp/
- MCP Client（`MCPToolset`）：https://pydantic.dev/docs/ai/mcp/client/
- Pydantic AI 源码：https://github.com/pydantic/pydantic-ai
