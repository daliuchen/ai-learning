# 动态工具集与上下文感知工具

> **一句话**：`tools=[...]` 可以在每次 `Runner.run` 之前临时拼——不同用户、不同权限、不同场景给不同 tool 列表。

---

## 1. 为啥要动态

固定工具集的问题：

- **管理员 / 普通用户**：给不同 tool 列表（user 看不到 `delete_all`）
- **付费 / 免费**：免费用户只能调便宜 tool
- **场景化**：客服 agent 只在退款场景里有 `process_refund`
- **A/B test**：50% 给 tool v1，50% 给 v2

---

## 2. 写法 1：每次 new Agent

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

简单粗暴。每次 new Agent 性能影响可忽略。

---

## 3. 写法 2：用 clone

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

## 4. 写法 3：tool 内部 gate

让 tool 自己根据 context 决定要不要执行：

```python
@function_tool
async def delete_record(ctx: RunContextWrapper[MyContext], record_id: int) -> str:
    if not ctx.context.user.is_admin:
        return "无权限"
    # 真删
    db.delete(record_id)
    return f"deleted {record_id}"
```

模型还是看得到 tool，但执行会被拒。优点：tool 列表稳定，方便缓存。

---

## 5. 上下文相关的 tool description

```python
def make_tool_description(user):
    plan = "付费版" if user.is_pro else "免费版"
    return f"查询数据（当前 {plan}，每天限 100 次）"


@function_tool(description_override="...")  # 静态
def search(...): ...
```

但 `description_override` 是 Agent 创建时定的。要动态描述，最简单的方法是动态 instructions（详见 [03-agent-config.md](../01-basics/03-agent-config.md)）。

---

## 6. 大 tool 集的"虚拟工具夹"

当工具特别多（>20）让模型挑会迷糊。常见模式：

### 模式 A：分组 + Triage

```python
billing_tools = [refund, change_plan, ...]   # 10 个
support_tools = [search_kb, escalate, ...]   # 10 个

billing = Agent(name="Billing", tools=billing_tools)
support = Agent(name="Support", tools=support_tools)

triage = Agent(name="Triage", handoffs=[billing, support])
```

详见 [03-handoffs/02-triage-pattern.md](../03-handoffs/02-triage-pattern.md)。

### 模式 B：Meta-tool

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

Agent 先 list 再 call。但这绕了一层，性能没原生 tools 好。

---

## 7. MCP 动态工具

MCP Server 启动时 declare 的 tools list 也可以动态。用 OpenAI Agents 消费 MCP Server：

```python
from agents.mcp import MCPServerStdio

server = MCPServerStdio(params={
    "command": "python",
    "args": ["my_mcp_server.py"],
})

await server.connect()
tools = await server.list_tools()  # 动态拿

agent = Agent(name="A", tools=tools)
```

详见 [06-integration/01-mcp.md](../06-integration/01-mcp.md)。

---

## 8. Tools 是否依赖运行时数据

如果 tool 实现需要运行时数据（DB 连接、API client），用 context：

```python
class Ctx(BaseModel):
    db: object
    api_client: object

    class Config:
        arbitrary_types_allowed = True


@function_tool
async def query_db(ctx: RunContextWrapper[Ctx], sql: str) -> str:
    return await ctx.context.db.execute(sql)


ctx = Ctx(db=my_db, api_client=my_client)
await Runner.run(agent, query, context=ctx)
```

不是修改 tools 列表，是注入"工具用的资源"。

---

## 9. 在 FastAPI handler 里组装

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

## 10. 完整 demo

```python
# demos/tools/05_dynamic_tools.py
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

## 11. 下一步

- 📖 用户 ctx 配 tools → [01-basics/06-sessions.md](../01-basics/06-sessions.md)
- 📖 Handoffs：把大 tool 集拆成多 Agent → [03-handoffs/01-handoffs-concept.md](../03-handoffs/01-handoffs-concept.md)
- 📖 MCP 接外部 tool 集 → [06-integration/01-mcp.md](../06-integration/01-mcp.md)
