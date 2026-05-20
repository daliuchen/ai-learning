# Agent 配置详解

> **一句话**：`Agent(...)` 有 10 来个参数，按"我能控制它**说啥**、**调谁**、**给啥**"三类分组就好记。

---

## 1. 全参数一览

```python
from agents import Agent
from agents.model_settings import ModelSettings


agent = Agent(
    name="MyAgent",                     # 必填，trace 里看名字
    instructions="你是助手...",          # 系统指令
    model="gpt-4o-mini",                # 模型 ID 或 Model 对象
    model_settings=ModelSettings(...),  # temperature 等
    tools=[...],                        # 工具列表
    handoffs=[...],                     # 可委派的 Agent
    output_type=Recipe,                 # 结构化输出
    input_guardrails=[...],             # 输入守卫
    output_guardrails=[...],            # 输出守卫
    hooks=...,                          # 生命周期 hooks
)
```

---

## 2. instructions：系统指令

支持字符串 / 动态函数：

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

适合：根据用户、时间、A/B test 变体动态拼 prompt。

---

## 3. model：模型选择

### 字符串（默认走 OpenAI）

```python
Agent(name="A", model="gpt-4o-mini")
Agent(name="A", model="gpt-4o")
Agent(name="A", model="o1-mini")
```

### 通过 LiteLLM 接其它 provider

```python
from agents.extensions.models.litellm_model import LitellmModel

Agent(
    name="A",
    model=LitellmModel("anthropic/claude-sonnet-4-6"),
)
```

详见 [05-advanced/04-multi-provider.md](../05-advanced/04-multi-provider.md)。

---

## 4. model_settings：采样参数

```python
from agents.model_settings import ModelSettings

agent = Agent(
    name="A",
    model="gpt-4o-mini",
    model_settings=ModelSettings(
        temperature=0.3,
        top_p=0.9,
        max_tokens=1024,
        parallel_tool_calls=True,
        tool_choice="auto",      # 或 "required" / "none" / {"type": "function", "name": "x"}
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

---

## 5. tools：工具列表

```python
from agents import function_tool

@function_tool
def now() -> str:
    return "2026-05-20"

agent = Agent(name="A", instructions="...", tools=[now])
```

工具规范见 [02-tools/01-function-tools.md](../02-tools/01-function-tools.md)。

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

详见 [03-handoffs/01-handoffs-concept.md](../03-handoffs/01-handoffs-concept.md)。

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

`result.final_output` 就是 `Sentiment` 实例。

不设 `output_type` 时，`final_output` 是字符串。

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

详见 [04-guardrails/01-input-guardrails.md](../04-guardrails/01-input-guardrails.md)。

---

## 9. hooks：生命周期回调

```python
from agents.lifecycle import AgentHooks


class MyHooks(AgentHooks):
    async def on_start(self, ctx, agent): ...
    async def on_handoff(self, ctx, agent, source): ...
    async def on_tool_start(self, ctx, agent, tool): ...
    async def on_tool_end(self, ctx, agent, tool, result): ...


agent = Agent(name="A", instructions="...", hooks=MyHooks())
```

详见 [05-advanced/03-lifecycle-hooks.md](../05-advanced/03-lifecycle-hooks.md)。

---

## 10. clone：派生 Agent

```python
chinese_agent = agent.clone(
    instructions="用中文回答",
    model="gpt-4o-mini",
)
```

适合做 A/B test 变体或者多语言版本。

---

## 11. 配置组合套路

### 套路 A：经典工具型 Agent

```python
Agent(
    name="WeatherBot",
    instructions="用 get_weather 查天气",
    model="gpt-4o-mini",
    tools=[get_weather],
)
```

### 套路 B：分流 Agent（Triage）

```python
Agent(
    name="Triage",
    instructions="按主题分流",
    model="gpt-4o-mini",
    handoffs=[billing, support],
    input_guardrails=[detect_pii],
)
```

### 套路 C：抽取器

```python
Agent(
    name="Extractor",
    instructions="抽取联系人信息",
    model="gpt-4o-mini",
    model_settings=ModelSettings(temperature=0),
    output_type=Contact,
)
```

### 套路 D：研究型（带 web_search）

```python
from agents.tools import web_search_tool

Agent(
    name="Researcher",
    instructions="研究问题，引用源",
    model="gpt-4o",
    tools=[web_search_tool()],
)
```

---

## 12. 常见坑

| 坑 | 表现 | 解 |
|----|------|----|
| `instructions` 没说怎么用 tool | 模型不调 tool | 显式 "When asked X, use Y tool" |
| `output_type` 用了 dataclass 不是 Pydantic | 解析错 | 用 `pydantic.BaseModel` |
| 多 tool 时 `parallel_tool_calls=False` 但模型并发 | 报错 | 加上 True 或别让模型同时调 |
| `temperature=0` + structured output | 偶发乱码 | 改 0.1 试 |

---

## 13. 下一步

- 📖 Runner 三种执行模式 → [04-runner.md](./04-runner.md)
- 📖 RunResult 都能拿到啥 → [05-run-result.md](./05-run-result.md)
- 📖 加 Sessions 持久化对话 → [06-sessions.md](./06-sessions.md)
