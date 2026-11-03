# Pydantic AI 02-01：Function Tools 函数工具

> 在 Pydantic AI 里，一个普通的 Python 函数加上 `@agent.tool` 就成了工具。框架按类型注解和 docstring 生成给模型看的 schema，JSON Schema 基本不用手写。这篇从装饰器讲到出错处理和重试预算。

以 `pydantic-ai 2.64.0` 为准。示例模型名 `openai:gpt-6.1-sol` 只是占位，换成任意支持工具调用的模型都行。

---

## 1. 为什么要工具

LLM 能聊天、能推理，但它自己没法去查数据库、查日历、调天气 API。工具就是给它的这部分能力：模型决定调用哪个函数、传什么参数，你的代码负责执行，结果再交回模型。

```
LLM  ─请求调用工具─▶  你的 Python 函数  ─返回结果─▶  LLM  ─继续推理─▶  最终回答
```

裸调 OpenAI SDK 写一个工具大概长这样：

```python
# ❌ 裸 SDK：手写 JSON Schema + 自己解析参数
tools = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get weather for a city",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name"},
                "unit": {"type": "string", "enum": ["c", "f"]},
            },
            "required": ["city"],
        },
    },
}]
# 还要自己 loop 解析 tool_calls、调函数、把结果塞回 messages...
```

Pydantic AI 里同一件事：

```python
# ✅ Pydantic AI：装饰一下就完事
@agent.tool_plain
def get_weather(city: str, unit: str = 'c') -> str:
    """Get weather for a city.

    Args:
        city: City name
        unit: Temperature unit, c or f
    """
    return f'{city} is 21{unit}'
```

schema 从签名和 docstring 自动生成，调用循环也由框架负责。

---

## 2. 两种装饰器：`tool` vs `tool_plain`

| 装饰器 | 用途 | 函数第一个参数 |
|--------|------|----------------|
| `@agent.tool` | 工具需要拿到 Agent 的 deps / usage / 历史消息 | `ctx: RunContext[DepsType]` |
| `@agent.tool_plain` | 工具是纯函数，不需要任何上下文 | 没有特殊参数 |

```python
from pydantic_ai import Agent, RunContext

agent = Agent('openai:gpt-6.1-sol', deps_type=str)

@agent.tool_plain                    # ← 不需要 ctx
def roll_dice() -> str:
    """Roll a six-sided die and return the result."""
    import random
    return str(random.randint(1, 6))

@agent.tool                          # ← 需要 ctx 拿 deps
def get_player_name(ctx: RunContext[str]) -> str:
    """Get the player's name."""
    return ctx.deps                  # ← deps 由 agent.run_sync(deps=...) 注入
```

好记的方式：`tool` 是默认情况，因为多数工具都要用到依赖；带 `_plain` 的是不需要上下文的特例。

---

## 3. Schema 是怎么生成的

Pydantic AI 用 [griffe](https://mkdocstrings.github.io/griffe/) 解析 docstring，拆成两部分：

- 第一段（标题段）作为工具的 `description`
- `Args:` 段里的每一项作为对应参数的 `description`

```python
@agent.tool_plain(docstring_format='google', require_parameter_descriptions=True)
def foobar(a: int, b: str, c: dict[str, list[float]]) -> str:
    """Get me foobar.

    Args:
        a: apple pie
        b: banana cake
        c: carrot smoothie
    """
    return f'{a} {b} {c}'
```

模型收到的 schema 长这样：

```python
{
    'description': 'Get me foobar.',
    'parameters': {
        'type': 'object',
        'additionalProperties': False,
        'properties': {
            'a': {'description': 'apple pie', 'type': 'integer'},
            'b': {'description': 'banana cake', 'type': 'string'},
            'c': {
                'additionalProperties': {'items': {'type': 'number'}, 'type': 'array'},
                'description': 'carrot smoothie',
                'type': 'object',
            },
        },
        'required': ['a', 'b', 'c'],
    },
}
```

支持的 docstring 风格：

| 风格 | 示例 |
|------|------|
| `google` | `Args:` / `Returns:` / `Raises:` 段落 |
| `numpy` | `Parameters\n----------` 分割 |
| `sphinx` | `:param a:` / `:returns:` 标签 |

不指定时框架会自己猜，但显式写上 `docstring_format='google'` 更稳。

`require_parameter_descriptions=True` 要求每个参数都有描述，缺一个就抛 `UserError`。我建议生产环境打开它。

---

## 4. 最小可跑例子

```python
import random
from pydantic_ai import Agent, RunContext

agent = Agent(
    'openai:gpt-6.1-sol',
    deps_type=str,
    instructions=(
        "You're a dice game, roll the die and see if the number "
        "matches the user's guess. Use the player's name in the response."
    ),
)

@agent.tool_plain
def roll_dice() -> str:
    """Roll a six-sided die and return the result."""
    return str(random.randint(1, 6))

@agent.tool
def get_player_name(ctx: RunContext[str]) -> str:
    """Get the player's name."""
    return ctx.deps

result = agent.run_sync('My guess is 4', deps='Anne')
print(result.output)
# Congratulations Anne, you guessed correctly! You're a winner!
```

模型会自己决定先调 `roll_dice`，再调 `get_player_name`，最后组织回复。

---

## 5. 不用装饰器：直接构造 `Tool`

同一个函数要给多个 Agent 用，或者想改工具的名字、描述时，可以直接构造 `Tool`：

```python
from pydantic_ai import Agent, Tool, RunContext

def roll_dice() -> str:
    """Roll a six-sided die."""
    return str(random.randint(1, 6))

def get_player_name(ctx: RunContext[str]) -> str:
    """Get the player's name."""
    return ctx.deps

agent = Agent(
    'openai:gpt-6.1-sol',
    deps_type=str,
    tools=[
        Tool(roll_dice, takes_ctx=False),         # 明确告诉框架是 plain
        Tool(get_player_name, takes_ctx=True),    # 这个吃 ctx
    ],
)
```

`takes_ctx=None`（默认）时框架会自动判断，但显式写出来更不容易出错。

改名字、改描述：

```python
Tool(roll_dice, name='throw_die', description='Throw one six-sided dice', takes_ctx=False)
```

函数没有 docstring 时，也可以直接给一份 JSON Schema：

```python
def foobar(**kwargs) -> str:
    return f"{kwargs['a']} + {kwargs['b']}"

tool = Tool.from_schema(
    function=foobar,
    name='sum',
    description='Sum two numbers.',
    json_schema={
        'type': 'object',
        'properties': {
            'a': {'type': 'integer', 'description': 'first'},
            'b': {'type': 'integer', 'description': 'second'},
        },
        'required': ['a', 'b'],
    },
    takes_ctx=False,
)
```

注意 `Tool.from_schema` 不做参数校验，参数原样以 `**kwargs` 传进函数。

---

## 6. 返回值类型

工具可以返回任何能被 Pydantic 序列化成 JSON 的对象：

| 返回类型 | 说明 |
|----------|------|
| `str` / `int` / `float` / `bool` | 最常用 |
| `dict` / `list` | 结构化数据 |
| `pydantic.BaseModel` | 自动 dump 成 JSON |
| `dataclasses.dataclass` / `TypedDict` | 同上 |
| `ImageUrl` / `BinaryContent` / `DocumentUrl` | 多模态（详见[Advanced Tool Features 高级工具特性](02-advanced-tools.md)） |
| `ToolReturn(...)` | 返回值、富内容、内部 metadata 分开交付（详见[Advanced Tool Features 高级工具特性](02-advanced-tools.md)） |

返回 Pydantic 对象的例子：

```python
from pydantic import BaseModel

class Weather(BaseModel):
    city: str
    temperature: float
    condition: str

@agent.tool_plain
def get_weather(city: str) -> Weather:
    """Get weather info for a city."""
    return Weather(city=city, temperature=21.0, condition='sunny')
```

模型收到的是 JSON 字符串，你这边写的是强类型对象，IDE 补全和单测都方便。

---

## 7. 同步 vs 异步工具

直接写 `async def`，框架会自己识别：

```python
import httpx

@agent.tool_plain
async def fetch_url(url: str) -> str:
    """Fetch URL and return body."""
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        return r.text[:500]
```

我的做法：

- IO 型（HTTP、数据库、文件）写 `async def`，多个工具调用默认并行执行
- CPU 密集的，在 `async` 里用 `asyncio.to_thread(...)` 包一层，别堵住事件循环
- 简单的同步计算直接写 `def`，框架会放到线程池里跑

---

## 8. 工具出错：三种结局

工具给不出正常结果时，按你希望模型下一步怎么做来选异常：

| 抛什么 | 模型看到什么 | 消耗重试预算吗 |
|--------|-------------|---------------|
| `ModelRetry('...')` | 一条 `outcome='retried'` 的工具结果：让它改参数或换个做法再试 | 是 |
| `ToolFailed('...')` | 一条 `outcome='failed'` 的工具结果：这次调用已经完成，结果是失败 | 否 |
| 其它任何异常 | 看不到，异常直接抛出并中断整个运行 | — |

```python
from pathlib import Path
from pydantic_ai import ModelRetry, ToolFailed

@agent.tool_plain
def lookup_user(user_id: str) -> dict:
    """Lookup a user by ID."""
    if not user_id.startswith('U-'):
        # 参数写错了，让模型改了再来
        raise ModelRetry(f"Invalid user_id format: {user_id!r}. Must start with 'U-'. Please retry.")
    return {'id': user_id, 'name': 'Alice'}

@agent.tool_plain
def read_file(path: str) -> str:
    """Read a text file."""
    if not Path(path).is_file():
        # 文件不存在：不是参数写错，重试也没用，但模型需要知道结果是失败
        raise ToolFailed(f'File not found: {path}')
    return Path(path).read_text(encoding='utf-8')
```

几个细节：

- 被要求重试时，消息历史里记的是这次工具调用自己的 `ToolReturnPart(outcome='retried')`，V2 起不再是 `RetryPromptPart`；
- `ToolFailed` 不占重试预算，模型反复调用一个总失败的工具，要靠 `UsageLimits(request_limit=...)` 拦住；
- 参数不符合 schema（Pydantic 校验失败）时，自动按 `ModelRetry` 处理；
- 工具的校验 / 执行 hook 里也能抛这两个异常，适合把第三方库的异常统一转换一遍，不用每个工具都写 `try/except`；
- 工具里可以读 `ctx.retry` / `ctx.max_retries`，知道自己已经重试了几次、上限是多少。

重试次数可以在三个层级设置，越细的越优先：

```python
@agent.tool(retries=3)                    # 1. 单工具级
def my_tool(...): ...

toolset = FunctionToolset(max_retries=5)  # 2. toolset 级

agent = Agent(..., retries={'tools': 2})  # 3. agent 级（默认 1）
```

重试预算按工具分别计数，没有全局共享的工具调用预算。超过次数会抛 `UnexpectedModelBehavior('Tool ... exceeded max retries count of N')`。

> 用 `TestModel` 离线跑时要注意：它给字符串参数传的是占位值 `'a'`，工具里「必须是某种格式」的校验会一直失败，直到预算耗尽。给 `TestModel` 配工具时，校验要能接受 `'a'`，或者改用 `FunctionModel` 写死调用脚本（见[用 TestModel、FunctionModel 与 override 测试 Agent](../05-patterns/03-testing.md)）。

---

## 9. 实战：双工具 Agent（日历 + 天气）

```python
from datetime import date
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, ModelRetry

class UserDeps(BaseModel):
    user_id: str
    timezone: str

agent = Agent(
    'openai:gpt-6.1-sol',
    deps_type=UserDeps,
    instructions="You're a friendly scheduling assistant.",
)

@agent.tool
def list_events(ctx: RunContext[UserDeps], day: date) -> list[dict]:
    """List calendar events for a given day.

    Args:
        day: Date to query in ISO format YYYY-MM-DD.
    """
    # 真实场景这里查 DB
    fake_db = {
        date(2026, 5, 20): [
            {'time': '10:00', 'title': 'Standup', 'user': ctx.deps.user_id},
            {'time': '15:00', 'title': '1:1 with manager', 'user': ctx.deps.user_id},
        ],
    }
    return fake_db.get(day, [])

@agent.tool_plain
def get_weather(city: str) -> dict:
    """Get current weather of a city.

    Args:
        city: City English name.
    """
    if not city or len(city) < 2:
        raise ModelRetry(f'Invalid city {city!r}. Provide a real city name.')
    return {'city': city, 'temperature': 21, 'condition': 'sunny'}

result = agent.run_sync(
    "What's on my calendar for May 20, 2026, and how's the weather in Shanghai?",
    deps=UserDeps(user_id='U-001', timezone='Asia/Shanghai'),
)
print(result.output)
```

模型会把问题拆成两步，先查日历再查天气，然后合成一段回答。

---

## 10. 和 LangChain 工具对比

LangChain：

```python
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """Get weather for city."""
    return f'{city} is sunny'

# 然后还要绑到模型 / Agent
llm_with_tools = model.bind_tools([get_weather])
```

Pydantic AI：

```python
from pydantic_ai import Agent

agent = Agent('openai:gpt-6.1-sol')

@agent.tool_plain
def get_weather(city: str) -> str:
    """Get weather for city."""
    return f'{city} is sunny'
```

（LangChain 1.x 里工具直接传给 `create_agent(tools=[...])`，`bind_tools` 是更底层的写法。）

| 维度 | LangChain | Pydantic AI |
|------|-----------|-------------|
| 绑定方式 | `bind_tools([...])` 显式 | 装饰器直接绑 Agent |
| 上下文/依赖 | 通过闭包或 `RunnableConfig` | 一等公民 `RunContext[Deps]` |
| schema 来源 | docstring + Pydantic args | 同上，格式选项更明确 |
| 重试 / 失败反馈 | 要挂 `ToolErrorMiddleware`，工具异常默认中断运行 | 内置 `ModelRetry`（改了再试）/ `ToolFailed`（做完但失败）+ `retries` 预算 |
| 工具组合 | `Toolkit` / middleware | `Toolset`（更模块化，见[Toolsets 工具集](03-toolsets.md)）|

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 模型乱填参数 / 不调工具 | docstring 写得太粗，模型看不懂 | 用 `google` 风格补 `Args:`，每个参数写描述 |
| 工具拿不到 deps，`ctx.deps` 是 None | 用了 `@agent.tool_plain` 而不是 `@agent.tool` | 改成 `@agent.tool` 并加 `ctx: RunContext[...]` |
| `RunContext[T]` 但 T 写错了 | `deps_type` 必须和 `RunContext[T]` 的 T 一致 | 统一类型，建议用 dataclass / BaseModel |
| 模型一直重试 | `ModelRetry` 的消息太含糊 | 在消息里写清楚应该怎么改 |
| 业务异常没 raise `ModelRetry`，运行直接挂掉 | 普通异常会终止 run | 能纠正的用 `ModelRetry`；做完但失败的用 `ToolFailed` |
| 返回值过大塞满 context | 工具吐了 10MB JSON | 返回前裁剪、摘要，或只返回 ID |
| `require_parameter_descriptions=True` 后报 UserError | 某个参数忘了写描述 | 补 `Args:` 段落 |
| 异步工具偶发卡住 | CPU 密集任务直接写在 `async def` 里，堵住了事件循环 | 改成 `await asyncio.to_thread(...)` |

---

## 12. 生产建议

1. 项目里 docstring 统一用 `google` 风格，用 ruff 的 `D` 规则强制
2. 每个工具都写 `Args:` 和类型注解，schema 写得好不好直接影响调用准确率
3. 返回值尽量小，返回前裁剪到 1KB 以内，除非确实需要大段内容
4. 参数校验类的错误用 `ModelRetry`，确实无法继续的业务故障才抛普通异常终止运行
5. 生产环境打开 `require_parameter_descriptions=True`，免得上线后才发现 schema 缺描述
6. 同名工具放到不同的 Agent 或 Toolset 里，避免命名冲突（详见[Toolsets 工具集](03-toolsets.md)）

---

## 13. 本章 demo

完整可运行代码：[`demos/tools/01_function_tools.py`](../../demos/tools/01_function_tools.py)

下一篇：[Pydantic AI 02-02：Advanced Tool Features 高级工具特性](02-advanced-tools.md)，讲 prepare 钩子、多模态返回和 `ToolReturn`。
