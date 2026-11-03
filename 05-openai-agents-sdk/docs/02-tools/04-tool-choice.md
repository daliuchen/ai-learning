# Tool Choice / Parallel / 超时 / 错误处理 / 人工审批

> 工具挂上之后，还有几件事要定：模型要不要调工具、能不能并发调（`model_settings` 管），工具出错或超时怎么办（`@function_tool` 的参数管），以及退款、删数据这类敏感操作怎么先等人确认（`needs_approval` + `RunState`）。这篇逐一讲。

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

# 强制特定 tool：直接写工具名字符串
ModelSettings(tool_choice="get_weather")
```

官方文档里还有一条限制：用 OpenAI Responses 的 tool search 时，具名的 `tool_choice` 不能指向命名空间名或仅延迟加载的工具，只能用 `auto`、`required` 或实际存在的顶层工具名。

### 用例

`required`：流程要求必须调用工具时
```python
classifier = Agent(
    name="Classifier",
    instructions="按规则分类，调 classify tool",
    tools=[classify],
    model_settings=ModelSettings(tool_choice="required"),
)
```

`none`：临时关闭工具
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

工具之间有先后依赖（必须先 `create_order` 再 `add_item`），或者数据库连接、API 配额这类资源有限时，应该关掉并发。查 N 个城市的天气、同时检索几个不同来源这种互不相干的调用，保持并发更快。

---

## 3. 工具错误：默认行为

```python
@function_tool
def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

模型调用 `divide(a=1, b=0)` 时，SDK 捕获异常，给模型返回一条 tool result。模型可能换参数重试、向用户道歉，或者改调别的工具。

默认的 tool result 只是一句笼统的 `An error occurred while running the tool. Please try again.`，里面不包含异常原文。想让模型知道具体原因，用下一节的 `failure_error_function`。

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

设成 `failure_error_function=None` 后，异常会抛出 `Runner.run`，由你自己的代码捕获。抛出来的是包了一层的 `UserError: Error running tool critical_op: ...`；模型给出非法 JSON 参数时则是 `ModelBehaviorError`。

关键操作出错时就该停下来，不要让模型自己编一个修复方案，这时用这种方式。

---

## 6. tool 内自己 catch

更干净的做法是在工具内部把异常处理掉：

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

模型看到 `{"ok": false, "error": "..."}`，自己决定怎么回应。

---

## 7. 工具的超时

`@function_tool` 从 0.9.0 起自带超时参数：

```python
@function_tool(timeout=2.0)                         # 仅 async 工具
async def slow_op(query: str) -> str:
    await asyncio.sleep(10)
    return "x"
```

- 默认 `timeout_behavior="error_as_result"`：给模型一条 `Tool 'slow_op' timed out after 2 seconds.`，由模型决定重试还是道歉（比如回一句“查询超时，请稍后重试”）；
- `timeout_behavior="raise_exception"`：抛 `ToolTimeoutError`（带 `tool_name`、`timeout_seconds`），整个 run 失败；
- `timeout_error_function=...`：自定义给模型的超时文案。

这里说的是工具超时，另外还有两层：模型调用的超时是 `ModelSettings(timeout=秒)`（抛 `ModelTimeoutError`，可以交给重试策略处理）；整个 run 的超时仍然要在外层用 `asyncio.wait_for`。

---

## 8. 重试 tool

我倾向于不让 SDK 自动重试工具，而是交给模型决定：

```python
@function_tool
def call_api(query: str) -> str:
    try:
        return _call(query)
    except RateLimitError:
        return "API 限流，请几秒后重试"
```

模型看到 "请几秒后重试"，会过一会儿再调。

如果确定要重试，就在工具内部做：

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

工具一直失败、模型一直重试，就会陷入死循环。`max_turns` 用来兜住这种情况：

```python
try:
    result = await Runner.run(agent, "...", max_turns=8)
except MaxTurnsExceeded:
    # 给用户一个 fallback
    pass
```

---

## 10. 人工审批：`needs_approval` + `RunState`

退款、删数据、发邮件这类工具，不应该让模型想调就调。SDK 从 0.8.0 起有原生的人工审批：

```python
from agents import Agent, Runner, RunState
from agents.decorators import tool


@tool(needs_approval=True)                           # True=每次都要批；也可传 async (ctx, params, call_id) -> bool 按参数决定
async def cancel_order(order_id: str) -> str:
    """取消订单"""
    return f"已取消订单 {order_id}"


agent = Agent(name="Support", instructions="用户要取消订单就调用 cancel_order", tools=[cancel_order])

result = await Runner.run(agent, "帮我取消订单 A100")
print(result.final_output)       # None：run 暂停了
for it in result.interruptions:  # 待审批列表
    print(it.name, it.arguments) # cancel_order {"order_id": "A100"}

state = result.to_state()                    # 可序列化的快照
saved = state.to_string()                    # 可以存库，换个进程再恢复
state = await RunState.from_string(agent, saved)

for it in state.get_interruptions():
    state.approve(it)                        # 或 state.reject(it, rejection_message="该订单已发货")
result = await Runner.run(agent, state)      # 传入原来的顶层 agent
print(result.final_output)                   # 订单 A100 已成功取消……
```

批准后工具正常执行；拒绝时模型会收到 `rejection_message`，并据此告诉用户“该订单已发货，无法取消”。

几个细节：

- `needs_approval` 可用在 `function_tool`、`Agent.as_tool`、`ShellTool`、`ApplyPatchTool`；本地 MCP 服务用 `require_approval`，hosted MCP 用 `tool_config={"require_approval": "always"}` + `on_approval_request`。
- 审批在整个 run 范围内生效：handoff 之后的 agent、嵌套的 `as_tool` 子 Agent 里的工具要审批，都会冒泡到最外层 `result.interruptions`。
- 流式 run 同样：先把 `stream_events()` 迭代完，再看 `interruptions`，用 `Runner.run_streamed(agent, state)` 继续流。
- 带 session 的话，恢复时传同一个 session：`Runner.run(agent, state, session=session)`。
- `state.approve(it, always_approve=True)` 会让之后同一工具的调用不再询问（这个决定存在 `RunContextWrapper` / `RunState` 里；本地 shell / apply_patch 的永久批准按工具类型加名字匹配，不区分 agent，所以各 agent 权限不同时只能逐次批准）。
- 全局统一改拒绝文案：`RunConfig(tool_error_formatter=...)`；单次拒绝用 `rejection_message=`。
- 审批只是让人在执行前看一眼，不限制工具本身能做什么，它替代不了沙箱。
- 之后还可以再 `state.add_input("补充一句...")`（0.20 起）在恢复前追加用户输入。

---

## 11. 完整示例

```python
import asyncio
from agents import Agent, Runner, function_tool
from agents.model_settings import ModelSettings


@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 22°C"


@function_tool
def get_news(topic: str) -> str:
    return f"News about {topic}: ..."


# 1. 允许并发（模型可以一次发多个 tool call，发不发由模型决定）
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

## 12. 常见坑

| 坑 | 解 |
|----|----|
| `tool_choice="required"` 但模型还是不调 | 检查 `tools=[]` 是否传了 |
| `parallel_tool_calls=True` 工具乱序 | 内部加锁或者串行 |
| 工具抛 `pydantic.ValidationError` | 检查参数 docstring 是否清晰 |
| 模型调了不存在的工具，整个 run 挂了 | `RunConfig(tool_not_found_behavior="return_error_to_model")` 让模型自己改正 |
| 一轮里模型发出十几个 tool call，本地同时执行把连接池占满 | `RunConfig(tool_execution=ToolExecutionConfig(max_function_tool_concurrency=3))` |
| 模型反复调同一 tool | tool result 里说明已调过 + max_turns 兜底 |

---

## 13. 下一步

- [动态工具集与上下文感知工具](./05-dynamic-tools.md)
- [Input Guardrails：输入守卫](../04-guardrails/01-input-guardrails.md)
