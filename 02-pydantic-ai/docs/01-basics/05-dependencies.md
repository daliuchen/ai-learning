# Pydantic AI 05：依赖注入（Dependencies）

> 工具、指令、输出校验经常要用到数据库连接、HTTP 客户端、当前用户这类外部资源。Pydantic AI 用依赖注入把它们交给 Agent，不用全局变量，也不用 monkey patch。这篇讲怎么声明、怎么取、怎么在测试里替换。

以 `pydantic-ai 2.64.0` 为准。这部分 API 和 V1 基本一致，`@agent.system_prompt` 推荐换成 `@agent.instructions`。

---

## 1. 为什么需要依赖注入

Agent 里的工具、动态指令、output_validator 经常需要：

- 数据库连接 / Redis 客户端
- 外部 API 的 client（httpx、stripe-sdk、Tavily 等）
- 当前请求的用户身份 / 租户 ID / 权限
- 配置对象（API key、阈值、超时）

最直接的办法是全局变量：

```python
# ❌ 朴素全局变量
db = None  # 启动时初始化

@agent.tool_plain
def query_user(user_id: str) -> str:
    return db.fetch_one("SELECT name FROM users WHERE id=$1", user_id)
```

问题不少：

1. 单元测试要 mock 全局变量
2. 同进程跑两个 agent 实例（多租户、A/B 实验）必然串台
3. 多线程 / 异步并发时共享 db 连接会竞态
4. IDE 跳转不到、类型检查抓不到
5. 读代码的人看不出这个 tool 依赖什么

Pydantic AI 的做法是把这些资源声明成一个 `deps` 类型，在 `run` 时显式传入，工具通过 `RunContext.deps` 取用。

---

## 2. 三步用法

```python
import os
from dataclasses import dataclass
import httpx
from pydantic_ai import Agent, RunContext

# 1) 声明依赖类型
@dataclass
class MyDeps:
    api_key: str
    http_client: httpx.AsyncClient

# 2) 告诉 Agent
agent = Agent(
    "openai-chat:qwen-flash",
    deps_type=MyDeps,
    instructions="你是一位天气助手。",
)

# 3) 工具里通过 RunContext 拿
@agent.tool
async def get_weather(ctx: RunContext[MyDeps], city: str) -> str:
    """查询天气"""
    resp = await ctx.deps.http_client.get(
        f"https://wttr.in/{city}?format=3",
        headers={"Authorization": ctx.deps.api_key},
    )
    return resp.text

# 调用时传入实例
async with httpx.AsyncClient() as client:
    deps = MyDeps(api_key=os.getenv("WEATHER_KEY", ""), http_client=client)
    result = await agent.run("北京天气", deps=deps)
    print(result.output)
```

### `deps_type` 是类型，`deps` 是实例

```python
# ❌ 常见错误
agent = Agent(..., deps_type=MyDeps(api_key="..."))  # 传了实例！

# ✅ 正确
agent = Agent(..., deps_type=MyDeps)                 # 传类
agent.run_sync(..., deps=MyDeps(api_key="..."))      # 这里才传实例
```

---

## 3. `RunContext` 能给你什么

`ctx` 不止有 `deps`，还有：

```python
@agent.tool
def my_tool(ctx: RunContext[MyDeps], x: int) -> str:
    ctx.deps          # 你的依赖实例
    ctx.usage         # 当前 run 的累计用量（RunUsage）
    ctx.usage_limits  # 当前生效的 UsageLimits（做「预算感知」的逻辑）
    ctx.model         # 实际用的 model 对象
    ctx.retry         # 这个工具已重试了几次（>0 表示重试）；ctx.max_retries 是上限
    ctx.prompt        # 用户原始输入
    ctx.messages      # 当前为止的 message 历史
    ctx.run_step      # agent 循环跑了几步
    ctx.tool_call_id  # 本次工具调用的 id；ctx.tool_name 是工具名
    ctx.metadata      # 运行元数据（Agent(metadata=...) / run(metadata=...)）
    ctx.workspace     # 工作区（挂了 LocalWorkspace 等 capability 时才有，见 harness 章节）
    ctx.run_id, ctx.conversation_id   # 运行 / 对话标识
    return f"got {x}"
```

可以按 `ctx.retry > 0` 走不同分支，比如第一次失败后第二次换简化逻辑。V2 的 `RunContext` 比 V1 多了不少字段（`workspace`、`metadata`、`usage_limits`、`deadline`、能力加载状态……），用到时直接在类型提示里看。

---

## 4. deps 可以是什么类型

| 形式 | 推荐度 | 注意 |
|------|--------|------|
| `@dataclass` | 首选 | 最常用，类型清晰 |
| Pydantic `BaseModel` | 推荐 | 需要校验时用 |
| `TypedDict` | 可以 | 兼容老代码 |
| `dict[str, Any]` | 不推荐 | IDE 无补全 |
| 普通 class | 推荐 | 也行，不要求 hashable |

deps 实例每次 run 都要重新传，所以别在里面塞太多东西。重的资源（如 DB pool）放一个只读引用进去，不要每次新建。

---

## 5. 依赖能注入到三个地方

### 5.1 工具（tool）

```python
@agent.tool
async def query(ctx: RunContext[Deps], sql: str) -> list[dict]:
    return await ctx.deps.db.fetch_all(sql)
```

### 5.2 动态指令

```python
@agent.instructions      # 旧写法 @agent.system_prompt 仍可用，但官方推荐 instructions
async def add_user(ctx: RunContext[Deps]) -> str:
    user = await ctx.deps.db.get_user(ctx.deps.user_id)
    return f"当前用户：{user.name}，权限：{user.role}"
```

### 5.3 输出校验

```python
from pydantic_ai import ModelRetry

@agent.output_validator
async def check_safe(ctx: RunContext[Deps], output: Answer) -> Answer:
    if await ctx.deps.moderation_api.is_unsafe(output.text):
        raise ModelRetry("内容违规，请换一种说法")
    return output
```

---

## 6. 异步依赖

建议用 async client，Agent 内部本来就是 async 的：

```python
import httpx
from dataclasses import dataclass

@dataclass
class Deps:
    http: httpx.AsyncClient

@agent.tool
async def search(ctx: RunContext[Deps], q: str) -> str:
    r = await ctx.deps.http.get("https://api.example.com/search", params={"q": q})
    return r.text
```

同步的工具函数（`def` 而不是 `async def`）会被 Pydantic AI 放到线程池里跑，不会阻塞事件循环；但在 `async def` 的工具里直接用同步 client，会把整个事件循环卡住。要么换异步 client，要么 `await asyncio.to_thread(...)`。

还有一点：工具跑在事件循环里时，不能再调 `agent.run_sync` 这类会自己开事件循环的同步 API（会报 `This event loop is already running`），要用对应的 `async` 版本。

---

## 7. 复合依赖

实际项目里 deps 往往很多，可以分两层：

```python
@dataclass
class Infra:
    db: AsyncDB
    redis: AsyncRedis
    http: httpx.AsyncClient

@dataclass
class RequestCtx:
    infra: Infra            # 共享基础设施
    user_id: str            # 本次请求的用户
    trace_id: str

agent = Agent("openai:gpt-6.1-sol", deps_type=RequestCtx)

@agent.tool
async def get_orders(ctx: RunContext[RequestCtx]) -> list[Order]:
    cache_key = f"orders:{ctx.deps.user_id}"
    cached = await ctx.deps.infra.redis.get(cache_key)
    if cached:
        return cached
    rows = await ctx.deps.infra.db.fetch(...)
    return rows
```

`Infra` 全进程共享，`RequestCtx` 每次请求新建。

---

## 8. 测试：用 `override` 替换 deps

```python
# 业务代码
async def application_code(prompt: str) -> str:
    deps = build_real_deps()
    r = await agent.run(prompt, deps=deps)
    return r.output

# 单测
async def test_app():
    fake_deps = MyDeps(api_key="x", http_client=mock_http)
    with agent.override(deps=fake_deps):
        result = await application_code("北京天气")
    assert "晴" in result
```

`override` 会临时把 agent 的 deps 换成你给的（线程安全、协程安全），测试里一般都这么写。

也可以同时 override model：

```python
from pydantic_ai.models.test import TestModel

with agent.override(model=TestModel(), deps=fake_deps):
    ...
```

---

## 9. 实战：客服 Agent 拿用户上下文

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

@dataclass
class CustomerDeps:
    user_id: str
    db: dict[str, dict]   # 简化：真实场景应是 DB 连接

agent = Agent(
    "openai-chat:qwen-flash",
    deps_type=CustomerDeps,
    instructions="你是一位电商客服。",
)

@agent.instructions
def add_customer(ctx: RunContext[CustomerDeps]) -> str:
    user = ctx.deps.db["users"].get(ctx.deps.user_id, {})
    return f"当前用户：{user.get('name', '未知')}，VIP 等级：{user.get('vip', 0)}"

@agent.tool
def query_orders(ctx: RunContext[CustomerDeps]) -> list[dict]:
    """查询当前用户最近的订单"""
    return ctx.deps.db["orders"].get(ctx.deps.user_id, [])

deps = CustomerDeps(
    user_id="u-001",
    db={
        "users": {"u-001": {"name": "Ethan", "vip": 3}},
        "orders": {"u-001": [{"id": "o-1", "item": "键盘", "status": "已发货"}]},
    },
)
print(agent.run_sync("我最近买了啥？", deps=deps).output)
```

`query_orders` 没有 `user_id` 参数，user_id 已经在 deps 里，LLM 不需要知道它。这样 LLM 就没机会填错 user_id、查到别人的订单上。能从 deps 拿的值就别让 LLM 填，既安全又省 token。

---

## 10. vs LangChain

LangChain 1.x 的对应物是 `create_agent(context_schema=...)` + `ToolRuntime`：

```python
from dataclasses import dataclass
from langchain.agents import create_agent
from langchain.tools import tool, ToolRuntime

@dataclass
class Context:
    user_id: str

@tool
def query(runtime: ToolRuntime[Context]) -> str:
    """查当前用户的订单"""
    return db.fetch(runtime.context.user_id)

agent = create_agent("openai:gpt-6.1-sol", tools=[query], context_schema=Context)
agent.invoke({"messages": [...]}, context=Context(user_id="u-001"))
```

（更早的做法是闭包传递，或通过 `RunnableConfig["configurable"]` 透传。）两者现在思路很接近：运行时注入一个带类型的上下文对象。Pydantic AI 的 `RunContext.deps` 同时覆盖工具、指令、输出校验、hooks，且 `Agent[DepsT, OutputT]` 的泛型让类型检查器能查「工具要的 deps 和 agent 声明的是不是同一个」。

---

## 11. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| mypy / pyright 报 deps 类型不匹配（运行时不报错，`deps_type` 只用于类型推断） | `deps_type` 传了实例不是类 | `deps_type=MyDeps`（不是 `MyDeps(...)`）|
| 工具拿不到 `ctx.deps` | 用了 `@agent.tool_plain` | 改用 `@agent.tool`，第一参数是 `ctx: RunContext[Deps]` |
| `AttributeError: 'NoneType' object has no attribute ...` | 忘了传 `deps=` | `agent.run_sync("...", deps=...)` |
| 类型注解写错（`RunContext[OtherDeps]`） | 静态检查不一致 | 全用同一个 deps_type |
| 多线程共享 deps 串台 | deps 里塞了可变状态（如计数器） | 重的资源用只读共享，可变状态放局部 |
| async 工具里 sync 客户端阻塞 | `requests` / 同步 DB 客户端 | 换异步 client，或用 `asyncio.to_thread` |
| `override` 没生效 | 漏写了 `with`，或在 `with` 外面调用 | `with agent.override(...): await ...` |
| 工具里调 `run_sync` / `*_sync` 报 `event loop is already running` | 工具在事件循环里执行 | 把工具写成 `async def`，改用 `await ...` 的异步版本 |

---

## 12. 本章 demo

完整可运行代码：[`demos/basics/05_dependencies.py`](../../demos/basics/05_dependencies.py)

下一章：[Pydantic AI 06：结构化输出（Output Types）](06-output-types.md)。
