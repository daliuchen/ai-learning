# 动态工具集与上下文感知工具

> `tools=[...]` 不必在定义 Agent 时写死，可以在每次 `Runner.run` 之前按用户、权限、场景临时组装。这篇讲几种写法，以及工具多到模型挑不过来时怎么办。

---

## 1. 为什么要动态组装

固定的工具集很快就不够用了：管理员和普通用户该看到的工具不同（普通用户不该看到 `delete_all`）；免费用户只能用便宜的工具；客服 agent 只在退款场景里才需要 `process_refund`；做 A/B 实验时，一半流量用 v1 工具，另一半用 v2。

---

## 2. 首选写法：用 `is_enabled` 按运行时条件隐藏工具

`FunctionTool` 的 `is_enabled` 参数正好用来让不同用户看到不同的工具：

```python
from agents import RunContextWrapper, function_tool


def admin_only(ctx: RunContextWrapper[dict], agent) -> bool:
    return ctx.context.get("is_admin", False)       # 也可以是 async 函数


@function_tool(is_enabled=admin_only)
def delete_record(record_id: int) -> str:
    """删除记录"""
    return f"deleted {record_id}"
```

可以直接检查：`await agent.get_all_tools(RunContextWrapper({"is_admin": False}))` 里没有 `delete_record`，传 `True` 时才有。`Agent.as_tool(..., is_enabled=...)` 和 handoff 的 `is_enabled` 也是同样的机制。被禁用的工具模型完全看不到，适合做请求级的能力开关、区分 dev/prod 环境，或者 A/B 实验。

官方文档特别强调过，`is_enabled` 只控制可见性，不等于授权。和具体参数、资源有关的权限检查（比如只能删自己的记录）仍然要在工具内部做，或者用 tool input guardrail / `needs_approval`。本地函数工具在调用前会再评估一次 `is_enabled`；MCP 服务器要自己做受保护操作的授权。

---

## 3. 写法 1：每次 new Agent

```python
def build_agent_for_user(user):
    tools = [get_weather, search]
    if user.is_admin:
        tools += [delete_record, reset_database]
    if user.is_pro:
        tools += [advanced_analytics]

    return Agent(
        name="Bot",
        instructions="...",
        tools=tools,
    )


# 每次请求
agent = build_agent_for_user(current_user)
result = await Runner.run(agent, query)
```

写法最直接，每次新建 Agent 的开销可以忽略。

---

## 4. 写法 2：用 clone

```python
base_agent = Agent(
    name="Bot",
    instructions="...",
    tools=[get_weather, search],
)


def for_admin(agent):
    return agent.clone(tools=agent.tools + [delete_record])


agent = base_agent
if current_user.is_admin:
    agent = for_admin(agent)
```

---

## 5. 写法 3：tool 内部 gate

让工具自己根据 context 决定是否执行：

```python
@function_tool
async def delete_record(ctx: RunContextWrapper[MyContext], record_id: int) -> str:
    if not ctx.context.user.is_admin:
        return "无权限"
    # 真删
    db.delete(record_id)
    return f"deleted {record_id}"
```

模型仍然能看到这个工具，只是调用时会被拒绝。好处是工具列表保持不变，方便命中缓存。

---

## 6. 上下文相关的 tool description

```python
def make_tool_description(user):
    plan = "付费版" if user.is_pro else "免费版"
    return f"查询数据（当前 {plan}，每天限 100 次）"


@function_tool(description_override="...")  # 静态
def search(...): ...
```

不过 `description_override` 在定义工具时就定下了。想让描述随上下文变化，最简单的办法是用动态 instructions（见 [Agent 配置详解](../01-basics/03-agent-config.md)）。

---

## 7. 工具太多时怎么分组

工具超过 20 个左右，模型就容易挑错。常见的处理方式有三种。

### 模式 A：分组 + Triage

```python
billing_tools = [refund, change_plan, ...]   # 10 个
support_tools = [search_kb, escalate, ...]   # 10 个

billing = Agent(name="Billing", tools=billing_tools)
support = Agent(name="Support", tools=support_tools)

triage = Agent(name="Triage", handoffs=[billing, support])
```

详见 [Triage Pattern：分流模式](../03-handoffs/02-triage-pattern.md)。

### 模式 B：Tool search 延迟加载（OpenAI Responses 专属，0.11.0 起支持）

官方对这种情况有原生方案：给工具加 `defer_loading=True`，按业务分成若干 `tool_namespace`，再给 agent 加一个（只能一个）`ToolSearchTool()`。模型先搜索，再按需加载需要的那一组：

```python
from agents import Agent, ToolSearchTool, tool_namespace
from agents.decorators import tool

@tool(defer_loading=True)
def get_customer_profile(customer_id: str) -> str:
    """Fetch a CRM customer profile."""
    ...

@tool(defer_loading=True)
def list_open_orders(customer_id: str) -> str:
    """List open orders for a customer."""
    ...

crm = tool_namespace(name="crm", description="CRM 查询工具", tools=[get_customer_profile, list_open_orders])
agent = Agent(name="Ops", model="gpt-6.1-sol", instructions="先加载 crm 命名空间再用。", tools=[*crm, ToolSearchTool()])
```

官方建议尽量用 namespace，每个命名空间最好少于 10 个函数；hosted MCP 服务也可以延迟加载（`tool_config["defer_loading"]=True`）。这套机制只支持 OpenAI Responses 模型，而且具名 `tool_choice` 不能指向命名空间或纯延迟工具。（运行这段需要 OpenAI Responses 模型。）

### 模式 C：Meta-tool

```python
@function_tool
def list_available_tools(category: str) -> str:
    """先看有哪些工具可用。category: billing / support / data"""
    return json.dumps(TOOL_CATALOG[category])


@function_tool
def call_tool(tool_name: str, args: dict) -> str:
    """调用具体工具"""
    return TOOL_REGISTRY[tool_name](**args)
```

Agent 先列出可用操作，再调用。多绕了一层，效果不如原生工具。

---

## 8. MCP 动态工具

MCP Server 声明的工具列表本身也可以变化。用 OpenAI Agents 接 MCP Server 时，别把 `list_tools()` 的结果塞进 `tools=`，那是 MCP 原生的 Tool 对象，SDK 不认。正确做法是把 server 挂到 `mcp_servers=`，SDK 会在每次 run 开始时拉取最新的工具列表：

```python
from agents.mcp import MCPServerStdio

async with MCPServerStdio(
    params={"command": "python", "args": ["my_mcp_server.py"]},
    cache_tools_list=False,          # 默认就是 False：每次 run 重新拉，工具集动态变化时能看到
) as server:
    agent = Agent(name="A", mcp_servers=[server])
    # 想看 server 当前有哪些工具：await server.list_tools()
```

MCP 工具还可以按条件过滤，用静态白名单、黑名单（`create_static_tool_filter(...)`）或者动态函数，见 [MCP 集成：让 Agent 消费 MCP Server](../06-integration/01-mcp.md) 的工具过滤一节，MCP 的其他用法也在[同一篇](../06-integration/01-mcp.md)里。

---

## 9. Tools 是否依赖运行时数据

如果工具的实现需要运行时资源（数据库连接、API client），通过 context 传进去：

```python
@dataclass
class Ctx:                       # context 可以是任意 Python 对象，dataclass 最省事（用 BaseModel 要加 arbitrary_types_allowed）
    db: object
    api_client: object


@function_tool
async def query_db(ctx: RunContextWrapper[Ctx], sql: str) -> str:
    return await ctx.context.db.execute(sql)


ctx = Ctx(db=my_db, api_client=my_client)
await Runner.run(agent, query, context=ctx)
```

这种情况工具列表不用变，变的只是工具拿到的资源。

---

## 10. 在 FastAPI handler 里组装

```python
from fastapi import FastAPI, Depends
from agents import Agent, Runner


app = FastAPI()


def build_agent(user: User) -> Agent:
    tools = [search]
    if user.role == "admin":
        tools.append(admin_tool)
    return Agent(name="Bot", instructions="...", tools=tools)


@app.post("/chat")
async def chat(body: dict, user: User = Depends(get_user)):
    agent = build_agent(user)
    result = await Runner.run(agent, body["message"])
    return {"reply": result.final_output}
```

---

## 11. 完整示例

```python
import asyncio
from dataclasses import dataclass
from agents import Agent, Runner, function_tool, RunContextWrapper


@dataclass
class UserCtx:
    user_id: str
    is_admin: bool
    is_pro: bool


@function_tool
def basic_search(query: str) -> str:
    return f"基础搜索结果: {query}"


@function_tool
def pro_search(query: str) -> str:
    return f"高级搜索结果（含分析）: {query}"


@function_tool
async def admin_action(ctx: RunContextWrapper[UserCtx], action: str) -> str:
    if not ctx.context.is_admin:
        return "无权限"
    return f"执行了 {action}"


def build_agent(user: UserCtx) -> Agent:
    tools = [basic_search]
    if user.is_pro:
        tools.append(pro_search)
    if user.is_admin:
        tools.append(admin_action)

    role = "admin" if user.is_admin else ("pro user" if user.is_pro else "free user")
    return Agent(
        name=f"Bot-{role}",
        instructions=f"你为 {role} 服务。",
        tools=tools,
    )


async def main():
    free_user = UserCtx(user_id="u1", is_admin=False, is_pro=False)
    pro_user = UserCtx(user_id="u2", is_admin=False, is_pro=True)
    admin = UserCtx(user_id="u3", is_admin=True, is_pro=True)

    for user in [free_user, pro_user, admin]:
        agent = build_agent(user)
        result = await Runner.run(agent, "搜 LLM 教程", context=user)
        print(f"\n[{user.user_id}] {result.final_output[:80]}")


asyncio.run(main())
```

---

## 12. 下一步

- context 和 session 的分工：[Sessions：会话状态自动管理](../01-basics/06-sessions.md)
- 把大工具集拆给多个 Agent：[Handoffs 概念：与 Tool、Sub-Agent 的区别](../03-handoffs/01-handoffs-concept.md)
- 接外部工具集：[MCP 集成：让 Agent 消费 MCP Server](../06-integration/01-mcp.md)
