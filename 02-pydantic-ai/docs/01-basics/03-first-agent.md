# Pydantic AI 03：第一个 Agent

> Pydantic AI 里几乎所有东西都挂在 `Agent` 上：模型、指令、工具、依赖、输出类型、capability 打包成一个可复用的对象，再通过同步、异步、流式、逐节点几种方式运行。这篇把它拆开看一遍。

以 `pydantic-ai 2.64.0` 为准。文中片段用占位模型名，demo 用千问。

---

## 1. Agent 做了什么

裸调 OpenAI 时你做的事情：

```python
client.chat.completions.create(
    model="gpt-6.1-sol",
    messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
    tools=[...],
)
# 收到回复 → 检查 tool_calls → 执行工具 → 拼回去再请求 → 直到没有 tool_calls
```

这个循环，加上参数封装、Schema 生成、重试逻辑，Pydantic AI 都包进了一个对象：

```python
agent = Agent("openai:gpt-6.1-sol", instructions="...", output_type=Foo, deps_type=Bar)
result = agent.run_sync("用户消息", deps=bar_instance)
```

可以把 `Agent` 看成一个带类型的 LLM 调用循环。官方的建议是把它当 FastAPI 应用一样复用：建一次，全局用，每次 `run` 是独立的一次运行。循环里每一步可能是：

1. 把当前 messages 发给模型
2. 模型回 text / 结构化输出 → 校验通过则返回
3. 模型回 tool_call → 执行工具 → tool_return 加入 messages → 回到第 1 步

---

## 2. Agent 里装了什么

官方把 `Agent` 描述成一个容器：

| 组件 | 是什么 | 详见 |
|------|--------|------|
| instructions | 开发者写给模型的指令 | 本篇 |
| 函数工具、toolsets | 模型可以调用的函数 | [Function Tools 函数工具](../02-tools/01-function-tools.md)、[Toolsets 工具集](../02-tools/03-toolsets.md) |
| 输出类型 `output_type` | 运行结束时模型必须交出的类型 | [结构化输出（Output Types）](06-output-types.md) |
| 依赖类型 `deps_type` | instructions、工具、输出函数运行时能拿到的依赖 | [依赖注入（Dependencies）](05-dependencies.md) |
| 模型 | 默认模型，运行时也可以另指定 | [模型与 Provider 全览](04-models-providers.md) |
| 模型设置 `model_settings` | 温度、超时等默认设置，运行时也可覆盖 | [模型与 Provider 全览](04-models-providers.md) |
| capabilities | 可复用的「工具 + hooks + 指令 + 设置」组合 | [Capabilities（能力）与 ModelProfile](../03-advanced/06-capabilities.md) |

`Agent` 是泛型的：依赖类型是 `int`、输出类型是 `bool` 的 agent，类型就是 `Agent[int, bool]`。工具的 `RunContext[int]` 和 agent 的 `deps_type=int` 对不上时，mypy / pyright 会直接报错。

### 构造参数（2.64.0 的签名）

```python
from pydantic_ai import Agent

agent = Agent(
    model="openai:gpt-6.1-sol",     # 模型字符串或 Model 对象；也可以省略，运行时再传
    output_type=str,                # 输出类型（默认 str）
    instructions="...",             # 指令：字符串 / 函数 / 列表（推荐）
    system_prompt=(),               # 系统提示（保留在历史里；一般用 instructions）
    deps_type=object,               # 依赖类型（默认 object）
    name=None,                      # agent 名字，进 trace / 多 agent 场景有用
    model_settings=None,            # 温度、max_tokens、超时……
    retries=None,                   # 重试预算：int，或 {"tools": 3, "output": 2}；不传时默认 1 次
    tools=(), toolsets=None,        # 函数工具 / 工具集
    capabilities=None,              # V2 核心：能力列表
    end_strategy="graceful",        # 模型同一回复里既有工具调用又有最终输出时的策略
    tool_timeout=None, max_concurrency=None, metadata=None,
    defer_model_check=False,        # True：创建时不检查模型/凭证，第一次运行才检查
)
```

> V1 的 `output_retries=`、`instrument=`、`result_type=` 在 V2 都没有了：重试预算统一成 `retries`（int 或字典，见下文）；可观测性靠 `Instrumentation` capability 或 `logfire.instrument_pydantic_ai()`。

最常用的是这 4 个：`model` / `instructions` / `output_type` / `deps_type`。

### `model`

可以是：

- 字符串：`"openai:gpt-6.1-sol"` / `"anthropic:claude-sonnet-5-5"` / `"google:gemini-..."`；千问等 OpenAI 兼容端点用 `"openai-chat:qwen-flash"`（见[模型与 Provider 全览](04-models-providers.md)）
- Model 对象：`OpenAIChatModel("qwen-flash", provider=OpenAIProvider(base_url=..., api_key=...))`
- 测试模型：`TestModel()`（`Agent("test")`）/ `FunctionModel(fn)`

### `instructions` 与 `system_prompt`

两者都是给模型的系统级指令，区别只有一个：传入了 `message_history` 时，`instructions` 只用当前 agent 的，历史消息里旧的 instructions 不会再发给模型；`system_prompt` 则会保留历史里的那些系统提示。官方建议除非有特别理由，都用 `instructions`。

```python
Agent(
    model,
    instructions=(
        "你是一位资深 Python 工程师。",
        "回答要简洁，必要时给代码示例。",
    ),
)
```

> `system_prompt=` 在 V2 里仍然能用，但新代码请用 `instructions=`。

---

## 3. 运行方式

| 入口 | 形态 | 用途 |
|------|------|------|
| `agent.run_sync(prompt)` | 同步，返回 `AgentRunResult` | 脚本、Jupyter、单测 |
| `await agent.run(prompt)` | 异步，返回 `AgentRunResult` | FastAPI、并发任务（默认选择） |
| `async with agent.run_stream(prompt) as r` | 异步流，`StreamedRunResult` | 边生成边展示文本 / 结构化输出 |
| `async with agent.run_stream_events(prompt) as ev` | 异步流，产出所有事件 | 要看到工具调用、思考过程等每个事件 |
| `async with agent.iter(prompt) as run` | 逐节点迭代 | 要在某一步插入自己的逻辑 |

### 3.1 `run_sync`：最常用

```python
result = agent.run_sync("Python GIL 是什么？")
print(result.output)          # 最终回复
print(result.usage)           # token 用量（V2：属性，不是方法）
print(result.all_messages())  # 完整对话历史
```

> `result.usage()` 在 V2 会触发弃用警告：`usage` 现在是属性。

### 3.2 `run`：异步版

```python
import asyncio

async def main():
    result = await agent.run("Python GIL 是什么？")
    print(result.output)

asyncio.run(main())
```

`run_sync` 内部会自己跑事件循环，所以在已经运行的事件循环里（async 函数、Jupyter）别用它，会报 `RuntimeError: This event loop is already running`。工具函数里也会碰到这个错：工具是 async 的时候，里面不能再调 `agent.run_sync(...)` 或 `embedder.embed_query_sync(...)`，要 `await` 异步版本。

### 3.3 `run_stream`：流式

```python
async def main():
    async with agent.run_stream("讲个长故事") as response:
        async for text in response.stream_text(delta=True):
            print(text, end="", flush=True)
        # 流结束后拿最终对象（注意：StreamedRunResult 没有 .output 属性）
        final = await response.get_output()
```

- `stream_text(delta=True)` 拿增量；`delta=False` 拿累积全文
- 流式 + 结构化输出：`response.stream_output()` 返回部分校验过的对象
- `run_stream` 把第一个符合输出类型的结果当作最终结果开始流式输出；同一次回复里排在它之后的工具调用不会执行。`run()` 不一样，按 `end_strategy` 处理（V2 默认 `'graceful'`：按模型发出的顺序执行，排在输出之前的函数工具会先跑完）

### 3.4 `run_stream_events` 与 `iter`

`run_stream_events()` 产出所有事件：`PartStartEvent` / `PartDeltaEvent` / `FinalResultEvent` / `FunctionToolCallEvent` / ... / `AgentRunResultEvent`，适合在聊天 UI 里实时显示工具调用。

`iter()` 返回一个 `AgentRun`，可以逐个拿到图节点，因为 Pydantic AI 的 agent 循环本身就是一张图：

```python
async with agent.iter("北京天气？") as agent_run:
    async for node in agent_run:
        print(type(node).__name__)
# UserPromptNode → ModelRequestNode → CallToolsNode → ModelRequestNode → CallToolsNode → End
print(agent_run.result.output)
```

也可以手动 `agent_run.next(node)` 一步步推进，在某个节点执行前检查、修改、甚至跳过。大多数时候用不到。

### 3.5 取消与整体超时

用户点了「停止」，要取消正在跑的运行：

```python
import asyncio
from pydantic_ai import Agent, CancellationToken, RunCancelled

async def main():
    token = CancellationToken()
    task = asyncio.create_task(agent.run("写一篇长文", cancellation_token=token))
    ...
    token.cancel()        # 幂等、线程安全；一个 token 可以管多个运行；token 单次有效
    try:
        await task
    except RunCancelled as exc:
        messages = exc.all_messages()                 # 取消前已完成的部分，含已完成的工具结果
        await agent.run("继续", message_history=messages)   # 可以从这里接着跑
```

`run_sync()` 也接受这个 token，从另一个线程调 `cancel()` 是中断阻塞中的同步运行的唯一办法。任何运行方法还接受 `timeout=`（整个运行的墙钟上限），超时抛 `RunTimedOut`（`RunCancelled` 的子类）。

> 旧写法 `await response.cancel()`（取消 `run_stream` 的流）在 V2 仍然存在，但官方推荐 `CancellationToken`：它能从运行外部、别的线程、`run_sync` 上取消，并带回可续跑的历史。

---

## 4. `AgentRunResult` 详解

`run_sync` 和 `run` 都返回 `AgentRunResult`：

| 成员 | 说明 |
|------|------|
| `output` | 最终输出（按 `output_type` 校验过） |
| `all_messages()` / `new_messages()` | 完整历史 / 本次新增 |
| `all_messages_json()` / `new_messages_json()` | JSON 字节 |
| `usage` | 本次运行累计用量 `RunUsage`（属性） |
| `conversation` | `Conversation` 对象，带消息 + 累计用量，续接对话用 |
| `response` | 最后一条 `ModelResponse` |
| `metadata` / `run_id` / `timestamp` | 运行元数据 |

`output` 的类型和 `output_type` 一一对应：

```python
agent = Agent("openai:gpt-6.1-sol", output_type=int)
r = agent.run_sync("2+3=?")
# r.output 是 int，不是 "5"
```

---

## 5. 一次运行 vs 一段对话

一次运行里可以交换任意多条消息，一段对话也可以由多次运行组成：

```python
result1 = agent.run_sync("Who was Albert Einstein?")
result2 = agent.run_sync(
    "What was his most famous equation?",
    conversation=result1.conversation,   # 不传的话，模型不知道「他」指谁
)
```

也可以传原始消息列表 `message_history=result1.all_messages()`，详见[消息与对话历史（Messages & History）](07-messages-history.md)。

---

## 6. 动态指令

静态 `instructions` 适合固定不变的指令。要根据 deps 或运行时情况决定，用 `@agent.instructions`：

```python
from dataclasses import dataclass
from datetime import date
from pydantic_ai import Agent, RunContext

@dataclass
class UserCtx:
    user_id: str
    name: str

agent = Agent("openai:gpt-6.1-sol", deps_type=UserCtx, instructions="你是一位客服。")   # 静态

@agent.instructions                                            # 动态：每次运行时计算
def add_user_info(ctx: RunContext[UserCtx]) -> str:
    return f"当前用户：{ctx.deps.name}（ID={ctx.deps.user_id}）"

@agent.instructions
def add_date() -> str:                                         # 不需要 RunContext 也行
    return f"今天是 {date.today()}"

result = agent.run_sync("我是谁？", deps=UserCtx("u1", "Ethan"))
# 运行时还能追加：agent.run_sync(..., instructions="用法语回答")
```

按定义顺序拼起来；动态 instructions 返回空字符串时不会添加任何内容。capability 和 toolset 也可以贡献 instructions，每个来源是一个独立的 `InstructionPart`。

动态指令每次 `run` 都会执行一次，别在里面做重活。

还有一个和提示词缓存有关的细节。默认情况下，一条 instruction 的文本变了，会改写整个指令前缀，provider 就没法复用之前缓存的对话；像待办清单、记忆这种在对话中途不断变化的内容，官方提供了「追加变更」的模式。

> 旧的装饰器 `@agent.system_prompt` 仍然存在，语义是「保留在历史里的系统提示」；新代码优先用 `@agent.instructions`。

---

## 7. 用量上限

模型可能陷入循环、不停调工具。`UsageLimits` 在运行时传入，超限抛 `UsageLimitExceeded`：

```python
from pydantic_ai import Agent, UsageLimitExceeded, UsageLimits

try:
    agent.run_sync("用一段话介绍意大利首都", usage_limits=UsageLimits(output_tokens_limit=10))
except UsageLimitExceeded as e:
    print(e)   # Exceeded the output_tokens_limit of 10 (output_tokens=32). ...
```

能限的东西：请求次数（`request_limit`，防无限循环）、成功执行的工具调用次数（`tool_calls_limit`）、输入输出 token、费用。`tool_calls_limit` 在执行工具之前检查：模型一次发了多个并行调用、合起来会超限时，一个都不执行。费用之所以能算，是因为 Pydantic AI 内置了模型价格表，每个 `ModelResponse` 的用量里直接带 `cost`（新模型发布后可用 `prices.update_in_background()` 更新）。

---

## 8. 实战 1：天气助手

```python
from dotenv import load_dotenv
from pydantic_ai import Agent

load_dotenv()

agent = Agent(
    "openai-chat:qwen-flash",
    instructions="你是一位天气助手，可以调用 get_weather 工具。",
)

@agent.tool_plain
def get_weather(city: str) -> str:
    """查询城市天气"""
    db = {"北京": "晴 26°C", "上海": "多云 24°C", "杭州": "雨 19°C"}
    return db.get(city, "未知")

result = agent.run_sync("北京和杭州哪里更凉快？")
print(result.output)
# > 杭州（19°C）比北京（26°C）更凉快。
```

- 模型自己决定调几次 `get_weather`
- `result.all_messages()` 会看到完整的 user → model_tool_call → tool_return → model_text 链
- 工具里抛的普通异常会中断整个运行；想让模型自己改正，抛 `ModelRetry`（预算默认 1 次，见下文）

---

## 9. 实战 2：结构化计算器

```python
from pydantic import BaseModel
from pydantic_ai import Agent

class CalcResult(BaseModel):
    expression: str
    answer: float
    explanation: str

agent = Agent(
    "openai-chat:qwen-flash",
    output_type=CalcResult,
    instructions="解析数学表达式并返回结构化结果",
)

r = agent.run_sync("帮我算 (12 + 8) * 3 - 5")
print(r.output)
# CalcResult(expression='(12 + 8) * 3 - 5', answer=55.0, explanation='...')
```

这里没写一行 JSON 解析代码，Pydantic AI 替你做了这几件事：

- 把 `CalcResult` 的 schema 喂给模型（默认走工具调用形式的「输出工具」，也可选原生 JSON Schema / 提示词模式，见[结构化输出（Output Types）](06-output-types.md)）
- 拿到结果后用 Pydantic 校验
- 校验失败时把错误反馈给模型，让它重试

---

## 10. 重试：七个层面

一次运行里说的「重试」，可能发生在七个不同层面，预算互不共享：

| 层面 | 重试的是什么 | 模型看得到吗 |
|------|-------------|-------------|
| 传输层 | 同一个 HTTP 请求（自己装 tenacity 传输） | 看不到 |
| provider SDK | 同一个 HTTP 请求，由 SDK 重发 | 看不到 |
| 持久执行引擎 | 整个模型请求，由 Temporal 等重放 | 看不到 |
| 模型回退 | 同一个请求换一个模型（`FallbackModel` / `ModelFallback`） | 看不到 |
| 工具 | 一次工具调用：把错误告诉模型让它改 | 看得到 |
| 输出 | 模型的最终答案：把校验错误告诉模型让它改 | 看得到 |
| 模型请求 hook | hook 里抛 `ModelRetry` | 看得到 |

只有后三种是「agent 层面的重试」，每次都多付一轮模型调用。预算默认 1 次，可以按类别分别设：

```python
agent = Agent("openai:gpt-6.1-sol", retries={"tools": 3, "output": 2})

@agent.tool(retries=2)          # 单个工具也能单独设
def get_user_by_name(ctx: RunContext[DatabaseConn], name: str) -> int:
    """Get a user's ID from their full name."""
    user_id = ctx.deps.users.get(name)
    if user_id is None:
        raise ModelRetry(f"No user found with name {name!r}, remember to provide their full name")
    return user_id
```

预算用完抛 `UnexpectedModelBehavior`；用 `capture_run_messages()` 包住运行，出错时能拿到这次运行交换过的全部消息，方便排查。层与层之间不共享预算，但次数会相乘：输出重试 2 次、SDK 默认重试 2 次、传输层再重试 2 次，每层都是 1 次原始请求加 2 次重试，最坏情况下一个最终答案要在网络上发出 3 × 3 × 3 = 27 个请求，所以 `UsageLimits` 和各层超时要按最坏情况定（详见 [HTTP 重试与 ModelRetry](../03-advanced/07-retries-http.md)）。

---

## 11. 测试：`TestModel` 与 `FunctionModel`

不想花 API 费用、不想等网络，就用 `TestModel`：

```python
from pydantic_ai.models.test import TestModel

agent = Agent(TestModel(), output_type=CalcResult)     # 等价于 Agent("test", ...)
r = agent.run_sync("随便问")
# TestModel 会自动生成符合 schema 的"假"对象；字符串字段都是 'a'，数字是 0
```

要模拟模型先调工具再回答的序列，用 `FunctionModel`：

```python
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

def fake_call(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="假装的回答")])

agent = Agent(FunctionModel(fake_call))
print(agent.run_sync("hi").output)  # 假装的回答
```

这两个模型是写测试的基础，完全不联网，见 [用 TestModel、FunctionModel 与 override 测试 Agent](../05-patterns/03-testing.md)。

---

## 12. 声明式：Agent Spec

V2 还支持把 agent 写成 YAML / JSON，把配置和代码分开：

```yaml
# agent.yaml
model: anthropic:claude-opus-5-5
instructions: You are a helpful assistant.
capabilities:
  - WebSearch
  - Thinking:
      effort: high
```

```python
from pydantic_ai import Agent

agent = Agent.from_file("agent.yaml")
```

模板字符串、自定义 capability 注册等见官方「Agent Specs」。

---

## 13. 全局默认模型

Pydantic AI 没有全局的 `set_default_model`，可以抽一个工厂函数：

```python
def make_agent(**kwargs):
    return Agent("openai-chat:qwen-flash", **kwargs)

agent = make_agent(output_type=Invoice)
```

---

## 14. vs LangChain

| 任务 | LangChain 1.x | Pydantic AI |
|------|---------------|-------------|
| 系统提示 | `create_agent(..., system_prompt="...")` | `Agent(..., instructions="...")` |
| 工具 | `@tool` + `create_agent(tools=[...])` | `@agent.tool_plain` 直接装饰（或 `tools=[...]`） |
| 结构化输出 | `create_agent(response_format=Schema)` | `Agent(..., output_type=Schema)` |
| 工具调用循环 | `create_agent` 内置（底层是 LangGraph） | `agent.run_sync(...)` 内置 |
| 扩展 | middleware | capability |
| 流式 | `agent.stream(...)` | `agent.run_stream(...)` |
| 异步 | `agent.ainvoke(...)` | `agent.run(...)` |

LangChain 1.x 里等价的 Hello Agent：

```python
from langchain.agents import create_agent
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """查询城市天气"""
    return {"北京": "晴 26°C"}.get(city, "未知")

agent = create_agent("openai:gpt-6.1-sol", tools=[get_weather])
result = agent.invoke({"messages": [{"role": "user", "content": "北京天气"}]})
```

> `langgraph.prebuilt.create_react_agent` 在 LangChain 1.x 里已被 `langchain.agents.create_agent` 取代。

几处差别：

- LangChain 的工具和 agent 是分开定义再组装；Pydantic AI 可以直接 `@agent.tool_plain`，工具与 agent 绑定
- 输入格式：LangChain 要 `{"messages": [...]}`，Pydantic AI 直接 `"...prompt..."`
- Pydantic AI 的 `deps_type` / `output_type` 是带类型参数的泛型，类型检查器能查

---

## 15. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| `RuntimeError: This event loop is already running`（Jupyter / async 函数 / async 工具里用 `run_sync`） | `run_sync` 要自己开事件循环 | 用 `await agent.run(...)`；同步 demo 放在 `asyncio.run` 之外 |
| `AttributeError: 'StreamedRunResult' object has no attribute 'output'` | `run_stream` 的结果没有 `.output` | `await response.get_output()` |
| `PydanticAIDeprecationWarning: usage is a property` | V2 里 `usage` 不再是方法 | `result.usage` |
| `TypeError: Agent.__init__() got an unexpected keyword argument 'output_retries'` | V2 去掉了 `output_retries` | `retries={"output": N}` |
| 模型没配 key，`Agent("openai:...")` 一创建就报错 | V2 创建时就检查凭证 | `defer_model_check=True`，或先用 `Agent("test")` |
| `instructions` 设了但模型不听 | 模型温度过高 / 指令太短 | 先把指令写具体；GPT、千问可以加 `model_settings={"temperature": 0}`，Claude 5.x 不接受 `temperature`（会返回 400） |
| `result.output` 与预期类型不符 | `output_type` 写成了联合 / 列表却没包一层 | 见[结构化输出（Output Types）](06-output-types.md) |
| 工具反复失败，最后抛 `UnexpectedModelBehavior` | 工具每次都抛 `ModelRetry`，重试预算用光 | 对模型改不了的错误（如数据不存在），在工具里返回说明性字符串，别抛 `ModelRetry`；同时设 `UsageLimits(request_limit=...)` 兜底 |
| 同一个 agent 全局共享导致 deps 串台 | 工具闭包了外部变量 | 每个请求 `agent.run(deps=...)`，工具用 `ctx.deps` 拿 |

---

## 16. 本章 demo

完整可运行代码：[`demos/basics/03_first_agent.py`](../../demos/basics/03_first_agent.py)（含 run_sync / run / run_stream / 动态指令 / 结构化输出 / 工具循环）

下一章：[Pydantic AI 04：模型与 Provider 全览](04-models-providers.md)。
