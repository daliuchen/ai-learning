# Agent 配置详解

> `Agent(...)` 有十来个参数。记的时候可以按用途分三类：控制它怎么说（instructions、model、model_settings），它能调什么（tools、handoffs），以及它最后交出什么（output_type、guardrails）。

---

## 1. 全参数一览

```python
from agents import Agent
from agents.model_settings import ModelSettings


agent = Agent(
    name="MyAgent",                     # 必填，trace 里看名字
    instructions="你是助手...",          # 系统指令（字符串 / 函数）
    handoff_description="一句话说明我擅长什么",  # 别的 Agent 把我当 handoff 目标时，给模型看的描述
    model="gpt-6-luna",                # 模型 ID 或 Model 对象；不写 → SDK 默认 gpt-5.6-luna
    model_settings=ModelSettings(...),  # temperature / timeout / retry 等
    tools=[...],                        # 工具列表
    mcp_servers=[...],                  # MCP 服务（见 06-integration/01-mcp）
    handoffs=[...],                     # 可委派的 Agent
    output_type=Recipe,                 # 结构化输出
    tool_use_behavior="run_llm_again",  # 工具结果怎么处理（见第 10 节）
    input_guardrails=[...],             # 输入守卫
    output_guardrails=[...],            # 输出守卫
    hooks=...,                          # 生命周期 hooks
)
```

> 0.24 的 `Agent` 构造参数还有 `prompt`（引用 OpenAI 平台上托管的提示词模板，仅 Responses）、`mcp_config`、`reset_tool_choice`。`Agent` 对上下文类型是泛型的：`Agent[MyCtx]`。

---

## 2. instructions：系统指令

可以是字符串，也可以是函数：

### 静态字符串

```python
agent = Agent(name="A", instructions="你是技术顾问，回答简洁。")
```

### 动态函数（按上下文返回）

```python
def make_instr(ctx, agent):
    user = ctx.context["user_name"]
    return f"用户名是 {user}，称呼他/她。"

agent = Agent(name="A", instructions=make_instr)
```

函数形式适合按用户、时间或 A/B 实验分组来拼 prompt。

---

## 3. model：模型选择

### 字符串（默认走 OpenAI）

```python
Agent(name="A", model="gpt-6-luna")
Agent(name="A", model="gpt-6.1-sol")
```

### 默认模型

`RunConfig(model=...)` 设了就会覆盖这次 run 里所有 agent 的 `model`。没设 RunConfig、agent 也没写 `model` 时，先看环境变量 `OPENAI_DEFAULT_MODEL`，再退到 SDK 内置默认（0.20 起 `gpt-5.6-luna`，`reasoning.effort="none"`）。想让行为稳定，就显式写 model：SDK 的内置默认在不同版本里换过，升级时很容易被悄悄换掉。

### 接非 OpenAI 模型

```python
# 方式 1：OpenAI 兼容端点（千问 / DeepSeek / 本地 vLLM），走 Chat Completions
from agents import OpenAIChatCompletionsModel
Agent(name="A", model=OpenAIChatCompletionsModel(model="qwen-flash", openai_client=client))

# 方式 2：适配器（Any-LLM / LiteLLM）
from agents.extensions.models.any_llm_model import AnyLLMModel
Agent(name="A", model=AnyLLMModel(model="anthropic/claude-sonnet-5-5"))
```

详见 [Multi-provider：接千问 / Claude / Gemini / 本地模型](../05-advanced/04-multi-provider.md)。

---

## 4. model_settings：采样参数

```python
from agents.model_settings import ModelSettings

agent = Agent(
    name="A",
    model="gpt-6-luna",
    model_settings=ModelSettings(
        temperature=0.3,
        top_p=0.9,
        max_tokens=1024,
        parallel_tool_calls=True,
        tool_choice="auto",      # 或 "required" / "none" / 具体工具名字符串如 "get_weather"
        timeout=30.0,            # 单次模型调用的超时（秒）
    ),
)
```

| 参数 | 说明 | 常用值 |
|------|------|--------|
| temperature | 随机性 | 0（确定）/ 0.7（创意）|
| top_p | 核采样 | 0.9 默认 |
| max_tokens | 输出上限 | 视任务 |
| parallel_tool_calls | 允许同时调多个 tool | True |
| tool_choice | 强制 / 自动 / 禁用 tool | "auto" |
| timeout | 单次模型调用超时（不含工具执行和重试退避） | 30.0 |
| retry | 模型调用的重试策略（opt-in，见 [错误处理与重试](../07-production/03-error-handling.md)） | `ModelRetrySettings(...)` |
| include_usage | 流式 + 第三方端点时要打开才有 token 用量 | True |
| reasoning / verbosity | 推理模型（GPT-5 系列起）的推理强度、输出详尽度 | `Reasoning(effort="low")` |

`ModelSettings` 是 dataclass，也可以传 dict（0.19 起统一支持，未知字段会报错）。Agent 上的设置和 `RunConfig(model_settings=...)` 会合并，冲突时以 RunConfig 为准。

---

## 5. tools：工具列表

```python
from agents import function_tool

@function_tool
def now() -> str:
    return "2026-05-20"

agent = Agent(name="A", instructions="...", tools=[now])
```

工具的写法见 [Function Tools：@function_tool 装饰器](../02-tools/01-function-tools.md)。

---

## 6. handoffs：可委派 Agent

```python
billing = Agent(name="Billing", instructions="...")
support = Agent(name="Support", instructions="...")

triage = Agent(
    name="Triage",
    instructions="分流到对应专家",
    handoffs=[billing, support],
)
```

详见 [Handoffs 概念：与 Tool、Sub-Agent 的区别](../03-handoffs/01-handoffs-concept.md)。

---

## 7. output_type：结构化输出

```python
from pydantic import BaseModel

class Sentiment(BaseModel):
    label: str   # "positive" / "negative" / "neutral"
    score: float


agent = Agent(
    name="Sentiment",
    instructions="判断情感倾向",
    output_type=Sentiment,
)
```

`result.final_output` 就是 `Sentiment` 实例；不设 `output_type` 时它是字符串。

---

## 8. input_guardrails / output_guardrails

```python
from agents import input_guardrail, GuardrailFunctionOutput


@input_guardrail
async def block_long(ctx, agent, user_input: str):
    return GuardrailFunctionOutput(
        tripwire_triggered=len(user_input) > 1000,
        output_info={"len": len(user_input)},
    )


agent = Agent(
    name="A",
    instructions="...",
    input_guardrails=[block_long],
)
```

详见 [Input Guardrails：输入守卫](../04-guardrails/01-input-guardrails.md)。

---

## 9. hooks：生命周期回调

```python
from agents import AgentHooks


class MyHooks(AgentHooks):
    async def on_start(self, ctx, agent): ...
    async def on_handoff(self, ctx, agent, source): ...
    async def on_tool_start(self, ctx, agent, tool): ...
    async def on_tool_end(self, ctx, agent, tool, result): ...
    async def on_llm_start(self, ctx, agent, system_prompt, input_items): ...   # 0.x 后期新增
    async def on_llm_end(self, ctx, agent, response): ...


agent = Agent(name="A", instructions="...", hooks=MyHooks())
```

详见 [Lifecycle Hooks：生命周期钩子](../05-advanced/03-lifecycle-hooks.md)。

---

## 10. tool_use_behavior：工具结果要不要再交给模型

```python
Agent(name="A", tools=[get_weather], tool_use_behavior="run_llm_again")        # 默认：工具跑完，模型再组织一次回答
Agent(name="A", tools=[get_weather], tool_use_behavior="stop_on_first_tool")   # 第一个工具的输出直接当 final_output
from agents import StopAtTools
Agent(name="A", tools=[a, b], tool_use_behavior=StopAtTools(stop_at_tool_names=["b"]))  # 调到 b 就停
# 也可以传自定义函数，自己决定是否终止
```

如果工具的返回值本身就是答案，这样可以省掉一次 LLM 调用。另外，当输出守卫拦下这种直接作为最终结果的工具输出时，0.22 起 SDK 会在 session 里把它替换成固定文本 `Output withheld by an output guardrail.`，避免泄露。

---

## 11. clone：派生 Agent

```python
chinese_agent = agent.clone(
    instructions="用中文回答",
    model="gpt-6-luna",
)
```

常用来做 A/B 实验的变体或多语言版本。

---

## 12. 几种常见组合

### A：工具型 Agent

```python
Agent(
    name="WeatherBot",
    instructions="用 get_weather 查天气",
    model="gpt-6-luna",
    tools=[get_weather],
)
```

### B：分流 Agent（Triage）

```python
Agent(
    name="Triage",
    instructions="按主题分流",
    model="gpt-6-luna",
    handoffs=[billing, support],
    input_guardrails=[detect_pii],
)
```

### C：信息抽取

```python
Agent(
    name="Extractor",
    instructions="抽取联系人信息",
    model="gpt-6-luna",
    model_settings=ModelSettings(temperature=0),
    output_type=Contact,
)
```

### D：研究型（带 web_search）

```python
from agents import WebSearchTool   # hosted tool：仅 OpenAI Responses 模型可用，不能配 qwen 等第三方

Agent(
    name="Researcher",
    instructions="研究问题，引用源",
    model="gpt-6.1-sol",
    tools=[WebSearchTool()],
)
```

---

## 13. 常见坑

| 坑 | 表现 | 解 |
|----|------|----|
| `instructions` 没说怎么用 tool | 模型不调 tool | 显式 "When asked X, use Y tool" |
| `output_type` 想用别的类型 | 担心不支持 | Pydantic / dataclass / TypedDict / `list[...]` 都支持（`Contact` dataclass、`list[TypedDict]` 都能直接当 output_type）；也可用 `AgentOutputSchema(..., strict_json_schema=False)` 放宽 |
| 想串行调用工具 | 模型一次返回好几个 tool call | `ModelSettings(parallel_tool_calls=False)`；它控制的是模型能不能一次发多个调用，SDK 同时执行几个由 `RunConfig(tool_execution=ToolExecutionConfig(max_function_tool_concurrency=N))` 控制 |

---

## 14. 下一步

- [Runner 的三种运行方式：run / run_sync / run_streamed](./04-runner.md)
- [RunResult 与 Usage：结果、中间步骤与 token 用量](./05-run-result.md)
- [Sessions：会话状态自动管理](./06-sessions.md)
