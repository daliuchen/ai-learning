# Pydantic AI 02-03：Toolsets 工具集

> **一句话**：Toolset 是"一组工具的抽象"，让你把工具按业务模块打包、跨 Agent 复用、批量改名/加前缀、按角色过滤、动态拼装、甚至接入 MCP/LangChain——一切像积木一样组合。

---

## 1. 为什么要 Toolset

工具一多就会遇到下面这些痛点：

- 同一组"数据库工具"想给多个 Agent 复用，不想每个 Agent 重新装饰一遍
- "搜索工具" + "支付工具"想要分别启用/禁用
- 工具来自 MCP server / LangChain，想统一接入 Agent
- 跨 toolset 重名（俩 `search`），要批量改前缀避免冲突
- 测试时想 mock 整组工具

Pydantic AI 的答案：**`Toolset` 抽象**。它实现的功能可以一句话总结：

```
Toolset = 一组工具 + 一组生命周期钩子（prepare、override、enter/exit）+ 一组组合操作（filter / prefix / rename / combine）
```

---

## 2. `FunctionToolset`：最常用的入门款

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
agent = Agent('openai:gpt-4o-mini', toolsets=[weather])
```

### 2.1 Toolset 自带 instructions

每个 toolset 可以**自带一段提示词**，被注入到 Agent 的 system 里：

```python
search_toolset = FunctionToolset(
    instructions='Always use the search tool before answering factual questions.',
)

@search_toolset.tool_plain
def search(query: str) -> str:
    """Search the web."""
    return f'results for {query}'
```

也支持动态 instructions（用 `RunContext`）：

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

适合"运行时插件式加载工具"的场景。

---

## 3. 组合操作

Toolset 之间能用**链式方法**组合，**每个方法都返回新 toolset**，原对象不变。

### 3.1 `CombinedToolset`：合并多个

```python
from pydantic_ai import CombinedToolset

combined = CombinedToolset([weather_toolset, datetime_toolset])
agent = Agent('openai:gpt-4o-mini', toolsets=[combined])
```

或者更常用的写法：直接传多个 toolset 到 Agent，框架自动合并。

### 3.2 `.prefixed(name)`：批量加前缀

俩 toolset 都有 `search` 工具会冲突，加前缀解决：

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

predicate 签名是 `(RunContext, ToolDefinition) -> bool`。

### 3.5 `.prepared(fn)`：批量改 tool def

和上一篇的 `PrepareTools` capability 等价，但作用域限制在 toolset 内：

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

返回的 toolset 里所有命中 predicate 的工具都自动 `requires_approval=True`。

### 3.7 链式组合一条龙

实战经常这样写：

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

[MCP](https://modelcontextprotocol.io/) 是 Anthropic 推的开放协议，社区有大量现成 server。Pydantic AI 把 MCP server 当 toolset 一等公民：

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerHTTP, MCPServerStdio

# 远程 MCP
mcp_http = MCPServerHTTP('http://localhost:8000/mcp')

# 本地 stdio（启动子进程）
mcp_stdio = MCPServerStdio(command='npx', args=['-y', '@modelcontextprotocol/server-filesystem', '/tmp'])

agent = Agent('openai:gpt-4o-mini', toolsets=[mcp_http, mcp_stdio])
```

MCP server 暴露的工具会被自动发现并塞给 Agent。

### 5.2 LangChain

```python
from langchain_community.tools import WikipediaQueryRun
from langchain_community.utilities import WikipediaAPIWrapper

from pydantic_ai.ext.langchain import LangChainToolset

wiki_tool = WikipediaQueryRun(api_wrapper=WikipediaAPIWrapper())
toolset = LangChainToolset([wiki_tool])

agent = Agent('openai:gpt-4o-mini', toolsets=[toolset])
```

---

## 6. `DeferredToolset` / 延迟加载

工具数量 > 10 或 token > 10k 时建议**延迟暴露**——只在搜索时才把工具定义送进模型：

```python
# 选项 1：构造时声明
ts = FunctionToolset(tools=[...], defer_loading=True)

# 选项 2：任意 toolset 链式调用
ts = my_toolset.defer_loading()

# 也支持 MCP server
ts = mcp_server.defer_loading()
```

适合：超大工具库（几十上百个）、超长描述。模型先调用一次"搜索工具"找到候选，再决定调哪个。

---

## 7. `ExternalToolset`：前端/上游来的工具

某些场景下工具实际由**前端或上游服务执行**，Pydantic AI 只负责拼协议：

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

模型决定调 `get_user_locale` 时，Pydantic AI 会通过 `DeferredToolRequests` 把请求暴露给应用层，由应用执行后回填。

---

## 8. `WrapperToolset`：自定义执行逻辑

继承 `WrapperToolset` 可以在**调用任意工具前后插钩子**（日志、监控、限速）：

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
agent = Agent('openai:gpt-4o-mini', toolsets=[logged])
```

---

## 9. `AbstractToolset`：完全自定义

要做得彻底，继承 `AbstractToolset`：

```python
from pydantic_ai import AbstractToolset, RunContext, ToolDefinition

class CustomToolset(AbstractToolset):
    async def get_tools(self, ctx: RunContext) -> dict[str, ToolDefinition]:
        # 返回当前可用工具
        ...

    async def call_tool(self, name, tool_args, ctx, tool):
        # 执行工具
        ...

    def get_instructions(self, ctx: RunContext) -> str | None:
        return 'Use these tools wisely.'
```

生命周期方法：

| 方法 | 何时调用 |
|------|----------|
| `for_run(ctx)` | 每次 run 起始 |
| `for_run_step(ctx)` | 每个 LLM step 之前 |
| `__aenter__` / `__aexit__` | 用 `async with` 时 |

适合：从远程注册中心动态拉工具、按 tenant 隔离工具、A/B 实验。

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

agent = Agent('openai:gpt-4o-mini', toolsets=[combined])
```

模型看到的工具是 `db_find_user` / `db_delete_user` / `search_web_search`，删除类自动走审批。

---

## 11. 对比 LangChain Toolkit

| 维度 | LangChain Toolkit | Pydantic AI Toolset |
|------|-------------------|---------------------|
| 抽象层次 | 偏低（一组 tool 而已） | 完整生命周期 + 组合操作 |
| 组合 | 手动 `tools = [*a, *b]` | `.prefixed/.filtered/.renamed/.prepared/.approval_required` 链式 |
| 动态启用 | 自己写循环过滤 | `.filtered` / `prepare` |
| MCP / 第三方 | 各自做适配器 | 一等公民 |
| 钩子 | 无统一抽象 | `WrapperToolset` |
| 测试覆盖 | 手 mock | `agent.override(toolsets=[mock])` 一行搞定 |

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 两个 toolset 工具同名报错 | 没加前缀 | `.prefixed('xxx')` |
| `prepare` 在 toolset 里怎么写 | 在 toolset 内的工具上加 `prepare=...`，或整体用 `.prepared(...)` | 看作用域 |
| MCP server 没启动 / 工具空 | `MCPServerHTTP` URL 不通 | 先 `curl` 验通 |
| `LangChainToolset` 报缺包 | 没装 `pydantic-ai-slim[langchain]` 或 langchain | `pip install` 补齐 |
| `agent.override(toolsets=[mock])` 测试不生效 | 漏写 `with` | 一定要进上下文 |
| `defer_loading=True` 后模型不知道工具存在 | 这是设计：靠 tool_search 触发 | 描述里写"先用 search_tools 找工具" |
| `FunctionToolset(instructions=...)` 没生效 | toolset 没真正绑到 agent | 检查 `toolsets=[...]` 是否传了 |
| `.filtered` 函数返回 None | predicate 必须返回 bool | 必须是 `True/False` |

---

## 13. 生产建议

1. **业务模块 = 一个 toolset**：DB / Search / Payment 各自一个 `FunctionToolset`
2. **跨模块组合一律加前缀**，从一开始就避免命名冲突
3. **危险操作集中放一个 toolset**，统一加 `.approval_required(...)`
4. **测试用 `agent.override(toolsets=[...])`** 注入 mock，避免改业务代码
5. **MCP server 写 `defer_loading()`**，工具多时省 token

---

## 14. 本章 demo

完整可运行代码：[`demos/tools/03_toolsets.py`](../../demos/tools/03_toolsets.py)

下一篇：[04-common-tools.md](04-common-tools.md) — 内置的搜索/抓取工具。
