# Pydantic AI 03：第一个 Agent

> **一句话**：`Agent` 是 Pydantic AI 的核心抽象，把"**模型 + 系统提示 + 工具 + 依赖 + 输出类型**"打包成一个可调用对象，对外暴露同步 / 异步 / 流式三种执行方式。

---

## 1. Agent 的本质

裸调 OpenAI 时你做的事情：

```python
client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
    tools=[...],
)
# 收到回复 → 检查 tool_calls → 执行工具 → 拼回去再请求 → 直到没有 tool_calls
```

这五步循环、参数封装、Schema 生成、重试逻辑，Pydantic AI 把它包装成一个对象：

```python
agent = Agent("openai:gpt-4o", system_prompt="...", output_type=Foo, deps_type=Bar)
result = agent.run_sync("用户消息", deps=bar_instance)
```

可以把 `Agent` 理解为"**带状态的 LLM 调用器**"。它内部维护一个**工具调用循环**（agent loop），每次循环可能：

1. 把当前 messages 发给模型
2. 模型回 text → 走 output_validator → 通过则返回
3. 模型回 tool_call → 执行工具 → tool_result 加入 messages → 回到第 1 步

---

## 2. 构造参数全览

```python
from pydantic_ai import Agent

agent = Agent(
    model="openai:gpt-4o-mini",       # 必填：模型字符串或 Model 对象
    system_prompt="...",              # 静态系统提示
    output_type=str,                  # 输出类型（默认 str）
    deps_type=type(None),             # 依赖类型（默认 None）
    retries=1,                        # 工具/输出校验失败时的重试次数
    output_retries=1,                 # output_validator 的重试上限
    model_settings=None,              # ModelSettings(temperature=..., max_tokens=...)
    instrument=True,                  # 是否接入 Logfire
    end_strategy="early",             # tool_call 结束策略
)
```

最常用的就 4 个：`model` / `system_prompt` / `output_type` / `deps_type`。

### `model`

可以是：

- 字符串：`"openai:gpt-4o-mini"` / `"anthropic:claude-sonnet-4-5"` / `"google-gla:gemini-1.5-pro"`
- Model 对象：`OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="..."))`
- 测试模型：`TestModel()` / `FunctionModel(call=...)`

### `system_prompt`

支持单字符串或元组：

```python
Agent(
    model,
    system_prompt=(
        "你是一位资深 Python 工程师。",
        "回答要简洁，必要时给代码示例。",
    ),
)
# 等价于把这俩字符串拼成 system 消息
```

---

## 3. 三种执行方式

| 方法 | 同步/异步 | 返回 | 用途 |
|------|-----------|------|------|
| `agent.run_sync(prompt)` | 同步 | `AgentRunResult` | 脚本、Jupyter、单测 |
| `await agent.run(prompt)` | 异步 | `AgentRunResult` | FastAPI、并发任务 |
| `async with agent.run_stream(prompt) as r` | 异步流 | `StreamedRunResult` | SSE / WebSocket 推送 |

### 3.1 `run_sync` —— 最常用

```python
result = agent.run_sync("Python GIL 是什么？")
print(result.output)        # 最终回复
print(result.usage())       # token 用量
print(result.all_messages())  # 完整对话历史
```

### 3.2 `run` —— 异步版

```python
import asyncio

async def main():
    result = await agent.run("Python GIL 是什么？")
    print(result.output)

asyncio.run(main())
```

`run_sync` 内部就是用 `asyncio.run(run(...))` 实现的，所以**在 async 环境里别用 `run_sync`**，会报 "already running event loop"。

### 3.3 `run_stream` —— 流式

```python
async def main():
    async with agent.run_stream("讲个长故事") as response:
        async for text in response.stream_text(delta=True):
            print(text, end="", flush=True)
        # 流结束后还能拿最终对象
        final = await response.get_output()
        print("\n\n--- final ---\n", final)
```

注意：

- `stream_text(delta=True)` 拿增量；`delta=False` 拿累积全文
- 流式 + 结构化输出：`response.stream(...)` 返回部分校验过的对象

---

## 4. `AgentRunResult` 详解

`run_sync` 和 `run` 都返回 `AgentRunResult`：

```python
class AgentRunResult:
    output: OutputT                  # 最终输出（按 output_type 校验过）
    def all_messages(self) -> list[ModelMessage]: ...   # 完整历史
    def new_messages(self) -> list[ModelMessage]: ...   # 本次新增
    def usage(self) -> Usage: ...                       # token 用量
    def all_messages_json(self) -> bytes: ...           # JSON 字节
```

**`output` 的类型与 `output_type` 一一对应**：

```python
agent = Agent("openai:gpt-4o", output_type=int)
r = agent.run_sync("2+3=?")
# r.output 是 int，不是 "5"
```

---

## 5. 动态系统提示

静态 `system_prompt` 适合"一成不变"的指令。要根据 deps 或运行时决定，用装饰器：

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

@dataclass
class UserCtx:
    user_id: str
    name: str

agent = Agent("openai:gpt-4o", deps_type=UserCtx, system_prompt="你是一位客服。")

@agent.system_prompt
def add_user_info(ctx: RunContext[UserCtx]) -> str:
    return f"当前用户：{ctx.deps.name}（ID={ctx.deps.user_id}）"

@agent.system_prompt
async def add_time(ctx: RunContext[UserCtx]) -> str:
    import datetime
    return f"当前时间：{datetime.datetime.now().isoformat()}"

result = agent.run_sync("我是谁？", deps=UserCtx("u1", "Ethan"))
```

执行顺序：

1. 先拼静态 `system_prompt`
2. 再依次执行每个 `@agent.system_prompt` 装饰的函数（按声明顺序），把返回值追加到 system 部分
3. 拼成最终 system 消息发给模型

⚠️ 动态系统提示每次 `run` 都会执行一次，**别在里面做重活**。

---

## 6. 实战 1：天气助手

```python
from dotenv import load_dotenv
from pydantic_ai import Agent

load_dotenv()

agent = Agent(
    "openai:gpt-4o-mini",
    system_prompt="你是一位天气助手，可以调用 get_weather 工具。",
)

@agent.tool_plain
def get_weather(city: str) -> str:
    """查询城市天气"""
    db = {"北京": "晴 26°C", "上海": "多云 24°C", "杭州": "雨 19°C"}
    return db.get(city, "未知")

result = agent.run_sync("北京和杭州哪里更凉快？")
print(result.output)
# > 杭州（19°C）比北京（26°C）更凉快，并且有雨。
```

注意：

- 模型自动决定调几次 `get_weather`
- `result.all_messages()` 会看到完整的 user → model_tool_call → tool_return → model_text 链
- 默认 `retries=1`，工具抛异常会自动重试一次

---

## 7. 实战 2：结构化计算器

```python
from pydantic import BaseModel
from pydantic_ai import Agent

class CalcResult(BaseModel):
    expression: str
    answer: float
    explanation: str

agent = Agent(
    "openai:gpt-4o-mini",
    output_type=CalcResult,
    system_prompt="解析数学表达式并返回结构化结果",
)

r = agent.run_sync("帮我算 (12 + 8) * 3 - 5")
print(r.output)
# CalcResult(expression='(12 + 8) * 3 - 5', answer=55.0, explanation='...')
```

**这里没写任何 JSON parsing 代码**，Pydantic AI 自动：

- 把 `CalcResult` 的 schema 喂给模型（function calling 形式）
- 拿到结果后用 `CalcResult.model_validate(...)` 校验
- 校验失败时让模型 retry

---

## 8. 测试：`TestModel` 与 `FunctionModel`

不想花 API 钱、不想等网络？用 `TestModel`：

```python
from pydantic_ai.models.test import TestModel

agent = Agent(TestModel(), output_type=CalcResult)
r = agent.run_sync("随便问")
# TestModel 会自动生成符合 schema 的"假"对象
```

要模拟"模型先调工具再回答"的序列，用 `FunctionModel`：

```python
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

def fake_call(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content="假装的回答")])

agent = Agent(FunctionModel(fake_call))
print(agent.run_sync("hi").output)  # 假装的回答
```

这两个模型是测试基石，**完全不联网**，第 14 篇会专门讲。

---

## 9. 全局默认模型

如果项目里所有 Agent 都用同一个模型，可以设默认：

```python
from pydantic_ai.models import infer_model
import pydantic_ai

# 方式 A：每个 Agent 都指定字符串
agent = Agent("openai:gpt-4o-mini")

# 方式 B：抽到工厂函数
def make_agent(**kwargs):
    return Agent("openai:gpt-4o-mini", **kwargs)

agent = make_agent(output_type=Invoice)
```

Pydantic AI **暂时没有**全局 `set_default_model` API，所以推荐"方式 B"。

---

## 10. vs LangChain

| 任务 | LangChain | Pydantic AI |
|------|-----------|-------------|
| 系统提示 | `ChatPromptTemplate.from_messages([("system", "..."), ...])` | `Agent(..., system_prompt="...")` |
| 工具 | `@tool` + `bind_tools([...])` + `AgentExecutor` | `@agent.tool_plain` 直接装饰 |
| 结构化输出 | `model.with_structured_output(Schema)` | `Agent(..., output_type=Schema)` |
| 工具调用循环 | `AgentExecutor(agent, tools).invoke(...)` | `agent.run_sync(...)` 内置 |
| 流式 | `chain.stream(...)` | `agent.run_stream(...)` |
| 异步 | `chain.ainvoke(...)` | `agent.run(...)` |

LangChain 的等价"Hello Agent"：

```python
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

@tool
def get_weather(city: str) -> str:
    """查询城市天气"""
    return {"北京": "晴 26°C"}.get(city, "未知")

agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), tools=[get_weather])
result = agent.invoke({"messages": [("user", "北京天气")]})
```

差别：

- LangChain 要先 `tool` 再传给 `create_react_agent`
- Pydantic AI 直接 `@agent.tool_plain` 装饰，工具与 agent 绑定
- 输入格式：LangChain 要 `{"messages": [...]}`，Pydantic AI 直接 `"...prompt..."`

---

## 11. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| `RuntimeError: asyncio loop is already running`（在 Jupyter 用 run_sync） | Jupyter 有自己的 loop | 用 `await agent.run(...)` |
| `output_type=list[Item]` 报错 | 旧版本不支持顶层 list | 包一层：`output_type=ItemList`（Pydantic Model 内含 `items: list[Item]`） |
| `system_prompt` 设了但模型不听 | 模型温度过高 / prompt 太短 | 加 `model_settings=ModelSettings(temperature=0)` |
| 两个 `@agent.system_prompt` 都加但顺序不对 | 装饰器按代码声明顺序追加 | 调整代码顺序 |
| `result.output` 是 `None` | 工具循环还没结束就 break（end_strategy 设错） | 用默认 `end_strategy="early"` |
| 工具调用一次后死循环 | tool 永远抛异常 → retry 用光 → 再调 | 工具内自己捕获并返回 friendly 字符串 |
| 同一个 agent 全局共享导致 deps 串台 | agent 本身无状态，但 `@agent.tool` 闭包了变量 | 每个请求 `agent.run(deps=...)`，工具用 `ctx.deps` 拿 |

---

## 12. 本章 demo

完整可运行代码：[`demos/basics/03_first_agent.py`](../../demos/basics/03_first_agent.py)

跑通后下一章：[04-models-providers.md](04-models-providers.md) —— 各家模型 Provider 对比。
