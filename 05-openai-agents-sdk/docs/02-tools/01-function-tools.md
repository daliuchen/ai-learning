# Function Tools：@function_tool 装饰器

> **一句话**：`@function_tool` 把 Python 函数变成 LLM 可调用的工具，参数 schema 由类型 hints + docstring 自动生成。

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

SDK 自动从：

- 函数名 → tool name
- docstring 第一行 → tool description
- 类型 hints → 参数 schema

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

`limit` 和 `lang` 有默认值，模型可省略。

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

适合参数多、有嵌套结构。

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

SDK 自动识别 sync / async。

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

工具能拿 context（用户身份、连接池等），LLM 看不到 context。

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

工具抛异常 → SDK 自动把错误信息当 tool result 返回给 LLM → LLM 决定怎么处理（道歉 / 换参 / 用别的工具）。

要**让异常彻底 fail**而不是给 LLM 看：

```python
@function_tool(failure_error_function=None)
def critical_op():
    ...
```

详见 [04-tool-choice.md](./04-tool-choice.md)。

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

适合：函数名/文档不适合给 LLM 看，或者多语言文档。

---

## 8. 工具的复杂返回值

LLM 看到的是返回值的 `str()`，复杂结构推荐：

```python
import json
from agents import function_tool


@function_tool
def get_orders(user_id: str) -> str:
    orders = [...]  # 从 DB 查
    return json.dumps(orders, ensure_ascii=False, indent=2)
```

或返回 Pydantic 实例（SDK 自动 dump）：

```python
class Order(BaseModel):
    id: int
    amount: float


@function_tool
def get_order(order_id: int) -> Order:
    return Order(id=order_id, amount=99.0)
```

---

## 9. 不让模型并发

```python
from agents.model_settings import ModelSettings

agent = Agent(
    name="A",
    tools=[get_weather, get_news],
    model_settings=ModelSettings(parallel_tool_calls=False),
)
```

适合工具间有顺序依赖。详见 [04-tool-choice.md](./04-tool-choice.md)。

---

## 10. 强制 / 禁用 tool

```python
ModelSettings(tool_choice="required")  # 必须调一个 tool
ModelSettings(tool_choice="none")      # 别调 tool
ModelSettings(tool_choice="auto")      # 默认，让模型决定
ModelSettings(tool_choice={"type": "function", "name": "get_weather"})  # 强制特定 tool
```

---

## 11. 工具最佳实践

### 命名

- ❌ `do_thing`、`process` 太模糊
- ✅ `search_news`、`create_order`、`fetch_user_profile`

### 描述

- ❌ "用这个工具" - 没用
- ✅ "搜过去 7 天的新闻。query 用关键词，不要写长句。"

### 参数

- 加 docstring `Args:` 让模型知道每个参数怎么用
- 用 `Literal["a", "b"]` 限制枚举值

```python
from typing import Literal

@function_tool
def set_priority(level: Literal["low", "medium", "high"]) -> str:
    return f"set to {level}"
```

### 返回值

- 短：`"OK"` / `"Done"` / `"22°C"`
- 长：JSON dump
- 失败：抛异常或返回 `{"error": "..."}`

---

## 12. 完整 demo

```python
# demos/tools/01_function_tools.py
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

## 13. 下一步

- 📖 Hosted Tools：OpenAI 独门 → [02-hosted-tools.md](./02-hosted-tools.md)
- 📖 把 Agent 当工具用 → [03-agent-as-tool.md](./03-agent-as-tool.md)
- 📖 tool_choice / parallel / 错误 → [04-tool-choice.md](./04-tool-choice.md)
