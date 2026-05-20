# Tool Choice / Parallel / 错误处理

> **一句话**：通过 `model_settings` 控制"调不调 tool"、"能否并发"，通过 `@function_tool` 参数控制"工具错误怎么处理"。

---

## 1. tool_choice：要不要调 tool

```python
from agents import Agent
from agents.model_settings import ModelSettings


# auto（默认）：让模型决定
ModelSettings(tool_choice="auto")

# required：必须调一个 tool
ModelSettings(tool_choice="required")

# none：禁用所有 tool
ModelSettings(tool_choice="none")

# 强制特定 tool
ModelSettings(tool_choice={"type": "function", "name": "get_weather"})
```

### 用例

**required**：必须执行某流程
```python
classifier = Agent(
    name="Classifier",
    instructions="按规则分类，调 classify tool",
    tools=[classify],
    model_settings=ModelSettings(tool_choice="required"),
)
```

**none**：临时关闭工具
```python
# 先调 search agent 收资料，再换个 agent 写报告（不再 search）
writer = Agent(
    name="Writer",
    tools=[search, ...],
    model_settings=ModelSettings(tool_choice="none"),
)
```

---

## 2. parallel_tool_calls：能否并发

```python
ModelSettings(parallel_tool_calls=True)   # 默认，可并发
ModelSettings(parallel_tool_calls=False)  # 强制串行
```

**关掉并发的场景**：

- Tool 之间有顺序依赖（先 `create_order` 再 `add_item`）
- 资源（数据库连接 / API quota）有限

**保持并发的场景**：

- 查 N 个城市的天气
- 同时检索几个不同源

---

## 3. 工具错误：默认行为

```python
@function_tool
def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

模型调 `divide(a=1, b=0)` → SDK 抓到异常 → 把错误信息当 tool result 返给模型 → 模型可能：

- 重试不同参数
- 道歉给用户
- 调别的 tool

---

## 4. 自定义错误处理

```python
def my_error_handler(ctx, error: Exception) -> str:
    return f"出错了: {type(error).__name__} - {str(error)[:100]}"


@function_tool(failure_error_function=my_error_handler)
def risky_op(x: int) -> int:
    return 1 / x
```

`failure_error_function` 接受 `(ctx, error)`，返回字符串当 tool result。

---

## 5. 不要给模型看错误

```python
@function_tool(failure_error_function=None)
def critical_op():
    raise RuntimeError("critical")
```

`failure_error_function=None` → 异常**抛到 Runner.run** 之外 → 由你的代码 catch。

适合：关键操作错了就停，别让模型自己 hallucinate 修复。

---

## 6. tool 内自己 catch

最干净的方式是工具内部消化：

```python
@function_tool
def safe_get_user(user_id: str) -> dict:
    try:
        user = db.get(user_id)
        return {"ok": True, "user": user}
    except UserNotFound:
        return {"ok": False, "error": "user not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

让模型看 `{"ok": false, "error": "..."}`，自己决定怎么响应。

---

## 7. 工具的超时

`@function_tool` 不自带超时。要超时：

```python
import asyncio


@function_tool
async def slow_op(query: str) -> str:
    try:
        return await asyncio.wait_for(_actual_call(query), timeout=10)
    except asyncio.TimeoutError:
        return "查询超时，请稍后再试"
```

或在 Runner.run 外层加 `asyncio.wait_for`。

---

## 8. 重试 tool

不要让 SDK 自动重试 tool——让**模型决定**：

```python
@function_tool
def call_api(query: str) -> str:
    try:
        return _call(query)
    except RateLimitError:
        return "API 限流，请几秒后重试"
```

模型看到 "请几秒后重试" 会自己稍后再调。

要在 tool 内部重试（确定要重试的场景）：

```python
import time

@function_tool
def call_api_with_retry(query: str) -> str:
    for attempt in range(3):
        try:
            return _call(query)
        except RateLimitError:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                raise
    raise RuntimeError("max retries")
```

---

## 9. max_turns 配合

工具一直失败 → 模型一直重试 → 死循环。`max_turns` 防这个：

```python
try:
    result = await Runner.run(agent, "...", max_turns=8)
except MaxTurnsExceeded:
    # 给用户一个 fallback
    pass
```

---

## 10. 完整 demo

```python
# demos/tools/04_tool_choice.py
import asyncio
from agents import Agent, Runner, function_tool
from agents.model_settings import ModelSettings


@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 22°C"


@function_tool
def get_news(topic: str) -> str:
    return f"News about {topic}: ..."


# 1. 强制并发
parallel_agent = Agent(
    name="Parallel",
    instructions="查天气和新闻",
    tools=[get_weather, get_news],
    model_settings=ModelSettings(parallel_tool_calls=True),
)

# 2. 强制串行
serial_agent = Agent(
    name="Serial",
    instructions="先查天气，再查新闻",
    tools=[get_weather, get_news],
    model_settings=ModelSettings(parallel_tool_calls=False),
)

# 3. 必须调 tool
required_agent = Agent(
    name="Required",
    instructions="必须调 get_weather",
    tools=[get_weather],
    model_settings=ModelSettings(tool_choice="required"),
)


async def main():
    r1 = await Runner.run(parallel_agent, "北京天气和 AI 新闻")
    r2 = await Runner.run(serial_agent, "北京天气和 AI 新闻")
    r3 = await Runner.run(required_agent, "随便聊聊")

    for label, r in [("Parallel", r1), ("Serial", r2), ("Required", r3)]:
        print(f"\n[{label}]")
        print(r.final_output[:100])


asyncio.run(main())
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| `tool_choice="required"` 但模型还是不调 | 检查 `tools=[]` 是否传了 |
| `parallel_tool_calls=True` 工具乱序 | 内部加锁或者串行 |
| 工具抛 `pydantic.ValidationError` | 检查参数 docstring 是否清晰 |
| 模型反复调同一 tool | tool result 里说明已调过 + max_turns 兜底 |

---

## 12. 下一步

- 📖 动态工具集 → [05-dynamic-tools.md](./05-dynamic-tools.md)
- 📖 完整守卫体系 → [04-guardrails/01-input-guardrails.md](../04-guardrails/01-input-guardrails.md)
