# Function Tools：@function_tool 装饰器

> `@function_tool` 把普通 Python 函数变成 LLM 能调用的工具，参数 schema 从类型注解和 docstring 自动生成。0.19 起还有一个同义的短别名 `@tool`（在 `agents.decorators` 里），下文两种写法可以互换。

---

## 1. 最简示例

```python
from agents import Agent, Runner, function_tool


@function_tool
def get_weather(city: str) -> str:
    """查询城市天气

    Args:
        city: 城市中文名
    """
    return f"{city}: 22°C, 晴"


agent = Agent(
    name="WeatherBot",
    instructions="用户问天气时调用 get_weather",
    tools=[get_weather],
)
```

SDK 会自动做这几件事：

- 函数名作为 tool name
- docstring 开头的说明段（`Args:` 之前的文字）作为 tool description
- 类型注解生成参数 schema（由 `pydantic` 完成）
- docstring 的 `Args:` 段作为每个参数的 description（用 `griffe` 解析，支持 google / numpy / sphinx 风格）

`@function_tool` 得到的是 `FunctionTool` 实例，可以直接 `print(tool.params_json_schema)` 看模型实际收到的 schema，我习惯写完工具先打出来看一眼。`tool.__wrapped__` 能拿到原函数，写单测方便，但这样会绕过 schema 校验、context 注入、guardrails、超时和 tracing。

---

## 2. 多参数 + 默认值

```python
@function_tool
def search_news(query: str, limit: int = 5, lang: str = "zh") -> list[dict]:
    """搜新闻

    Args:
        query: 关键词
        limit: 返回数
        lang: 语言（zh / en）
    """
    return [{"title": f"news{i}", "url": "..."} for i in range(limit)]
```

`limit` 和 `lang` 有默认值，模型调用时可以不传。要给参数加约束或描述，可以用 `Annotated` + pydantic `Field`，生成的 schema 里会带上 `enum`、`minimum`/`maximum`：

```python
from typing import Annotated, Literal
from pydantic import Field

@function_tool
def pick(
    level: Annotated[Literal["low", "high"], Field(description="优先级")],
    n: Annotated[int, Field(ge=1, le=5, description="数量")] = 1,
) -> str:
    """选择"""
    return f"{level}x{n}"
```

---

## 3. Pydantic 模型作参数

```python
from pydantic import BaseModel
from agents import function_tool


class SearchParams(BaseModel):
    query: str
    limit: int = 5
    lang: str = "zh"


@function_tool
def search_news(params: SearchParams) -> list[dict]:
    """搜新闻"""
    return [...]
```

参数多、有嵌套结构时用这种写法。

---

## 4. async tool

```python
import httpx
from agents import function_tool


@function_tool
async def fetch_url(url: str) -> str:
    """读 URL 的文本"""
    async with httpx.AsyncClient() as c:
        r = await c.get(url, timeout=30)
        return r.text[:5000]
```

SDK 会自动识别同步和异步函数。0.8 起，同步函数工具会放到工作线程里执行（`asyncio.to_thread`），所以依赖线程局部变量或者必须在特定线程上使用的资源，要改写成 async 工具。

---

## 5. 拿 context

```python
from agents import RunContextWrapper, function_tool


class MyContext(BaseModel):
    user_id: str
    db: object


@function_tool
async def query_user_orders(ctx: RunContextWrapper[MyContext]) -> list[dict]:
    """查当前用户的订单"""
    user_id = ctx.context.user_id
    # return await ctx.context.db.fetch_orders(user_id)
    return [{"id": 1, "amount": 99}]
```

跑：

```python
my_ctx = MyContext(user_id="42", db=...)
await Runner.run(agent, "查我的订单", context=my_ctx)
```

工具可以从 context 里拿用户身份、连接池这类东西，而这些内容 LLM 看不到。

---

## 6. 错误处理

```python
@function_tool
def divide(a: float, b: float) -> float:
    """除法"""
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

工具抛异常时，SDK 会捕获它，转成一条 tool result 交给 LLM，由 LLM 决定是道歉、换参数重试还是改用别的工具。

不过默认交给 LLM 的只是一句笼统的话：`An error occurred while running the tool. Please try again.`。比如工具里抛 `ValueError("除数不能为 0")`，“除数不能为 0”这句原因不会传给模型。想让模型看到原因，就自己写 `failure_error_function`：

```python
def explain(ctx, error: Exception) -> str:
    return f"工具失败：{error}"

@function_tool(failure_error_function=explain)
def divide(a: float, b: float) -> float: ...
```

如果希望异常直接让这次 run 失败，而不是交给 LLM：

```python
@function_tool(failure_error_function=None)
def critical_op():
    ...
```

这时异常会从 `Runner.run` 抛出来，外面包了一层 `UserError("Error running tool critical_op: ...")`（如果是模型传了非法 JSON，则是 `ModelBehaviorError`）。

更多见 [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](./04-tool-choice.md)。

---

## 7. 自定义 description

```python
@function_tool(
    name_override="weather",
    description_override="查询天气信息，输入城市名",
)
def get_weather_data_v2(city: str) -> str:
    return "..."
```

函数名或 docstring 不适合直接给 LLM 看，或者需要多语言描述时，用这种方式覆盖。

---

## 8. 工具的复杂返回值

LLM 看到的是返回值的字符串形式。结构复杂时可以返回 JSON：

```python
import json
from agents import function_tool


@function_tool
def get_orders(user_id: str) -> str:
    orders = [...]  # 从 DB 查
    return json.dumps(orders, ensure_ascii=False, indent=2)
```

也可以返回 Pydantic 实例，SDK 会自动序列化，比如让 `get_order` 返回 `Order`，模型能正确读出金额：

```python
class Order(BaseModel):
    id: int
    amount: float


@function_tool
def get_order(order_id: int) -> Order:
    return Order(id=order_id, amount=99.0)
```

---

要返回图片或文件，直接返回 `ToolOutputImage(image_url=...)`、`ToolOutputFileContent(...)`、`ToolOutputText(...)`（或它们的 TypedDict 版本，也可以返回列表），模型会把它们当多模态内容读。注意 Chat Completions 端点只认纯文本输出：默认只提取其中的文本部分，图片、文件内容会被丢掉。

---

## 9. 超时、按条件启用、延迟加载

```python
import asyncio
from agents import RunContextWrapper, function_tool

@function_tool(timeout=2.0)                       # 仅 async 工具支持
async def slow_lookup(q: str) -> str:
    await asyncio.sleep(10)
    return "x"
# 超时默认给模型一条 "Tool 'slow_lookup' timed out after 2 seconds."（timeout_behavior="error_as_result"）
# 想直接让 run 失败：timeout_behavior="raise_exception" → 抛 ToolTimeoutError
# 自定义提示：timeout_error_function=...

def only_admin(ctx: RunContextWrapper[dict], agent) -> bool:      # 每次 run 前评估，False 则对模型隐藏
    return ctx.context.get("admin", False)

@function_tool(is_enabled=only_admin)
def admin_op() -> str: ...
```

`is_enabled` 返回 False 时，`await agent.get_all_tools(ctx)` 里就没有这个工具。但它只控制模型能不能看到这个工具，算不上权限控制。和参数、资源相关的权限检查仍然要写在工具里，或者交给 tool guardrails / 审批。

工具特别多（超过 20 个）时，OpenAI Responses 模型支持延迟加载：`@function_tool(defer_loading=True)` + `tool_namespace(name=..., description=..., tools=[...])`，再在 agent 里加一个（只能一个）`ToolSearchTool()`，模型会按需把要用的那组工具搜出来，省下 schema 占用的 token。这只在 Responses 模型上可用，第三方端点不行。详见 [动态工具集与上下文感知工具](./05-dynamic-tools.md)。

---

## 10. 不让模型并发

```python
from agents.model_settings import ModelSettings

agent = Agent(
    name="A",
    tools=[get_weather, get_news],
    model_settings=ModelSettings(parallel_tool_calls=False),
)
```

工具之间有先后依赖时这样设。详见 [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](./04-tool-choice.md)。

---

## 11. 强制 / 禁用 tool

```python
ModelSettings(tool_choice="required")  # 必须调一个 tool
ModelSettings(tool_choice="none")      # 别调 tool
ModelSettings(tool_choice="auto")      # 默认，让模型决定
ModelSettings(tool_choice="get_weather")   # 强制特定 tool：直接写工具名字符串
```

---

## 12. 写工具的几条经验

### 命名

`do_thing`、`process` 这种名字太模糊，模型猜不出什么时候该用。`search_news`、`create_order`、`fetch_user_profile` 这样动词加对象的名字就清楚得多。

### 描述

"用这个工具" 这种描述等于没写。好的描述会说清楚范围和用法，比如 "搜过去 7 天的新闻。query 用关键词，不要写长句。"

### 参数

在 docstring 的 `Args:` 里写清每个参数怎么用；取值有限的参数用 `Literal["a", "b"]` 限定。

```python
from typing import Literal

@function_tool
def set_priority(level: Literal["low", "medium", "high"]) -> str:
    return f"set to {level}"
```

### 返回值

简单结果直接返回短字符串，如 `"OK"`、`"Done"`、`"22°C"`；结构化的结果用 JSON；失败时抛异常，或者返回 `{"error": "..."}`。

---

## 13. 完整示例

```python
import asyncio
from typing import Literal
from pydantic import BaseModel
from agents import Agent, Runner, function_tool


@function_tool
def get_weather(city: str, unit: Literal["C", "F"] = "C") -> str:
    """查城市天气

    Args:
        city: 城市中文名
        unit: 温度单位，C 摄氏 / F 华氏
    """
    return f"{city}: 22°{unit}, 晴"


class NewsQuery(BaseModel):
    query: str
    limit: int = 3


@function_tool
def search_news(params: NewsQuery) -> str:
    """搜新闻"""
    items = [
        {"title": f"{params.query} 相关新闻 {i}", "url": f"https://example.com/{i}"}
        for i in range(params.limit)
    ]
    import json
    return json.dumps(items, ensure_ascii=False)


agent = Agent(
    name="InfoBot",
    instructions="按需调用工具回答。",
    tools=[get_weather, search_news],
)


async def main():
    result = await Runner.run(agent, "查北京天气，再搜 AI 相关新闻")
    print(result.final_output)


asyncio.run(main())
```

---

## 14. 下一步

- [Hosted Tools：OpenAI 托管的内置工具](./02-hosted-tools.md)
- [Agent as Tool：把 Agent 当作工具调用](./03-agent-as-tool.md)
- [Tool Choice / Parallel / 超时 / 错误处理 / 人工审批](./04-tool-choice.md)
