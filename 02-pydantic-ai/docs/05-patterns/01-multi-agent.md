# Pydantic AI 05-01：多 Agent 协作模式

> **一句话**：多 Agent 协作主要有四种主流模式 —— **编排（orchestrator）**、**Agent-as-Tool**、**Handoff（移交）** 和 **消息总线**。Pydantic AI 把它们都做成纯 Python 异步函数 + Pydantic 类型，不强加额外 DSL，理解清楚四种模式后你就能在一个项目里混合使用。

---

## 1. 为什么需要多 Agent

单个 Agent 走天下听起来很美，但真实业务很快会撞墙：

- **职责膨胀**：一个 Agent 既要分类客户问题，又要查订单，又要写邮件，system prompt 堆到 2k token，token 烧得快还互相干扰
- **工具爆炸**：一个 Agent 挂 30 个 tool，模型 schema 选择困难，准确率断崖式下跌（实测 OpenAI 文档建议单 Agent ≤ 20 个 tool）
- **权限隔离**：客服 Agent 不应该有数据库写权限，财务 Agent 不应该看用户聊天，单 Agent 没法做最小权限
- **模型分级**：分类 / 路由用便宜的 `gpt-4o-mini`，深度推理用 `claude-sonnet`，单 Agent 只能选一个

所以"把一个胖 Agent 拆成几个瘦 Agent + 协作机制"是工程上的必然选择。Pydantic AI 的多 Agent 方案，**核心只有一句话**：

```
Agent 是普通对象，agent.run() 是普通协程，你想怎么组合都行。
```

没有"必须用我们的图框架才能做多 Agent"这种绑架。

---

## 2. 四种协作模式速览

| 模式 | 控制流 | 谁决定下一步 | 典型场景 | Pydantic AI 推荐姿势 |
|------|--------|--------------|----------|----------------------|
| **Agent-as-Tool** | 父 Agent 调子 Agent | LLM（父 Agent 选择是否调） | 文档摘要器 / 翻译器作为可选工具 | `@parent.tool` 内部 `child.run(...)` |
| **Orchestrator** | 编排器按图执行 | 代码（编排器） | 固定流水线：抽取 → 校验 → 入库 | `pydantic_graph` 或纯 Python `async` |
| **Handoff** | Agent A 显式移交给 B | LLM（A 决定移交） | 客服分流（前台 → 技术支持 / 账单） | A 的 tool 返回 `Handoff(to_agent=B, message=...)` |
| **Message Bus** | 多 Agent 监听 / 发布事件 | 事件 + 代码 | 多 Agent 并行 + 异步协作 | `asyncio.Queue` / Redis Pub-Sub |

后面四节分别讲。

---

## 3. 模式 1：Agent-as-Tool（最常用）

最朴素也最实用的写法 —— 把"子 Agent"包成父 Agent 的一个工具：

```python
from pydantic_ai import Agent, RunContext

translator = Agent(
    "openai:gpt-4o-mini",
    system_prompt="你是一名翻译，把任何输入翻译成英文，只输出译文。",
)

main_agent = Agent(
    "openai:gpt-4o",
    system_prompt="你是助手。需要把内容翻译成英文时，调用 translate 工具。",
)

@main_agent.tool
async def translate(ctx: RunContext, text: str) -> str:
    """把中文翻译成英文。

    Args:
        text: 待翻译的中文文本。
    """
    result = await translator.run(text)
    return result.output
```

**关键观察**：

1. `@main_agent.tool` 装饰的函数体里直接 `await translator.run(...)`，没有任何特殊 API
2. 工具的 docstring 会被自动当作 schema 描述给模型 —— 这点和 OpenAI Function Calling 是一致的
3. **子 Agent 完全独立**：用不同模型、不同 system prompt、不同输出类型都可以

### 3.1 传递 usage（token 计费）

子 Agent 跑完后，token 不会自动汇总到父 Agent。生产环境一定要传 usage：

```python
@main_agent.tool
async def translate(ctx: RunContext, text: str) -> str:
    result = await translator.run(text, usage=ctx.usage)
    return result.output
```

`ctx.usage` 是父 Agent 当前 run 的累计 usage，传给子 Agent 后子 Agent 的 token 会**直接加到这个对象上**。最终 `parent_result.usage()` 返回的是父子总和。

### 3.2 共享 deps

子 Agent 想拿到父 Agent 的 deps（比如数据库连接）：

```python
@main_agent.tool
async def translate(ctx: RunContext[DBConn], text: str) -> str:
    result = await translator.run(text, deps=ctx.deps, usage=ctx.usage)
    return result.output
```

注意子 Agent 也要声明同样的 deps 类型（或更宽泛的父类）。

---

## 4. 模式 2：Orchestrator（编排器）

当流程是**确定的**（不是 LLM 决定下一步），Agent-as-Tool 反而是浪费 —— 没必要让 LLM 自由选择，直接代码编排就好。

### 4.1 纯 Python 编排

```python
from pydantic_ai import Agent
from pydantic import BaseModel

class ExtractedTicket(BaseModel):
    title: str
    priority: str
    category: str

class ValidatedTicket(BaseModel):
    title: str
    priority: str
    category: str
    duplicate_of: int | None

extractor = Agent("openai:gpt-4o-mini", output_type=ExtractedTicket)
validator = Agent("openai:gpt-4o", output_type=ValidatedTicket)

async def pipeline(raw_text: str) -> ValidatedTicket:
    step1 = await extractor.run(raw_text)
    step2 = await validator.run(
        f"校验并去重以下工单：{step1.output.model_dump_json()}",
        usage=step1.usage(),  # 累加 usage
    )
    return step2.output
```

**简单但够用**，绝大多数线性流水线这样写就行了，不需要图框架。

### 4.2 用 pydantic_graph 编排（复杂分支）

当流程有分支 / 循环 / 状态时，可以用官方的 `pydantic_graph`：

```python
from dataclasses import dataclass
from pydantic_graph import BaseNode, End, Graph, GraphRunContext

@dataclass
class State:
    text: str
    extracted: ExtractedTicket | None = None

@dataclass
class Extract(BaseNode[State]):
    async def run(self, ctx: GraphRunContext[State]) -> "Validate":
        r = await extractor.run(ctx.state.text)
        ctx.state.extracted = r.output
        return Validate()

@dataclass
class Validate(BaseNode[State, None, ValidatedTicket]):
    async def run(self, ctx: GraphRunContext[State]) -> End[ValidatedTicket]:
        r = await validator.run(
            f"校验：{ctx.state.extracted.model_dump_json()}"
        )
        return End(r.output)

graph = Graph(nodes=[Extract, Validate])
result = await graph.run(Extract(), state=State(text="..."))
print(result.output)
```

`pydantic_graph` 的特点：

- 节点是 dataclass，类型注解决定连边（`-> Validate` 表示下一个节点）
- 状态对象用 dataclass，节点之间共享
- 支持 `End[T]` 显式终止
- 支持持久化（适合长流程 / 人工审批）

---

## 5. 模式 3：Handoff（移交）

灵感来自 OpenAI Swarm。**Agent A 在 tool 里返回一个特殊对象，告诉编排层"接下来请用 Agent B 接手对话"**。Pydantic AI 没有内置的 Handoff 类型，但用结构化输出 + 编排层 5 行代码就能模拟：

```python
from typing import Literal
from pydantic import BaseModel
from pydantic_ai import Agent

class Handoff(BaseModel):
    """指示编排层把对话移交给另一个 Agent。"""
    to: Literal["tech_support", "billing", "stay"]
    reason: str

frontdesk = Agent(
    "openai:gpt-4o-mini",
    output_type=Handoff,
    system_prompt=(
        "你是客服前台。判断用户问题是技术支持(tech_support)、"
        "账单(billing)还是闲聊(stay)。"
    ),
)

tech = Agent("openai:gpt-4o", system_prompt="你是技术支持专员。")
billing = Agent("openai:gpt-4o", system_prompt="你是账单专员。")

async def route(question: str) -> str:
    decision = await frontdesk.run(question)
    target = decision.output.to
    if target == "tech_support":
        return (await tech.run(question)).output
    if target == "billing":
        return (await billing.run(question)).output
    return f"前台直接回复：{decision.output.reason}"
```

### 5.1 多轮移交

如果想让"被移交的 Agent"也能再次移交，把 `output_type=Handoff | FinalAnswer` 设成联合类型，编排层循环判断即可：

```python
class FinalAnswer(BaseModel):
    text: str

agent_a = Agent(..., output_type=Handoff | FinalAnswer)

current = agent_a
question = "..."
for _ in range(5):  # 最多 5 跳
    r = await current.run(question)
    if isinstance(r.output, FinalAnswer):
        print(r.output.text)
        break
    current = AGENTS[r.output.to]
```

---

## 6. 模式 4：Message Bus（消息总线）

适合**多 Agent 并行 + 异步**的场景。例如做监控：日志 Agent / 告警 Agent / 报表 Agent 各自订阅事件流。

最简实现就是 `asyncio.Queue`：

```python
import asyncio
from pydantic_ai import Agent

bus: asyncio.Queue = asyncio.Queue()

summarizer = Agent("openai:gpt-4o-mini", system_prompt="一句话总结日志。")
alerter = Agent("openai:gpt-4o-mini", system_prompt="判断是否需要告警。")

async def producer():
    for log in ["...", "..."]:
        await bus.put(log)

async def summarize_worker():
    while True:
        log = await bus.get()
        print("summary:", (await summarizer.run(log)).output)

async def alert_worker():
    while True:
        log = await bus.get()
        r = await alerter.run(log)
        print("alert?:", r.output)

# 真实场景下你会 spawn 多个 worker、用 Redis 替代 Queue、把消息持久化
```

生产场景把 `asyncio.Queue` 换成 Redis Stream / NATS / Kafka 即可，Pydantic AI 本身不关心传输层。

---

## 7. 共享状态 & deps

四种模式都绕不开"怎么传上下文"。Pydantic AI 的答案是 **deps**：

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

@dataclass
class AppDeps:
    db: "DBConn"
    user_id: int

agent = Agent("openai:gpt-4o", deps_type=AppDeps)

@agent.tool
async def lookup_order(ctx: RunContext[AppDeps], order_id: int) -> dict:
    return await ctx.deps.db.fetch_one(
        "SELECT * FROM orders WHERE id=$1 AND user_id=$2",
        order_id, ctx.deps.user_id,
    )

# 调用时
result = await agent.run("我的订单 1234 状态？", deps=AppDeps(db=..., user_id=42))
```

**关键纪律**：

- `deps` 是**强类型**的，不要塞 dict
- 子 Agent 想用同样的 deps，把 `deps_type=AppDeps` 也声明上，调用时传 `ctx.deps`
- deps 不要塞业务大对象，**只放"运行期依赖"**（连接池、用户身份、Feature Flag…）

---

## 8. 与 LangGraph 横向对比

很多人会问"为啥不直接用 LangGraph？" —— 两者覆盖范围不一样，看下面这张表：

| 维度 | LangGraph | Pydantic AI（含 pydantic_graph） |
|------|-----------|----------------------------------|
| 核心抽象 | StateGraph + Node + Edge | `Agent` + `pydantic_graph` 节点 |
| 状态类型系统 | `TypedDict` / `Annotated` reducer | Python dataclass / Pydantic 模型 |
| 检查点 / 持久化 | 内置 `Checkpointer` | `pydantic_graph` 支持手动 snapshot |
| 流式 UI 协议 | 自家 stream protocol | A2A / SSE，章节 02 详细讲 |
| 学习曲线 | 中（要理解 reducer / channel） | 低（Python + 类型即可） |
| 工具调用 | 通过 ToolNode | `@agent.tool` 装饰器 |
| 适合规模 | 大型有状态 Agent 系统 | 中小型 Agent + 复杂工作流也能搞 |

**经验法则**：

- 单 Agent + 偶尔多 Agent → **Pydantic AI**
- 整个产品就是大型 Agent 系统（任务规划 / 长期记忆 / 多人协作）→ **LangGraph**
- 两者**可以混用**：用 LangGraph 编排，节点内部跑 Pydantic AI 的 Agent

---

## 9. 实战：客服三级分流

把上面学的都用上 —— 一个**前台 + 技术 + 账单**的客服系统：

```python
from typing import Literal
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

class Routing(BaseModel):
    to: Literal["tech", "billing", "self"]
    reason: str

class FinalAnswer(BaseModel):
    answer: str

frontdesk = Agent(
    "openai:gpt-4o-mini",
    output_type=Routing,
    system_prompt="你是前台分流员，给出 to 字段。",
)

tech = Agent("openai:gpt-4o", system_prompt="你是技术支持。")
billing = Agent("openai:gpt-4o", system_prompt="你是账单专员。")

AGENTS = {"tech": tech, "billing": billing}

async def handle(question: str) -> str:
    routing = await frontdesk.run(question)
    if routing.output.to == "self":
        return routing.output.reason
    target = AGENTS[routing.output.to]
    answer = await target.run(question, usage=routing.usage())
    return answer.output
```

完整 demo 见 [`demos/patterns/01_multi_agent.py`](../../demos/patterns/01_multi_agent.py)。

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 子 Agent 的 token 没算进总账 | 没传 `usage=ctx.usage` | 子 `agent.run(..., usage=ctx.usage)` |
| 子 Agent 想用父的 deps 但拿不到 | 没传 `deps=ctx.deps` 或 `deps_type` 没对齐 | 子 Agent 声明同样的 `deps_type` |
| 主 Agent 把子 Agent 当 tool 但模型总不调用 | 工具描述（docstring）写得太模糊 | 把工具用途和触发场景写清楚 |
| Handoff 死循环（A → B → A → B …） | 编排层没设置 hop 上限 | `for _ in range(MAX_HOPS)` |
| pydantic_graph 节点没跑 | 返回类型不是节点对象 / End | 用类型注解 `-> NodeB` 显式声明边 |
| 子 Agent 用了和父 Agent 同名 tool 互相覆盖 | 全局命名空间冲突 | 子 Agent 独立创建，工具放在各自的实例上 |
| Message Bus 模式下消费者卡死 | `queue.get()` 是阻塞协程没设超时 | 用 `asyncio.wait_for(...)` |
| 多 Agent 串行慢 | 实际可并行的步骤没用 `asyncio.gather` | 独立步骤 `await asyncio.gather(a.run(...), b.run(...))` |

---

## 11. 生产环境建议

1. **每个 Agent 的 system prompt 单独管理**：别堆在代码里，做成模板文件或者放进 PromptHub
2. **强制传 usage**：在 review 时把"子 Agent 调用没传 usage"列为必查项，否则上线后账单会爆
3. **加 hop / 深度上限**：不管 Agent-as-Tool 还是 Handoff，都要有 `max_iterations` 兜底
4. **Logfire 全链路追踪**：Pydantic AI 原生集成 Logfire，多 Agent 时 trace 视图能看清每一跳
5. **错误降级**：子 Agent 抛 `ModelRetry` 或 `ValidationError` 时，父 Agent 要有回退策略（默认行为 / 退给人工）
6. **模型分级**：分类 / 路由用 mini 模型，深度任务用大模型，能省一个量级的钱

---

## 12. 本章 demo

完整可运行代码：[`demos/patterns/01_multi_agent.py`](../../demos/patterns/01_multi_agent.py)

下一篇：[02-web-chat-ui.md](02-web-chat-ui.md) — 从 Agent 到 Web 聊天 UI 的完整通路。
