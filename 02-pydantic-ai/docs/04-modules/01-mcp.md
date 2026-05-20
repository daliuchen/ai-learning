# Pydantic AI 04-01：MCP（Model Context Protocol）集成

> **一句话**：MCP 是 Anthropic 主导、Pydantic / OpenAI / Google 都已跟进的"LLM 工具调用统一协议"，Pydantic AI 既能当 **client**（让 Agent 调用别人的 MCP server），也能当 **server**（把 Agent 暴露成 MCP 服务），三种传输方式 Stdio / Streamable HTTP / SSE 都内置。

---

## 1. 为什么会有 MCP

写过 Agent 工具的同学都遇到过这样的场景：

- 想让 Agent 能读本地文件 → 自己写一个 `read_file` tool
- 想让 Agent 能查 GitHub Issue → 自己写一个 `github_search` tool
- 想让 Agent 能控制浏览器 → 自己再写一个 `browser_click` tool

每接一个外部能力都要在你的 Agent 代码里加一个 `@agent.tool`，工具数量一多，**Agent 代码就成了万能工具仓库**。更糟的是这些工具完全不通用——你给 Pydantic AI 写的 tool，换 LangChain 写一份，换 Claude Code 又得写一份。

MCP（Model Context Protocol）做的事就是：

> 把"工具"这件事**从 Agent 里抽离**，定义一个**标准协议**，让任何 server 都可以发布自己的 tool / resource / prompt，任何 client（Agent / IDE / CLI）都可以用同一套协议接入。

你可以把它类比成 LSP（Language Server Protocol）：以前 VSCode、Vim、Emacs 各自实现 Python 智能提示，现在大家都用同一个 `pylsp`。MCP 之于 LLM 工具，正是 LSP 之于 IDE。

---

## 2. MCP vs 普通 tool

| 维度 | `@agent.tool` 本地工具 | MCP 工具 |
|------|----------------------|---------|
| 部署方式 | 跟 Agent 同进程 | 独立进程（stdio）或独立服务（HTTP） |
| 跨语言 | ❌ 必须 Python | ✅ 任何语言都行（官方有 TS/Python/Rust SDK） |
| 跨框架复用 | ❌ Pydantic AI 专属 | ✅ Claude Code / Cursor / Cline 都能用 |
| 发现机制 | 写死在代码 | Client 启动时 `list_tools()` 动态拉取 |
| 适合场景 | 紧耦合业务工具 | 通用能力（FS、Git、DB、Browser…） |

**一句话**：业务专属、轻量、跟 deps 紧耦合的工具，仍然用 `@agent.tool`；通用能力（文件系统、浏览器、Slack…）能用现成 MCP server 就别自己写。

---

## 3. Pydantic AI 作 Client

Pydantic AI 提供三个 `MCPServer` 子类，分别对应三种传输方式：

```python
from pydantic_ai.mcp import (
    MCPServerStdio,           # 子进程 + stdio 双向管道
    MCPServerStreamableHTTP,  # 推荐的 HTTP 传输（流式响应）
    MCPServerSSE,             # 旧版 HTTP+SSE，逐步淘汰
)
```

它们都实现了 `pydantic_ai.toolsets.AbstractToolset`，注册到 Agent 只需要传 `toolsets=[...]`：

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

server = MCPServerStdio(
    "npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
)

agent = Agent("openai:gpt-4o-mini", toolsets=[server])

async def main() -> None:
    async with agent:                       # 进入时启动 server，退出时关闭
        result = await agent.run("/tmp 下有哪些文件？")
        print(result.output)
```

### 3.1 三种传输方式怎么选

| 传输 | 何时用 | 优点 | 缺点 |
|------|-------|------|------|
| **Stdio** | 本机工具、个人开发 | 零网络配置、安全 | 单机、子进程模型 |
| **Streamable HTTP** | 团队/线上部署、跨机器 | 多客户端、可水平扩展、状态可恢复 | 需要部署 |
| **SSE** | 兼容老 server | — | 已 deprecated，能避就避 |

**默认建议**：本地开发用 `MCPServerStdio`，生产用 `MCPServerStreamableHTTP`。

### 3.2 Stdio 详细参数

```python
server = MCPServerStdio(
    command="python",          # 启动命令
    args=["my_server.py"],     # 启动参数
    env={"DEBUG": "1"},        # 子进程环境变量
    cwd="/path/to/server",     # 工作目录
    timeout=10,                # 启动握手超时（秒）
)
```

最常见的两类 stdio server：

```python
# 1) npx 启动 Node 写的 MCP server（最常见）
MCPServerStdio("npx", args=["-y", "@modelcontextprotocol/server-github"])

# 2) python 启动自己写的 server
MCPServerStdio("python", args=["my_mcp_server.py"])
```

### 3.3 HTTP 详细参数

```python
from pydantic_ai.mcp import MCPServerStreamableHTTP

server = MCPServerStreamableHTTP(
    url="http://localhost:8000/mcp",
    headers={"Authorization": "Bearer xxx"},   # 鉴权
    timeout=30,
)
```

HTTP 模式下 server 通常长期运行（K8s pod / Docker 容器），多个 Agent 实例共享。

---

## 4. 生命周期管理：必须 `async with`

MCP server 需要"握手 → 维持连接 → 优雅关闭"。Pydantic AI 用 async context manager 把这一切包好了：

```python
async with agent:           # 一次进入，N 次 run，最后一次性关闭
    await agent.run("第一句")
    await agent.run("第二句")
# 退出后 server 已经停掉
```

或者直接对单个 server：

```python
async with server:
    tools = await server.get_tools(ctx)   # 也可以脱离 Agent 直接调用
```

❌ **错误示范**：忘了 `async with`，每次 run 都启动一个新子进程：

```python
agent = Agent(..., toolsets=[MCPServerStdio(...)])
await agent.run("...")    # 内部隐式启动 + 关闭，慢且容易漏 cleanup
await agent.run("...")    # 又来一次
```

✅ **正确**：

```python
async with agent:
    for q in questions:
        await agent.run(q)
```

---

## 5. Pydantic AI 作 Server：FastMCP

Pydantic AI 没有自己重新发明 MCP server，而是直接用官方 SDK 里的 `FastMCP`（类似 FastAPI 风格的装饰器）：

```python
from mcp.server.fastmcp import FastMCP
from pydantic_ai import Agent

mcp = FastMCP("my-server")

# 用 Pydantic AI 的 Agent 作为底层"大脑"
poet_agent = Agent(
    "anthropic:claude-3-5-haiku-latest",
    system_prompt="你是一位押韵诗人，回答必须押韵。",
)

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
    mcp.run()    # 默认 stdio
```

### 5.1 装饰器三件套

| 装饰器 | 暴露给 client | 典型用途 |
|--------|--------------|---------|
| `@mcp.tool()` | tool 列表 | 让 LLM 调用执行动作 |
| `@mcp.resource("uri")` | resource 列表 | 暴露只读数据（配置、文档） |
| `@mcp.prompt()` | prompt 列表 | 暴露可复用模板 |

### 5.2 HTTP 模式启动

```python
mcp.run(transport="streamable-http")  # 监听 0.0.0.0:8000/mcp
```

或者用 `uvicorn`：

```bash
uvicorn my_server:mcp.streamable_http_app --port 8000
```

---

## 6. 双向：Agent 同时是 Client 和 Server

最有意思的玩法：你的 Pydantic AI Agent 既调用别的 MCP server（作 client），又把自己暴露成 MCP server（让别的 Agent 调用）。

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio
from mcp.server.fastmcp import FastMCP

# 1) 作 client：用 filesystem server 读文件
fs_server = MCPServerStdio(
    "npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
)
agent = Agent("openai:gpt-4o-mini", toolsets=[fs_server])

# 2) 作 server：暴露 read_summary 工具给外面
mcp = FastMCP("file-summarizer")

@mcp.tool()
async def summarize_file(path: str) -> str:
    """读取文件并用 LLM 总结"""
    async with agent:
        result = await agent.run(f"读取 {path} 并用一句话总结")
        return result.output

if __name__ == "__main__":
    mcp.run()
```

这就是"**Agent 编排 Agent**"的标准模式——你的服务对内是 Pydantic AI Agent，对外只暴露 MCP 协议，client 是 Claude Code 还是 Cursor 都无所谓。

---

## 7. MCP Sampling：让 server 反过来调 client 的 LLM

某些场景下 server 需要调用 LLM 但**不想自己持 API Key**——比如插件市场里别人写的 MCP server。MCP 协议允许 server 通过 client 的连接"反向"调用 LLM，叫 **Sampling**。

Pydantic AI 提供了对应的 `MCPSamplingModel`：

```python
from mcp.server.fastmcp import FastMCP, Context
from pydantic_ai import Agent
from pydantic_ai.models.mcp_sampling import MCPSamplingModel

mcp = FastMCP("sampling-demo")
agent = Agent(system_prompt="你只会押韵")  # 注意没传 model！

@mcp.tool()
async def write_poem(ctx: Context, theme: str) -> str:
    result = await agent.run(
        f"写一首关于 {theme} 的诗",
        model=MCPSamplingModel(session=ctx.session),  # ← 用 client 的 LLM
    )
    return result.output
```

server 自己不需要任何 API Key，所有 LLM 费用都由 client 承担。这对"零信任的 MCP 市场"非常关键。

---

## 8. 工具发现与过滤

Agent 启动时会调用每个 server 的 `list_tools()` 拉所有工具，你可以**过滤**只用其中一部分：

```python
from pydantic_ai.mcp import MCPServerStdio

server = MCPServerStdio(
    "npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    tool_prefix="fs_",        # 给所有工具加前缀，避免重名
    allow_tools=["fs_read_file", "fs_list_directory"],  # 只允许这两个
)
```

`tool_prefix` 在多个 server 工具重名时特别有用。

---

## 9. 实战：用 filesystem server 让 Agent 操作本地文件

完整 demo 见 [`demos/modules/01_mcp.py`](../../demos/modules/01_mcp.py)。核心思路：

```python
import asyncio
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

# 用官方 filesystem MCP server（Node 实现，npx 自动下载）
fs = MCPServerStdio(
    "npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp/mcp-demo"],
)

agent = Agent(
    "openai:gpt-4o-mini",
    toolsets=[fs],
    system_prompt="你是文件管家，回答问题时可以读写 /tmp/mcp-demo 下的文件。",
)

async def main() -> None:
    async with agent:
        r = await agent.run("创建一个 hello.txt，里面写一句中文问候")
        print(r.output)
        r = await agent.run("hello.txt 里写的是什么？")
        print(r.output)

asyncio.run(main())
```

跑起来你会观察到：

1. 第一次启动时 `npx` 会下载 `@modelcontextprotocol/server-filesystem`
2. Agent 自动发现了 `read_file` / `write_file` / `list_directory` 等十几个工具
3. 一次问答可能调用 3-5 次 tool，最后输出结果

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `MCPServerStdio` 启动慢 5-10 秒 | 第一次 `npx` 在下载包 | 提前 `npx -y <pkg> --version` 预热 |
| 报错 `connection closed` | server 子进程崩溃 | 加 `env={"DEBUG":"*"}` 看 server stderr |
| 工具调用后 hang 住 | 忘了 `async with agent` | 永远用 `async with`，别裸调 |
| 一次 run 启动一个 server | 没复用 context | 整个会话一个 `async with agent` 包到底 |
| 多个 server 工具重名 | 名字撞了 | 给每个 server 加 `tool_prefix=` |
| HTTP server `401` | header 没传 | `MCPServerStreamableHTTP(headers={"Authorization": ...})` |
| 二级模型也想用 sampling | 想嵌套 | `MCPSamplingModel(session=ctx.session)` |
| 工具描述太啰嗦烧 token | server tool 的 description 没控制 | client 侧用 `allow_tools=` 只挑要的 |
| Stdio 进程残留 | 异常退出没 cleanup | 用 `async with`，或捕获 `KeyboardInterrupt` 后 `await server.__aexit__(...)` |

---

## 11. 何时该用 MCP，何时别用

| 场景 | 用 MCP | 用 `@agent.tool` |
|------|-------|-----------------|
| 文件系统、Git、Browser、Slack 等通用能力 | ✅ 大概率有现成 server | — |
| 业务 DB 查询、内部 RPC | — | ✅ 拼 deps 注入更简单 |
| 想让 Claude Code / Cursor 也能用同一个能力 | ✅ MCP 是唯一选项 | — |
| 强类型校验、和 Pydantic Model 紧耦合 | — | ✅ 本地 tool 类型链路完整 |
| 跨语言（server 用 Go/Rust 写） | ✅ 协议无关 | — |
| 性能敏感（每次调用都要快） | — | ✅ 同进程零开销 |

---

## 12. 本章 demo

完整可运行代码：[`demos/modules/01_mcp.py`](../../demos/modules/01_mcp.py)

里面包含三个例子：

1. **Demo A**：Agent 作 client，调用 `filesystem` MCP server 读写文件
2. **Demo B**：用 `FastMCP` 自己写一个最小 MCP server 暴露 `echo` 工具
3. **Demo C**：没有 API Key 时用 `TestModel` 验证 MCP 注册流程

跑通后下一篇：[02-evals.md](02-evals.md) —— 用 Pydantic Evals 把 Agent 的回答质量当单元测试来跑。
