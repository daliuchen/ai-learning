# Pydantic AI 02-03：Toolsets 工具集

> 工具多起来以后，挨个挂在 Agent 上会越来越难管。Toolset 把一组工具当成一个整体：可以按业务模块打包、在多个 Agent 之间复用、批量加前缀或改名、按角色过滤，MCP server 和 LangChain 工具也通过它接进来。

以 `pydantic-ai 2.64.0` 为准。V2 的 MCP 客户端从 `MCPServerHTTP` / `MCPServerStdio` 换成了基于 FastMCP 的 `MCPToolset`（见第 5 节）。

---

## 1. 为什么要 Toolset

工具一多，下面这些问题就会冒出来：

- 一组数据库工具要给好几个 Agent 用，不想每个 Agent 都重新装饰一遍
- 搜索工具和支付工具想分别启用、禁用
- 工具来自 MCP server 或 LangChain，想用同一种方式接进 Agent
- 两个模块里都有叫 `search` 的工具，要批量加前缀避免冲突
- 测试时想把整组工具换成 mock

Pydantic AI 的办法是 `Toolset` 这层抽象，它包含的东西可以这样概括：

```
Toolset = 一组工具 + 一组生命周期钩子（prepare、override、enter/exit）+ 一组组合操作（filter / prefix / rename / combine）
```

---

## 2. `FunctionToolset`：最常用的一种

把一组函数打包成一个 toolset：

```python
from pydantic_ai import Agent, FunctionToolset, RunContext

def temperature_celsius(city: str) -> float:
    """Get temperature in Celsius."""
    return 21.0

def temperature_fahrenheit(city: str) -> float:
    """Get temperature in Fahrenheit."""
    return 69.8

weather = FunctionToolset(tools=[temperature_celsius, temperature_fahrenheit])

# 也支持装饰器写法
@weather.tool_plain
def conditions(city: str) -> str:
    """Get weather conditions."""
    return "sunny"

# 给 Agent
agent = Agent('openai:gpt-6.1-sol', toolsets=[weather])
```

### 2.1 Toolset 自带 instructions

每个 toolset 可以带一段自己的提示词，注入到 Agent 的 system 里：

```python
search_toolset = FunctionToolset(
    instructions='Always use the search tool before answering factual questions.',
)

@search_toolset.tool_plain
def search(query: str) -> str:
    """Search the web."""
    return f'results for {query}'
```

也可以用 `RunContext` 写动态 instructions：

```python
@toolset.instructions
def instr(ctx: RunContext[str]) -> str:
    return f'You are helping user: {ctx.deps}.'
```

### 2.2 动态加工具：`add_function` / `add_tool`

```python
ts = FunctionToolset()
ts.add_function(temperature_celsius)
ts.add_tool(Tool(temperature_fahrenheit, takes_ctx=False))
```

适合运行时按插件方式加载工具。

---

## 3. 组合操作

Toolset 可以链式组合，每个方法都返回一个新的 toolset，原对象不变。

### 3.1 `CombinedToolset`：合并多个

```python
from pydantic_ai import CombinedToolset

combined = CombinedToolset([weather_toolset, datetime_toolset])
agent = Agent('openai:gpt-6.1-sol', toolsets=[combined])
```

更常见的写法是直接把多个 toolset 传给 Agent，框架会自动合并。

### 3.2 `.prefixed(name)`：批量加前缀

两个 toolset 都有 `search` 工具时会冲突，加前缀就能解决：

```python
combined = CombinedToolset([
    weather_toolset.prefixed('weather'),   # → weather_search
    docs_toolset.prefixed('docs'),         # → docs_search
])
```

### 3.3 `.renamed({...})`：精确改名

```python
renamed = combined.renamed({
    'current_time': 'datetime_now',
    'temperature_celsius': 'weather_temp_c',
})
```

### 3.4 `.filtered(predicate)`：按条件过滤

```python
no_fahrenheit = combined.filtered(
    lambda ctx, tool_def: 'fahrenheit' not in tool_def.name
)
```

predicate 的签名是 `(RunContext, ToolDefinition) -> bool`。

### 3.5 `.prepared(fn)`：批量改 tool def

效果和上一篇的 `PrepareTools` capability 一样，只是作用范围限定在这个 toolset 内：

```python
from dataclasses import replace

async def add_emoji(ctx, tool_defs):
    return [replace(td, description=f'🛠 {td.description}') for td in tool_defs]

prepared = weather_toolset.prepared(add_emoji)
```

### 3.6 `.approval_required(predicate)`：批量加审批

```python
approval_ts = toolset.approval_required(
    lambda ctx, td, args: td.name.startswith('delete_')
)
```

返回的 toolset 里，命中 predicate 的工具都会自动变成 `requires_approval=True`。

### 3.7 链式组合

实际项目里经常这样写：

```python
ts = (
    CombinedToolset([weather, db, search])
    .filtered(lambda ctx, td: ctx.deps.role != 'guest' or td.name == 'search')
    .prefixed('app')
    .approval_required(lambda ctx, td, args: td.name.startswith('app_db_delete'))
)
```

---

## 4. 把 toolset 注册给 Agent 的四种方式

```python
# 1) 构造时
agent = Agent(model, toolsets=[ts])

# 2) 运行时
agent.run_sync(prompt, toolsets=[ts])

# 3) 动态生成（每次 run 都重新算）
@agent.toolset
def dynamic(ctx: RunContext):
    return weather_toolset if ctx.deps.want_weather else docs_toolset

# 4) 上下文覆盖（测试常用）
with agent.override(toolsets=[mock_ts]):
    agent.run_sync(prompt)
```

---

## 5. 第三方 Toolset：MCP / LangChain

### 5.1 MCP Server

[MCP](https://modelcontextprotocol.io/) 是 Anthropic 推出的开放协议，社区已经有大量现成的 server。在 Pydantic AI 里，MCP server 就是一种 toolset。V2 的客户端是 `MCPToolset`（基于 FastMCP，`pip install "pydantic-ai-slim[mcp]"`），第一个参数可以是：URL（Streamable HTTP；以 `/sse` 结尾时走已弃用的 SSE）、本地 Python / Node 脚本路径（stdio）、FastMCP 的 transport、预先构造好的 `fastmcp.Client`，或者同进程里的 FastMCP server 对象：

```python
from fastmcp.client.transports import StdioTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

# 远程 MCP（Streamable HTTP）
mcp_http = MCPToolset('http://localhost:8000/mcp')

# 本地 stdio（启动子进程）
mcp_stdio = MCPToolset(
    StdioTransport(command='npx', args=['-y', '@modelcontextprotocol/server-filesystem', '/tmp'])
)

agent = Agent('openai:gpt-6.1-sol', toolsets=[mcp_http, mcp_stdio])

async def main():
    async with agent:            # 打开 / 关闭所有 MCP 连接
        result = await agent.run('列一下 /tmp 下的文件')
```

MCP server 暴露的工具会被自动发现并交给 Agent。多数场景下官方更推荐高一层的 `MCP` capability：`MCP('https://mcp.example.com/api')`。它还能让支持的模型厂商直接去连 MCP server（`native=True`，不支持时回退到本地连接）。

> V1 的 `MCPServerHTTP` / `MCPServerStdio` / `MCPServerStreamableHTTP` 在 V2 里已经没有了（`from pydantic_ai.mcp import MCPServerStdio` 会 `ImportError`）。MCP 的服务端（用 FastMCP 写 server、让 agent 当 server）见[MCP（Model Context Protocol）集成](../04-modules/01-mcp.md)；官方 SDK 2.x 里对应的是 `mcp.server.MCPServer`，见 MCP 手册的[写第一个 MCP Server 和 Client](../../../03-mcp/docs/01-basics/06-first-server.md)。
>
> 工具报错怎么处理由 `tool_error_behavior` 决定：`'retry'`（默认，当作 `ModelRetry`，让模型改了再试）、`'failed'`（当作 `ToolFailed`）、`'error'`（直接抛出，中断运行）。比如模型传错参数导致 MCP 工具返回错误时，默认流程是重试，重试预算（默认 1）用完后抛 `UnexpectedModelBehavior`。

### 5.2 LangChain

```python
from langchain_community.tools import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper

from pydantic_ai.ext.langchain import LangChainToolset

wiki_tool = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())
toolset = LangChainToolset([wiki_tool])

agent = Agent('openai:gpt-6.1-sol', toolsets=[toolset])
```

（需要 `pip install langchain-community` 以及对应工具的依赖。）

---

## 6. 延迟加载（`defer_loading`）与工具搜索

工具超过 10 个，或者定义超过 1 万 token 时，建议延迟暴露：只有搜索命中时才把工具定义送给模型。

```python
# 选项 1：构造时声明
ts = FunctionToolset(tools=[...], defer_loading=True)

# 选项 2：任意 toolset 链式调用
ts = my_toolset.defer_loading()

# 也支持 MCP server
ts = mcp_server.defer_loading()
```

几十上百个工具的大工具库，或者描述特别长的工具，都适合这么做。被延迟的工具一开始不出现在模型面前，框架会自动注入 `ToolSearch` capability：支持的模型（比如较新的 Anthropic、OpenAI Responses 型号）用厂商原生的工具搜索，其它模型用本地的 `search_tools` 工具按关键词匹配名字和描述，模型先搜出候选再调用。工具名用统一的前缀，描述里写上用户可能搜的词，命中率会高很多。

---

## 7. `ExternalToolset`：前端或上游执行的工具

有些工具实际由前端或上游服务执行，Pydantic AI 只负责协议这一侧：

```python
from pydantic_ai import ExternalToolset, ToolDefinition

ts = ExternalToolset([
    ToolDefinition(
        name='get_user_locale',
        description='Get the user locale from browser.',
        parameters_json_schema={'type': 'object'},
    ),
])
```

模型决定调用 `get_user_locale` 时，Pydantic AI 通过 `DeferredToolRequests` 把请求交给应用层，应用执行完再把结果填回来。

---

## 8. `WrapperToolset`：自定义执行逻辑

继承 `WrapperToolset`，可以在任何工具调用前后加逻辑，比如日志、监控、限流：

```python
from pydantic_ai import WrapperToolset, ToolsetTool, RunContext

class LoggingToolset(WrapperToolset):
    async def call_tool(self, name, tool_args, ctx, tool):
        print(f'[tool] >>> {name}({tool_args})')
        try:
            result = await super().call_tool(name, tool_args, ctx, tool)
            print(f'[tool] <<< {name} -> {result!r}')
            return result
        except Exception as e:
            print(f'[tool] !!! {name} raised {e}')
            raise

logged = LoggingToolset(weather_toolset)
agent = Agent('openai:gpt-6.1-sol', toolsets=[logged])
```

---

## 9. `AbstractToolset`：完全自定义

需要完全控制时，继承 `AbstractToolset`：

```python
from pydantic_ai import AbstractToolset, RunContext, ToolsetTool

class CustomToolset(AbstractToolset):
    async def get_tools(self, ctx: RunContext) -> dict[str, ToolsetTool]:
        # 返回当前可用工具（ToolsetTool 里带着 ToolDefinition 和参数校验器）
        ...

    async def call_tool(self, name, tool_args, ctx, tool):
        # 执行工具
        ...

    async def get_instructions(self, ctx: RunContext) -> str | None:
        return 'Use these tools wisely.'
```

生命周期方法：

| 方法 | 何时调用 |
|------|----------|
| `for_run(ctx)` | 每次 run 起始 |
| `for_run_step(ctx)` | 每个 LLM step 之前 |
| `__aenter__` / `__aexit__` | 用 `async with` 时 |

从远程注册中心动态拉取工具、按租户隔离工具、做 A/B 实验，都可以用这种方式。

---

## 10. 实战：数据库 + 搜索两套 toolset 组合

```python
from pydantic_ai import Agent, FunctionToolset, CombinedToolset, RunContext

# ---------- DB toolset ----------
db = FunctionToolset(instructions='Use DB tools for any account/order question.')

@db.tool_plain
def find_user(user_id: str) -> dict:
    """Find user by id."""
    return {'id': user_id, 'name': 'Alice'}

@db.tool_plain
def delete_user(user_id: str) -> str:
    """Delete user. ⚠️ destructive."""
    return f'deleted {user_id}'

# ---------- Search toolset ----------
search = FunctionToolset(instructions='Use search for current world events.')

@search.tool_plain
def web_search(query: str) -> str:
    """Search the web."""
    return f'top result for {query}'

# ---------- 组合：加前缀避免冲突 + 删除类需要审批 ----------
combined = (
    CombinedToolset([db.prefixed('db'), search.prefixed('search')])
    .approval_required(lambda ctx, td, args: 'delete' in td.name)
)

agent = Agent('openai:gpt-6.1-sol', toolsets=[combined])
```

模型看到的工具是 `db_find_user` / `db_delete_user` / `search_web_search`，删除类的会自动走审批。

---

## 11. 对比 LangChain Toolkit

| 维度 | LangChain Toolkit | Pydantic AI Toolset |
|------|-------------------|---------------------|
| 抽象层次 | 偏低，就是一组 tool | 有完整生命周期和组合操作 |
| 组合 | 手动 `tools = [*a, *b]` | `.prefixed/.filtered/.renamed/.prepared/.approval_required` 链式 |
| 动态启用 | 自己写循环过滤 | `.filtered` / `prepare` |
| MCP / 第三方 | 各自做适配器 | 本身就是一种 toolset |
| 钩子 | 无统一抽象 | `WrapperToolset` |
| 测试替换 | 手动 mock | `agent.override(toolsets=[mock])` |

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 两个 toolset 工具同名报错 | 没加前缀 | `.prefixed('xxx')` |
| `prepare` 在 toolset 里怎么写 | 在 toolset 内的工具上加 `prepare=...`，或整体用 `.prepared(...)` | 看作用范围选 |
| MCP server 没启动 / 工具列表为空 | `MCPToolset` 的 URL 不通，或没 `async with agent:` | 先用 `curl` 确认能连通；记得用 `async with agent` 打开连接 |
| `ImportError: cannot import name 'MCPServerStdio'` | V2 去掉了旧的 MCP 客户端类 | 改用 `MCPToolset` / `MCP` capability |
| `LangChainToolset` 报缺包 | 没装 LangChain（`pydantic-ai-slim` 没有 langchain extra） | `pip install langchain-core langchain-community` 及具体工具的依赖 |
| `agent.override(toolsets=[mock])` 测试不生效 | 漏写 `with` | 必须进入上下文 |
| `defer_loading=True` 后模型不知道有这个工具 | 设计如此，要靠 tool_search 触发 | 描述里写「先用 search_tools 找工具」 |
| `FunctionToolset(instructions=...)` 没生效 | toolset 没有绑到 agent 上 | 检查 `toolsets=[...]` 有没有传 |
| `.filtered` 函数返回 None | predicate 必须返回 bool | 返回 `True/False` |

---

## 13. 生产建议

1. 一个业务模块对应一个 toolset：DB、Search、Payment 各用一个 `FunctionToolset`
2. 跨模块组合时一律加前缀，从一开始就避开命名冲突
3. 危险操作集中放进一个 toolset，统一加 `.approval_required(...)`
4. 测试用 `agent.override(toolsets=[...])` 注入 mock，不用改业务代码
5. MCP server 工具多时加 `defer_loading()` 省 token（官方建议工具超过 10 个左右、或定义超过约 1 万 token 时用）
6. 接多个 MCP server 时都加前缀：`MCPToolset(...).prefixed('github')`，或用 `MCP(..., tool_prefix=True, id='github')`

---

## 14. 本章 demo

完整可运行代码：[`demos/tools/03_toolsets.py`](../../demos/tools/03_toolsets.py)

下一篇：[Pydantic AI 02-04：Common Tools 内置常用工具](04-common-tools.md)，讲内置的搜索和抓取工具。
