# Pydantic AI 05：依赖注入（Dependencies）

> **一句话**：`Dependencies` 是 Pydantic AI 的"**依赖注入**"机制，让 Agent 在工具、系统提示、输出校验里能拿到 DB 连接、HTTP 客户端、当前用户等外部资源，**不靠全局变量、不靠 monkey patch**。

---

## 1. 为什么需要依赖注入

Agent 内部的工具、动态系统提示、output_validator，经常需要：

- 数据库连接 / Redis 客户端
- 外部 API 的 client（httpx、stripe-sdk、Tavily 等）
- 当前请求的用户身份 / 租户 ID / 权限
- 配置对象（API key、阈值、超时）

最朴素的方法是用**全局变量**：

```python
# ❌ 朴素全局变量
db = None  # 启动时初始化

@agent.tool_plain
def query_user(user_id: str) -> str:
    return db.fetch_one("SELECT name FROM users WHERE id=$1", user_id)
```

问题一大堆：

1. 单元测试要 mock 全局变量
2. 同进程跑两个 agent 实例（多租户、A/B 实验）必然串台
3. 多线程 / 异步并发时共享 db 连接会竞态
4. IDE 跳转不到、类型检查抓不到
5. 代码阅读者根本看不出"这个 tool 依赖什么"

Pydantic AI 的方案：**把这些资源声明成一个 `deps` 类型，在 `run` 时显式传入，工具通过 `RunContext.deps` 拿**。

---

## 2. 三步搞定

```python
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
    "openai:gpt-4o-mini",
    deps_type=MyDeps,
    system_prompt="你是一位天气助手。",
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

### `deps_type` 是**类型**，`deps` 是**实例**

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
    ctx.deps       # 你的依赖实例
    ctx.usage      # 当前 run 的 token 用量
    ctx.model      # 实际用的 model 对象
    ctx.retry      # 这是第几次重试（>0 表示重试）
    ctx.prompt     # 用户原始输入
    ctx.messages   # 当前为止的 message 历史
    ctx.run_step   # agent 循环跑了几步
    return f"got {x}"
```

`ctx.retry > 0` 时可以**做不同分支**：第一次失败后第二次走简化逻辑。

---

## 4. deps 可以是什么类型

| 形式 | 推荐度 | 注意 |
|------|--------|------|
| `@dataclass` | ⭐⭐⭐⭐⭐ | 最常用，类型清晰 |
| Pydantic `BaseModel` | ⭐⭐⭐⭐ | 你想要校验时用 |
| `TypedDict` | ⭐⭐⭐ | 兼容老代码 |
| `dict[str, Any]` | ⭐ | IDE 无补全，不推荐 |
| 普通 class | ⭐⭐⭐⭐ | 也行，必须 hashable 不强求 |

注意：**deps 实例每次 run 都重新传**，所以**别在 deps 里塞太多东西**，重的资源（如 DB pool）应该是**只读引用**而非每次新建。

---

## 5. 依赖能注入到三个地方

### 5.1 工具（tool）

```python
@agent.tool
async def query(ctx: RunContext[Deps], sql: str) -> list[dict]:
    return await ctx.deps.db.fetch_all(sql)
```

### 5.2 动态系统提示

```python
@agent.system_prompt
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

**强烈推荐用 async client**，因为 Agent 内部本来就是 async：

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

用同步 client 也能跑（Pydantic AI 会 `run_in_executor`），但你**主线程会被阻塞**。

---

## 7. 复合依赖

实际项目 deps 往往很多，建议**分层**：

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

agent = Agent("openai:gpt-4o", deps_type=RequestCtx)

@agent.tool
async def get_orders(ctx: RunContext[RequestCtx]) -> list[Order]:
    cache_key = f"orders:{ctx.deps.user_id}"
    cached = await ctx.deps.infra.redis.get(cache_key)
    if cached:
        return cached
    rows = await ctx.deps.infra.db.fetch(...)
    return rows
```

**Infra** 全进程共享，**RequestCtx** 每次请求新建。

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

`override` 会临时把 agent 的所有 deps 替换成你给的（线程安全、协程安全），**这是测试的标准姿势**。

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
    "openai:gpt-4o-mini",
    deps_type=CustomerDeps,
    system_prompt="你是一位电商客服。",
)

@agent.system_prompt
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
        "users": {"u-001": {"name": "刘晨", "vip": 3}},
        "orders": {"u-001": [{"id": "o-1", "item": "键盘", "status": "已发货"}]},
    },
)
print(agent.run_sync("我最近买了啥？", deps=deps).output)
```

**注意**：

- `query_orders` 不接收 `user_id` 参数，**LLM 不需要知道**，user_id 已经在 deps 里
- 这样可以避免 LLM"瞎填" user_id 调到别人的订单上
- **能从 deps 拿的就别让 LLM 填**，安全又省 token

---

## 10. vs LangChain

LangChain 没有直接对应"deps_type"的机制，常见做法：

```python
# LangChain：闭包传递
def make_tool(db):
    @tool
    def query(user_id: str) -> str:
        return db.fetch(user_id)
    return query

agent_executor = AgentExecutor(agent=agent, tools=[make_tool(db)])
```

或者用 `Runnable.with_config` 把 config 透传到 tool 内部：

```python
@tool
def query(user_id: str, config: RunnableConfig) -> str:
    db = config["configurable"]["db"]
    return db.fetch(user_id)
```

Pydantic AI 的 `RunContext.deps` **类型清晰、IDE 友好、不依赖闭包或 magic dict**，是更工程化的方案。

---

## 11. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| `TypeError: deps_type must be a type` | 传了实例不是类 | `deps_type=MyDeps`（不是 `MyDeps(...)`）|
| 工具拿不到 `ctx.deps` | 用了 `@agent.tool_plain` | 改用 `@agent.tool`，第一参数是 `ctx: RunContext[Deps]` |
| `AttributeError: 'NoneType' object has no attribute ...` | 忘了传 `deps=` | `agent.run_sync("...", deps=...)` |
| 类型注解写错（`RunContext[OtherDeps]`） | 静态检查不一致 | 全用同一个 deps_type |
| 多线程共享 deps 串台 | deps 里塞了可变状态（如计数器） | 重的资源用只读共享，可变状态放局部 |
| async 工具里 sync 客户端阻塞 | `requests` / 同步 DB 客户端 | 换异步 client，或用 `asyncio.to_thread` |
| `override` 没生效 | 漏写了 `with`，或在 `with` 外面调用 | `with agent.override(...): await ...` |

---

## 12. 本章 demo

完整可运行代码：[`demos/basics/05_dependencies.py`](../../demos/basics/05_dependencies.py)

下一章：[06-output-types.md](06-output-types.md) —— 结构化输出。
