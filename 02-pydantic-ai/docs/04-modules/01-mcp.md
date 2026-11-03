# Pydantic AI 04-01：MCP（Model Context Protocol）集成

> MCP 是一套让 LLM 调用外部工具的统一协议。Pydantic AI 在两头都用得上：作为 client，Agent 通过 `MCPToolset` 或 `MCP` capability 调用别人的 MCP server；作为 server 一侧，可以在 FastMCP 写的 server 里调用 Agent，或者用 MCP sampling 借用 client 的模型。传输方式是 Stdio 和 Streamable HTTP，SSE 已经弃用。

以 `pydantic-ai 2.64.0` 为准。客户端从 `MCPServerStdio` / `MCPServerStreamableHTTP` / `MCPServerSSE` 换成了基于 FastMCP 的 `MCPToolset`；写 server 改用 `from fastmcp import FastMCP`。

---

## 1. 为什么会有 MCP

每接一个外部能力（读文件、查 GitHub、控制浏览器），都要在 Agent 代码里加一个 `@agent.tool`。工具一多，Agent 代码就变成了一个什么都往里放的工具仓库，而且这些工具没法复用：给 Pydantic AI 写的，换到 LangChain 或 Claude Code 还得再写一份。MCP 把工具从 Agent 里拆出来，定义了一套标准协议：任何 server 都可以发布 tool、resource、prompt，任何 client（Agent、IDE、CLI）用同一套协议接入。可以拿 LSP 和 IDE 的关系来类比。

## 2. MCP vs 普通 tool

| 维度 | `@agent.tool` 本地工具 | MCP 工具 |
|------|----------------------|---------|
| 部署方式 | 跟 Agent 同进程 | 独立进程（stdio）或独立服务（HTTP） |
| 跨语言 | 不行，必须 Python | 任何语言 |
| 跨框架复用 | 不行 | 可以，Claude Code / Cursor / 各种框架 |
| 发现机制 | 写死在代码 | Client 启动时 `list_tools()` 动态拉取 |
| 适合场景 | 紧耦合业务工具 | 通用能力（FS、Git、DB、Browser…） |

业务专属、轻量、和 deps 紧耦合的工具仍然用 `@agent.tool`；通用能力如果已经有现成的 MCP server，就别自己写。

---

## 3. Pydantic AI 作 Client：`MCPToolset`

```bash
pip install "pydantic-ai-slim[mcp]"      # 装 FastMCP 4（自己写 server 还要 pip install fastmcp）
```

`MCPToolset` 的第一个参数可以是：

- URL 字符串（Streamable HTTP；以 `/sse` 结尾时走已弃用的 SSE）
- 本地 Python / Node 脚本路径（走 stdio）
- FastMCP 的 transport（如 `StdioTransport`）
- 预先构造好的 `fastmcp.Client`
- 同一进程里的 FastMCP server 对象（用于测试或单进程部署，没有网络开销）

它本身就是一个 toolset，直接放进 `toolsets=[...]`：

```python
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

# stdio：子进程 + 双向管道
fs = MCPToolset(StdioTransport(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]))
# Streamable HTTP
calc = MCPToolset("http://localhost:8000/mcp")

agent = Agent("openai-chat:qwen-flash", toolsets=[fs, calc])

async def main() -> None:
    async with agent:                       # 进入时连接所有 MCP server，退出时关闭
        result = await agent.run("/tmp 下有哪些文件？")
        print(result.output)
```

### 3.1 更高层：`MCP` capability（官方推荐）

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import MCP

agent = Agent(
    "openai:gpt-6.1-sol",
    capabilities=[
        MCP("https://mcp.example.com/api"),                       # 本地连接（凭证、hooks、tracing 都在你手里）
        MCP("https://mcp.example.com/other", native=True),        # 让模型厂商去连；不支持就回退本地
        MCP(local=my_fastmcp_client),                             # 任意 MCPToolset 输入
    ],
)
```

不给 `id=` 时，capability 用 URL 的主机名当 id。多个 server 的工具重名时，用 `tool_prefix="weather"` 或 `tool_prefix=True`（以 `id` 作前缀）。如果每个用户要用自己的凭证，`authorization_token` / `headers` 可以传一个接收 `RunContext` 的函数，每次运行时现算。

### 3.2 三种传输方式怎么选

| 传输 | 何时用 | 优点 | 缺点 |
|------|-------|------|------|
| Stdio | 本机工具、个人开发 | 不用配网络、安全 | 单机、子进程模型 |
| Streamable HTTP | 团队 / 线上部署、跨机器 | 多客户端、可水平扩展 | 需要部署 |
| SSE | 兼容老 server | — | 已弃用 |

### 3.3 从配置文件加载

格式和 Claude Desktop / Claude Code / Cursor 用的 `mcpServers` JSON 一样：

```json
{
  "mcpServers": {
    "fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
    "calculator": {"url": "http://localhost:8000/mcp"},
    "weather-api": {"url": "https://${SERVER_HOST:-localhost}:${SERVER_PORT:-8080}/sse"}
  }
}
```

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import load_mcp_toolsets

agent = Agent("openai:gpt-6.1-sol", toolsets=load_mcp_toolsets("mcp_config.json"))
```

stdio 条目支持 `command / args / env / cwd`，HTTP 条目支持 `url / headers`，都可以用 `${VAR}`、`${VAR:-default}` 展开环境变量（`${VAR}` 未定义会抛 `ValueError`）。配置文件要当作受信任的输入来对待：它决定了要启动哪个可执行文件，环境变量展开也没有白名单。`load_mcp_toolsets` 返回的每个 toolset 已经按 key 加好了前缀（2 个 server 就得到 2 个 `PrefixedToolset`）。`clai --mcp-config` 用的也是这个格式。

---

## 4. 生命周期：用 `async with agent`

```python
async with agent:           # 一次进入，N 次 run，最后一次性关闭
    await agent.run("第一句")
    await agent.run("第二句")
```

如果忘了 `async with`，每次 `run` 都会隐式建立再关闭一遍连接，stdio 的话就是每次起一个子进程，既慢又容易漏掉清理。整个会话用一个 `async with agent:` 包到底就行。

---

## 5. 工具出错：`tool_error_behavior`

MCP server 报告工具错误时，处理方式由这个参数决定：

| `tool_error_behavior` | 行为 |
|------|------|
| `'retry'`（默认） | 当作 `ModelRetry`：把服务器报错作为 `outcome='retried'` 的工具结果回给模型，让它改了再试（消耗工具重试预算） |
| `'failed'` | 当作 `ToolFailed`：记录为 `outcome='failed'`，模型自己决定下一步，不消耗预算 |
| `'error'` | 直接抛出底层异常、中断运行，交给应用代码处理 |

协议层和传输层的错误不走这条路。举个例子：模型给 `list_issues` 传了一个不存在的 repo，服务器返回 404，默认行为就是重试、重试预算（默认 1 次）耗尽、抛 `UnexpectedModelBehavior`。如果希望模型看到失败就如实报告，用 `MCPToolset(..., tool_error_behavior='failed')`。

## 6. 命名冲突、前缀、过滤

```python
weather = MCPToolset("http://localhost:3001/mcp").prefixed("weather")     # weather_*
calculator = MCPToolset("http://localhost:3002/mcp").prefixed("calc")       # calc_*
agent = Agent("openai:gpt-6.1-sol", toolsets=[weather, calculator])
```

V1 `MCPServerStdio(tool_prefix=..., allow_tools=...)` 的这两个参数已经没有了，改用 toolset 的组合方法：`.prefixed(...)` 加前缀，`.filtered(lambda ctx, td: td.name in {...})` 只保留需要的工具（也可以按 MCP 工具的 `meta` / `annotations` 过滤）。

## 7. 其它客户端能力（按需）

- server instructions：`MCPToolset(url, include_instructions=True)` 会把服务器初始化时给出的使用说明注入 agent 指令。默认关闭，因为这段文字是服务器写的，模型收到时会把它和你写的指令同等对待，只对信任的服务器打开；
- `process_tool_call`：在每次工具调用前后插回调（比如注入服务器 handler 要读的 metadata）；
- resources / prompts：MCP 的资源和提示词模板；`MCP Apps`：服务器声明只给自己 UI 用的工具，对模型不可见；
- 后台任务：两种 MCP Tasks 都支持。FastMCP 3 的 Server 用 2025-11-25 版（SEP-1686），按工具的 `execution.taskSupport` 决定是否以任务模式调用，`prefer_tasks=False` 可以强制普通调用；FastMCP 4 的 Server 用新的 Tasks 扩展（SEP-2663），由 Server 决定是否创建任务，普通调用会自动把任务驱动到完成；
- 认证：`auth=` 可以是 bearer token 字符串、`httpx2.Auth`、字面量 `'oauth'`（FastMCP OAuth），或者一个接收 `RunContext` 的函数，用来做每用户凭证；
- 自定义 TLS / 客户端标识：`verify=`、`client_info=`；
- sampling / elicitation：`sampling_model=` 让服务器借客户端的模型生成；`elicitation_handler` 处理服务器向用户的提问。FastMCP 4 的「现代协议模式」不支持服务器发起的 sampling 和 elicitation，也不能设 `log_level`，需要这些功能得用 FastMCP 3。

---

## 8. Pydantic AI 作 Server 的"大脑"：FastMCP

Pydantic AI 没有自己实现 MCP server，而是借用 FastMCP（装饰器写法和 FastAPI 很像），在工具函数里调用 Agent。这里的 `FastMCP` 来自第三方 `fastmcp` 包；官方 SDK 2.x 里对应的是 `mcp.server.MCPServer`，见 MCP 手册的[写第一个 MCP Server 和 Client](../../../03-mcp/docs/01-basics/06-first-server.md)。

```python
from fastmcp import FastMCP            # ⚠️ 不是 mcp.server.fastmcp（mcp 2.x 里已更名）
from pydantic_ai import Agent

mcp = FastMCP("my-server")

poet_agent = Agent("openai-chat:qwen-flash", instructions="你是一位押韵诗人，回答必须押韵。")

@mcp.tool()
async def write_poem(theme: str) -> str:
    """根据主题写一首押韵的小诗"""
    result = await poet_agent.run(f"主题：{theme}")
    return result.output

@mcp.resource("config://app")
def app_config() -> str:
    """暴露一个静态资源"""
    return '{"version": "1.0"}'

@mcp.prompt()
def summarize_prompt(text: str) -> str:
    """暴露一个可复用 prompt 模板"""
    return f"请用一句话总结：{text}"

if __name__ == "__main__":
    mcp.run(show_banner=False)                                          # 默认 stdio
    # mcp.run(transport="http", host="127.0.0.1", port=8000)           # HTTP 端点默认在 /mcp
```

| 装饰器 | 暴露给 client | 常见用途 |
|--------|--------------|---------|
| `@mcp.tool()` | tool 列表 | 让 LLM 调用执行动作 |
| `@mcp.resource("uri")` | resource 列表 | 暴露只读数据 |
| `@mcp.prompt()` | prompt 列表 | 暴露可复用模板 |

> stdio server 的日志必须走 **stderr**，往 stdout 打印会污染协议流。demo 04（GitHub Issue 查询）里用的就是 `logging.basicConfig(stream=sys.stderr)`。FastMCP 的启动横幅可以用 `show_banner=False` 关掉。

### 8.1 双向：Agent 同时是 Client 和 Server

```python
from fastmcp import FastMCP
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

# 1) 作 client：用 filesystem server 读文件
fs = MCPToolset(StdioTransport(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]))
agent = Agent("openai-chat:qwen-flash", toolsets=[fs])

# 2) 作 server：把"读文件并总结"暴露给外面
mcp = FastMCP("file-summarizer")

@mcp.tool()
async def summarize_file(path: str) -> str:
    """读取文件并用 LLM 总结"""
    async with agent:
        result = await agent.run(f"读取 {path} 并用一句话总结")
        return result.output

if __name__ == "__main__":
    mcp.run(show_banner=False)
```

这样就成了 Agent 编排 Agent：服务内部是 Pydantic AI Agent，对外只暴露 MCP，client 用 Claude Code 还是 Cursor 都可以。

### 8.2 同进程测试：把 FastMCP server 直接交给 `MCPToolset`

```python
toolset = MCPToolset(mcp)            # 传 FastMCP 对象：不起进程、不走网络
agent = Agent("test", toolsets=[toolset])
async with agent:
    await agent.run("hi")
```

demo 04 就是用这个办法确认 server 的 3 个工具都注册上了。

---

## 9. MCP Sampling：让 server 反过来调 client 的 LLM

有些 server 需要调 LLM，但又不想自己持有 API Key，比如插件市场里别人写的 server。MCP 允许它通过 client 的连接反向调用 LLM，这叫 Sampling。Pydantic AI 里对应的是 `MCPSamplingModel`：

```python
from mcp.server.fastmcp import Context, FastMCP     # 这个例子来自官方文档，用的是官方 mcp SDK 1.x 的 FastMCP
from pydantic_ai import Agent
from pydantic_ai.models.mcp_sampling import MCPSamplingModel

server = FastMCP("sampling-demo")
agent = Agent(instructions="你只会押韵")            # 注意没传 model

@server.tool()
async def write_poem(ctx: Context, theme: str) -> str:
    result = await agent.run(f"写一首关于 {theme} 的诗", model=MCPSamplingModel(session=ctx.session))
    return result.output
```

这样 server 不需要 API Key，LLM 费用全由 client 承担。注意这段代码依赖 `mcp` 1.x 的 `Context`，FastMCP 4 的现代协议模式不支持 sampling，在 2.64 的依赖组合下不一定能直接运行。官方文档里 client 侧是用 `MCPToolset(..., sampling_model=...)` 提供模型的。

---

## 10. 实战：用 filesystem server 让 Agent 操作本地文件

```python
import asyncio
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

fs = MCPToolset(StdioTransport(command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp/mcp-demo"]))

agent = Agent(
    "openai-chat:qwen-flash",
    toolsets=[fs],
    instructions="你是文件管家，回答问题时可以读写 /tmp/mcp-demo 下的文件。",
)

async def main() -> None:
    async with agent:
        r = await agent.run("创建一个 hello.txt，里面写一句中文问候")
        print(r.output)
        r = await agent.run("hello.txt 里写的是什么？")
        print(r.output)

asyncio.run(main())
```

第一次运行时 `npx` 要下载包，会多等几秒。

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `ImportError: cannot import name 'MCPServerStdio'` | V2 去掉了旧客户端类 | 用 `MCPToolset` / `MCP` capability |
| `ModuleNotFoundError: mcp.server.fastmcp` | `mcp` 2.x 把 FastMCP 改名为 `MCPServer` | 用 `from fastmcp import FastMCP`（`pip install fastmcp`），或锁 `mcp<2` |
| `ImportError: FastMCP server support is not installed` | 只装了 `fastmcp-slim` | `pip install "fastmcp-slim[server]"` 或 `pip install fastmcp` |
| 启动慢 5-10 秒 | 第一次 `npx` 在下载包 | 提前 `npx -y <pkg> --version` 预热 |
| 报错 `connection closed` | server 子进程崩溃 | 单独运行 server 脚本看 stderr |
| 工具调用后卡住 / 每次 run 都重启 server | 没写 `async with agent` | 整个会话包一个 `async with agent:` |
| 多个 server 工具重名 | 名字冲突 | `.prefixed("xxx")` 或 `MCP(..., tool_prefix=...)` |
| HTTP server `401` | 没带认证 | `MCPToolset(url, auth="token")` / `headers={...}`（每用户用函数） |
| 模型传错参数后运行直接失败 | 默认 `tool_error_behavior='retry'`，预算 1 次 | 调大 `max_retries` / 设 `'failed'` |
| stdio server 日志污染协议 | 往 stdout 打印 | 日志走 stderr |
| sampling / elicitation 不工作 | FastMCP 4 现代协议模式不支持 | 用 FastMCP 3，或让 server 直接持 key |
| 工具描述太长、费 token | server tool 的 description 没控制 | `.filtered(...)` 只挑要的；工具多时用 `defer_loading` |

---

## 12. 何时该用 MCP，何时别用

| 场景 | 用 MCP | 用 `@agent.tool` |
|------|-------|-----------------|
| 文件系统、Git、Browser、Slack 等通用能力 | 适合，大概率有现成 server | — |
| 业务 DB 查询、内部 RPC | — | 适合，配合 deps 注入更简单 |
| 想让 Claude Code / Cursor 也能用同一个能力 | 适合 | — |
| 强类型校验、和 Pydantic Model 紧耦合 | — | 适合 |
| 跨语言（server 用 Go/Rust 写） | 适合 | — |
| 性能敏感 | — | 适合，同进程没有额外开销 |

---

## 13. 本章 demo

完整可运行代码：[`demos/modules/01_mcp.py`](../../demos/modules/01_mcp.py)，包含三个例子：

1. Demo A（`client`）：Agent 作 client，调用 `filesystem` MCP server 读写文件
2. Demo B（`server`）：用 FastMCP 写一个最小的 MCP server
3. Demo C（`test`，默认）：没有 API Key 时，用 `TestModel` + 子进程 stdio server 验证注册流程

完整的实战版见 [`demos/practice/04_project_mcp_server.py`](../../demos/practice/04_project_mcp_server.py)。下一篇：[Pydantic Evals 评测框架](02-evals.md)。
