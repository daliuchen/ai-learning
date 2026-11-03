# Pydantic AI 05-01：多 Agent 协作模式

> 多 Agent 协作常见的有四种模式：编排（orchestrator）、Agent-as-Tool、Handoff（移交）和消息总线。在 Pydantic AI 里，这四种都用普通的 Python 异步函数加 Pydantic 类型来写，没有额外的 DSL。V2 的 harness 还提供了现成的 `SubAgents`、`CodeMode`、`DynamicWorkflow`。

以 `pydantic-ai 2.64.0` / `pydantic-ai-harness 0.64.0` 为准。

---

## 1. 为什么需要多 Agent

一个 Agent 包办所有事，在真实业务里很快就会遇到问题：

- 职责膨胀：一个 Agent 既要给客户问题分类，又要查订单，还要写邮件，system prompt 堆到 2k token，费钱，指令之间还互相干扰
- 工具太多：一个 Agent 挂 30 个 tool，模型很难选对，准确率会明显下降（OpenAI 文档的建议是单个 Agent 不超过 20 个 tool）
- 权限隔离：客服 Agent 不该有数据库写权限，财务 Agent 不该看用户聊天记录，单个 Agent 做不到最小权限
- 模型分级：分类和路由用便宜的 mini / flash 档模型，深度推理用 `claude-opus-5-5`，单个 Agent 只能选一个

所以工程上通常会把一个大 Agent 拆成几个小 Agent，再加上协作机制。Pydantic AI 在多 Agent 上的思路可以概括成下面这句：

```
Agent 是普通对象，agent.run() 是普通协程，你想怎么组合都行。
```

它不要求你非得用某个图框架才能做多 Agent。

---

## 2. 四种协作模式速览

| 模式 | 控制流 | 谁决定下一步 | 常见场景 | Pydantic AI 推荐写法 |
|------|--------|--------------|----------|----------------------|
| Agent-as-Tool | 父 Agent 调子 Agent | LLM（父 Agent 选择是否调） | 文档摘要器 / 翻译器作为可选工具 | `@parent.tool` 内部 `child.run(...)` |
| Orchestrator | 编排器按图执行 | 代码（编排器） | 固定流水线：抽取 → 校验 → 入库 | `pydantic_graph` 或纯 Python `async` |
| Handoff | Agent A 显式移交给 B | LLM（A 决定移交） | 客服分流（前台 → 技术支持 / 账单） | A 的结构化输出给出移交目标（自定义的 `Handoff` 模型），编排层据此调用 B |
| Message Bus | 多 Agent 监听 / 发布事件 | 事件 + 代码 | 多 Agent 并行 + 异步协作 | `asyncio.Queue` / Redis Pub-Sub |

官方文档按复杂度把多 agent 分成五级：①单 agent；②委派（工具里调 agent，或用 `SubAgents`）；③程序化交接（应用代码决定下一个调谁）；④图（官方的态度是非必要不用）；⑤深度 agent（规划 + 文件 + 委派 + 沙箱）。上表的四种模式对应前四级的写法，第五级见第 7 节和 [clai 命令行与 Harness 工具集](../04-modules/05-cli-harness.md)。

---

## 3. 模式 1：Agent-as-Tool（最常用）

最简单也最实用的写法，是把子 Agent 包成父 Agent 的一个工具：

```python
from pydantic_ai import Agent, RunContext

translator = Agent(
    "openai-chat:qwen-flash",
    instructions="你是一名翻译，把任何输入翻译成英文，只输出译文。",
)

main_agent = Agent(
    "openai:gpt-6.1-sol",
    instructions="你是助手。需要把内容翻译成英文时，调用 translate 工具。",
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

这段代码有几个地方值得看：`@main_agent.tool` 装饰的函数里直接 `await translator.run(...)`，不需要任何特殊 API；工具的 docstring 会自动作为 schema 描述交给模型，和 OpenAI Function Calling 的做法一样；子 Agent 是完全独立的，模型、system prompt、输出类型都可以和父 Agent 不同。

### 3.1 传递 usage（token 计费）

子 Agent 跑完后，token 不会自动汇总到父 Agent，生产环境一定要把 usage 传下去：

```python
@main_agent.tool
async def translate(ctx: RunContext, text: str) -> str:
    result = await translator.run(text, usage=ctx.usage)
    return result.output
```

`ctx.usage` 是父 Agent 当前 run 的累计 usage，传给子 Agent 后，子 Agent 的 token 会直接加到这个对象上，最终 `parent_result.usage` 就是父子的总和。

### 3.2 共享 deps

子 Agent 想用父 Agent 的 deps（比如数据库连接），就把它传过去：

```python
@main_agent.tool
async def translate(ctx: RunContext[DBConn], text: str) -> str:
    result = await translator.run(text, deps=ctx.deps, usage=ctx.usage)
    return result.output
```

子 Agent 也要声明同样的 deps 类型（或者更宽泛的父类）。

---

## 4. 模式 2：Orchestrator（编排器）

如果流程是确定的，下一步做什么不需要 LLM 来决定，那用 Agent-as-Tool 就多此一举了，直接用代码编排即可。

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

extractor = Agent("openai-chat:qwen-flash", output_type=ExtractedTicket)
validator = Agent("openai:gpt-6.1-sol", output_type=ValidatedTicket)

async def pipeline(raw_text: str) -> ValidatedTicket:
    step1 = await extractor.run(raw_text)
    step2 = await validator.run(
        f"校验并去重以下工单：{step1.output.model_dump_json()}",
        usage=step1.usage,  # 累加 usage
    )
    return step2.output
```

写法很简单，大多数线性流水线这样写就够了，用不着图框架。

### 4.2 用 pydantic_graph 编排（复杂分支）

流程里有分支、循环或状态时，可以用官方的 `pydantic_graph`：

```python
from dataclasses import dataclass
from pydantic_graph import BaseNode, End, GraphBuilder, GraphRunContext, StepContext

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
        r = await validator.run(f"校验：{ctx.state.extracted.model_dump_json()}")
        return End(r.output)

g = GraphBuilder(state_type=State, input_type=str, output_type=ValidatedTicket)

@g.step
async def start(ctx: StepContext[State, None, str]) -> Extract:
    return Extract()

g.add(g.node(Extract), g.node(Validate), g.edge_from(g.start_node).to(start))
graph = g.build()
output = await graph.run(state=State(text="..."), inputs="...")   # 直接返回 ValidatedTicket
print(output)
```

`pydantic_graph` 的节点是 dataclass，类型注解决定怎么连边（`-> Validate` 表示下一个节点）；状态对象也是 dataclass，在节点之间共享；用 `End[T]` 显式终止。builder 还支持分支、并行扇出和汇聚（见[Pydantic Graph 状态机](../04-modules/03-graph.md)）。V2 移除了图的持久化（`pydantic_graph.persistence`），需要扛住重启的话，用持久执行引擎或 harness 的 `StepPersistence`。

---

## 5. 模式 3：Handoff（移交）

这个模式来自 OpenAI Swarm：Agent A 返回一个特殊对象，告诉编排层接下来由 Agent B 接手对话。Pydantic AI 没有内置的 Handoff 类型，但用结构化输出加上编排层几行代码就能实现：

```python
from typing import Literal
from pydantic import BaseModel
from pydantic_ai import Agent

class Handoff(BaseModel):
    """指示编排层把对话移交给另一个 Agent。"""
    to: Literal["tech_support", "billing", "stay"]
    reason: str

frontdesk = Agent(
    "openai-chat:qwen-flash",
    output_type=Handoff,
    instructions=(
        "你是客服前台。判断用户问题是技术支持(tech_support)、"
        "账单(billing)还是闲聊(stay)。"
    ),
)

tech = Agent("openai:gpt-6.1-sol", instructions="你是技术支持专员。")
billing = Agent("openai:gpt-6.1-sol", instructions="你是账单专员。")

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

如果希望接手的 Agent 还能继续移交，把 `output_type` 设成 `Handoff | FinalAnswer` 联合类型，编排层循环判断即可：

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

适合多个 Agent 并行、异步协作的场景。比如做监控时，日志 Agent、告警 Agent、报表 Agent 各自订阅事件流。

最简单的实现就是 `asyncio.Queue`：

```python
import asyncio
from pydantic_ai import Agent

bus: asyncio.Queue = asyncio.Queue()

summarizer = Agent("openai-chat:qwen-flash", instructions="一句话总结日志。")
alerter = Agent("openai-chat:qwen-flash", instructions="判断是否需要告警。")

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

生产环境把 `asyncio.Queue` 换成 Redis Stream、NATS 或 Kafka 就行，Pydantic AI 本身不关心传输层。

---

## 7. 现成的委派：`SubAgents`、`CodeMode`、`DynamicWorkflow`（harness）

`SubAgents` 接收一组有名字的 agent，对模型暴露一个 `delegate_task(agent_name, task)` 工具。每次委派都是一次全新的、隔离的运行，看不到父对话，结果以 `str(result.output)` 返回。

```python
from pydantic_ai_harness import SubAgent, SubAgents
from pydantic_ai.usage import UsageLimits

reproducer = Agent("anthropic:claude-opus-5-5", name="reproducer", description="Reproduce the reported bug from a minimal script.")
librarian = Agent("anthropic:claude-opus-5-5", name="librarian", description="Find relevant docs, issues, and prior art.")

orchestrator = Agent(
    "anthropic:claude-opus-5-5",
    capabilities=[SubAgents(agents=[
        SubAgent(reproducer, usage_limits=UsageLimits(request_limit=35), timeout_seconds=600, max_calls=1),
        SubAgent(librarian, usage_limits=UsageLimits(request_limit=18), timeout_seconds=300, max_calls=2),
    ])],
)
```

默认情况下，父运行的 `deps` 会转发给每个子 agent（依赖类型要一致）；用量默认共享（父的 `UsageLimits` 管整棵树，`forward_usage=False` 改成各算各的）；父的工具不传下去（想要一模一样的子 agent 用 `include_self=True`，`Coder` 就是这么做的）；`shared_capabilities` 给所有子 agent 统一加 capability。每个子 agent 可单独设用量预算、墙钟超时、每次父运行最多委派几次。

`CodeMode` 针对的是普通工具调用的一个固有开销：每一批相互依赖的调用都要多一轮模型请求。`CodeMode()` 把可用工具收进一个 `run_code` 工具，模型改为写一段 Python，在沙箱里把工具当函数调用（可以用循环、条件、`asyncio.gather`），只把需要的结果返回。沙箱是 Pydantic 团队的 Monty 解释器（默认碰不到主机文件 / 环境变量 / 时钟，要访问文件用 `mount` 显式挂载）；代码里的工具调用仍会经过 Pydantic AI 分派，所以审批、hooks、护栏、用量计数照常生效；默认每片段 30 秒 / 256 MiB / 100 次嵌套工具调用。

`DynamicWorkflow` 思路相同，只是脚本里调用的是整个子 agent（每个子 agent 是一个 async 函数，名字就是 agent 的 `name`）：

```python
from pydantic_ai_harness import DynamicWorkflow
orchestrator = Agent("openai:gpt-6.1-sol", capabilities=[DynamicWorkflow(agents=[reviewer, summarizer])])
# 编排者用 run_workflow 写脚本：
# reports = await asyncio.gather(reviewer(task="..."), reviewer(task="..."))
# await summarizer(task="总结：" + "\n".join(reports))
```

中间结果留在沙箱里，只有最终结果回到编排者的上下文。怎么选：委派偶尔才发生，或者每个结果都要父 agent 看过再决定下一步，用 `SubAgents`；想把扇出、串联、投票、重试循环压进一次工具调用，用 `DynamicWorkflow`；拿不准就先用 `SubAgents`。用量方面，默认（`forward_usage=True`）子 agent 和父运行共用一个用量计数，也受父运行的 `usage_limits` 约束，但并发扇出时只是尽力而为；要给子 agent 单独的限额就设 `sub_agent_usage_limits`（它会替换父运行的限额），要精确限制子 agent 的运行次数用 `max_agent_calls`。另外工作流不能嵌套。这几个 capability 需要 `pip install "pydantic-ai-harness[code-mode,dynamic-workflow]"`。本章 demo 不包含这几个 capability，示例代码来自官方文档，对应 2.64 / 0.64 的 API。

---

## 8. 共享状态 & deps

四种模式都要解决怎么传上下文的问题，Pydantic AI 用的是 deps：

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

@dataclass
class AppDeps:
    db: "DBConn"
    user_id: int

agent = Agent("openai:gpt-6.1-sol", deps_type=AppDeps)

@agent.tool
async def lookup_order(ctx: RunContext[AppDeps], order_id: int) -> dict:
    return await ctx.deps.db.fetch_one(
        "SELECT * FROM orders WHERE id=$1 AND user_id=$2",
        order_id, ctx.deps.user_id,
    )

# 调用时
result = await agent.run("我的订单 1234 状态？", deps=AppDeps(db=..., user_id=42))
```

用 deps 时有几条规矩：

- `deps` 是强类型的，不要塞 dict
- 子 Agent 想用同样的 deps，也声明 `deps_type=AppDeps`，调用时传 `ctx.deps`
- 不要往 deps 里塞业务大对象，只放运行期依赖（连接池、用户身份、Feature Flag…）

---

## 9. 与 LangGraph 横向对比

常有人问为什么不直接用 LangGraph。两者覆盖的范围不一样：

| 维度 | LangGraph | Pydantic AI（含 pydantic_graph） |
|------|-----------|----------------------------------|
| 核心抽象 | StateGraph + Node + Edge | `Agent` + `pydantic_graph` 节点 |
| 状态类型系统 | `TypedDict` / `Annotated` reducer | Python dataclass / Pydantic 模型 |
| 检查点 / 持久化 | 内置 `Checkpointer` | 图本身不做快照；`StepPersistence` / 持久执行引擎 |
| 流式 UI 协议 | 自家 stream protocol | UI 事件流适配器（Vercel AI / AG-UI），见[Web Chat UI 与 UI 事件流](02-web-chat-ui.md) |
| 学习曲线 | 中（要理解 reducer / channel） | 低（Python + 类型即可） |
| 工具调用 | 通过 ToolNode（LangChain 1.x 另有 `create_agent`） | `@agent.tool` 装饰器 |
| 适合规模 | 大型有状态 Agent 系统 | 中小型 Agent + 复杂工作流也能搞 |

我的经验是：以单 Agent 为主、偶尔需要多 Agent 的，用 Pydantic AI；整个产品就是一个大型 Agent 系统（任务规划、长期记忆、多人协作）的，用 LangGraph。两者也可以混用，用 LangGraph 编排，节点内部跑 Pydantic AI 的 Agent。

---

## 10. 实战：客服三级分流

把前面的内容合起来，做一个由前台、技术支持、账单三个 Agent 组成的客服系统：

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
    "openai-chat:qwen-flash",
    output_type=Routing,
    instructions="你是前台分流员，给出 to 字段。",
)

tech = Agent("openai:gpt-6.1-sol", instructions="你是技术支持。")
billing = Agent("openai:gpt-6.1-sol", instructions="你是账单专员。")

AGENTS = {"tech": tech, "billing": billing}

async def handle(question: str) -> str:
    routing = await frontdesk.run(question)
    if routing.output.to == "self":
        return routing.output.reason
    target = AGENTS[routing.output.to]
    answer = await target.run(question, usage=routing.usage)
    return answer.output
```

完整 demo 见 [`demos/patterns/01_multi_agent.py`](../../demos/patterns/01_multi_agent.py)。

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 子 Agent 的 token 没算进总账 | 没传 `usage=ctx.usage` | 子 `agent.run(..., usage=ctx.usage)` |
| 子 Agent 想用父的 deps 但拿不到 | 没传 `deps=ctx.deps` 或 `deps_type` 没对齐 | 子 Agent 声明同样的 `deps_type` |
| 主 Agent 把子 Agent 当 tool 但模型总不调用 | 工具描述（docstring）写得太模糊 | 把工具用途和触发场景写清楚 |
| Handoff 死循环（A → B → A → B …） | 编排层没设置 hop 上限 | `for _ in range(MAX_HOPS)` |
| pydantic_graph 节点没跑 | 返回类型不是节点对象 / End | 用类型注解 `-> NodeB` 显式声明边 |
| Message Bus 模式下消费者卡死 | `queue.get()` 是阻塞协程没设超时 | 用 `asyncio.wait_for(...)` |
| 多 Agent 串行慢 | 实际可并行的步骤没用 `asyncio.gather` | 独立步骤 `await asyncio.gather(a.run(...), b.run(...))` |

---

## 12. 生产环境建议

1. 每个 Agent 的 system prompt 单独管理，别堆在代码里，做成模板文件或者放进 PromptHub
2. 子 Agent 调用一定传 usage，code review 时把这一条列为必查项，否则上线后成本统计会漏掉一大块
3. 加跳数或深度上限，不管是 Agent-as-Tool 还是 Handoff，都要有 `max_iterations` 兜底
4. 用 Logfire 做全链路追踪，Pydantic AI 原生集成了 Logfire，多 Agent 时 trace 视图能看清每一跳
5. 做好错误降级：子 Agent 重试耗尽抛 `UnexpectedModelBehavior`、超限抛 `UsageLimitExceeded` 时，父 Agent 要有回退策略（默认回答或转人工）
6. 模型分级：分类和路由用 mini 模型，深度任务用大模型，成本能差一个数量级

---

## 13. 本章 demo

完整可运行代码：[`demos/patterns/01_multi_agent.py`](../../demos/patterns/01_multi_agent.py)

下一篇：[Web Chat UI 与 UI 事件流](02-web-chat-ui.md)，讲怎样把 Agent 接到 Web 聊天界面上。
