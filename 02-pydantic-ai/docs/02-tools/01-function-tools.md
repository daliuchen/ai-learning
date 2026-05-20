# Pydantic AI 02-01：Function Tools 函数工具

> **一句话**：用 `@agent.tool` 把任意 Python 函数装饰成工具，Pydantic AI 自动按 type hint + docstring 给 LLM 生成 schema，几乎不用手写 JSON Schema。

---

## 1. 为什么要工具

LLM 是个"嘴上厉害"的同事：能聊、能推理，但**不会真的去查数据库、查日历、调天气 API**。给它装上"手脚"——这就是工具（Tool）的本质：

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

Schema 是从签名 + docstring 自动生成的，工具调用循环框架自动跑。

---

## 2. 两种装饰器：`tool` vs `tool_plain`

| 装饰器 | 用途 | 函数第一个参数 |
|--------|------|----------------|
| `@agent.tool` | 工具需要拿到 Agent 的 deps / usage / 历史消息 | `ctx: RunContext[DepsType]` |
| `@agent.tool_plain` | 工具是纯函数，不需要任何上下文 | 没有特殊参数 |

```python
from pydantic_ai import Agent, RunContext

agent = Agent('openai:gpt-4o-mini', deps_type=str)

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

**记忆方法**：`tool` 是默认（多数工具都要依赖），带 `_plain` 才是"纯函数"特例。

---

## 3. Schema 是怎么生成的

Pydantic AI 用 [griffe](https://mkdocstrings.github.io/griffe/) 解析 docstring，把它分成两部分：

- **第一段（标题段）** → 工具的 `description`
- **`Args:` 段每个参数** → 该参数的 `description`

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

模型收到的 schema 长这样（自动生成）：

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

不指定时框架会自动嗅探，但显式写 `docstring_format='google'` 更稳。

**`require_parameter_descriptions=True`** 强制每个参数都要有描述，少一个就抛 `UserError`——生产环境很值得开。

---

## 4. 最小可跑例子

```python
import random
from pydantic_ai import Agent, RunContext

agent = Agent(
    'openai:gpt-4o-mini',
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

模型会自己决定先调 `roll_dice`、再调 `get_player_name`、最后拼 reply，循环框架自动跑。

---

## 5. 不用装饰器：直接构造 `Tool`

需要复用同一个函数给多个 Agent，或者想覆盖名字/描述时：

```python
from pydantic_ai import Agent, Tool, RunContext

def roll_dice() -> str:
    """Roll a six-sided die."""
    return str(random.randint(1, 6))

def get_player_name(ctx: RunContext[str]) -> str:
    """Get the player's name."""
    return ctx.deps

agent = Agent(
    'openai:gpt-4o-mini',
    deps_type=str,
    tools=[
        Tool(roll_dice, takes_ctx=False),         # 明确告诉框架是 plain
        Tool(get_player_name, takes_ctx=True),    # 这个吃 ctx
    ],
)
```

`takes_ctx=None`（默认）会自动嗅探，但写显式一点不容易踩坑。

`Tool` 还能改名字 / 改描述：

```python
Tool(roll_dice, name='throw_die', description='Throw one six-sided dice', takes_ctx=False)
```

也能直接从 JSON Schema 构造（函数无 docstring 时救命）：

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

注意 `Tool.from_schema` **不做参数校验**，所有参数原样以 `**kwargs` 传进去。

---

## 6. 返回值类型

工具可以返回任何能被 Pydantic 序列化为 JSON 的对象：

| 返回类型 | 说明 |
|----------|------|
| `str` / `int` / `float` / `bool` | 最常用 |
| `dict` / `list` | 结构化数据 |
| `pydantic.BaseModel` | 自动 dump 成 JSON |
| `dataclasses.dataclass` / `TypedDict` | 同上 |
| `ImageUrl` / `BinaryContent` / `DocumentUrl` | 多模态（详见第 2 篇） |
| `ToolReturn(...)` | 返回值 + 富内容 + 内部 metadata 三层分离（详见第 2 篇） |

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

模型收到的是 JSON 字符串，但你写的是强类型对象，IDE 补全 + 单测都好写。

---

## 7. 同步 vs 异步工具

直接写 `async def` 就行，框架自动识别：

```python
import httpx

@agent.tool_plain
async def fetch_url(url: str) -> str:
    """Fetch URL and return body."""
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        return r.text[:500]
```

**实践规则**：

- IO（HTTP / DB / 文件）→ 写 `async def`，工具默认并行执行
- CPU 密集 → 用 `async` 包一层 `asyncio.to_thread(...)`，别堵事件循环
- 简单同步计算 → 直接 `def` 也行，框架会丢到线程池

---

## 8. 让模型重试：`ModelRetry`

参数语法上没问题但**业务上**校验不通过时，别 raise 普通异常（会直接终止 run），用 `ModelRetry` 让模型重新生成参数：

```python
from pydantic_ai import ModelRetry

@agent.tool_plain
def lookup_user(user_id: str) -> dict:
    """Lookup a user by ID."""
    if not user_id.startswith('U-'):
        raise ModelRetry(
            f"Invalid user_id format: {user_id!r}. "
            "Must start with 'U-'. Please retry."
        )
    return {'id': user_id, 'name': 'Alice'}
```

异常的 message 会以 `RetryPromptPart` 形式送回 LLM，提示它怎么改。

**重试次数**有三层覆盖（从细到粗）：

```python
@agent.tool(retries=3)             # 1. 单工具级
def my_tool(...): ...

toolset = FunctionToolset(max_retries=5)   # 2. toolset 级

agent = Agent(..., retries={'tools': 2})   # 3. agent 级
```

超过次数会抛 `UnexpectedModelBehavior('Tool ... exceeded max retries count of N')`。

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
    'openai:gpt-4o-mini',
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

模型会自己拆任务，先查日历再查天气，最后合成自然语言回答。

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

agent = Agent('openai:gpt-4o-mini')

@agent.tool_plain
def get_weather(city: str) -> str:
    """Get weather for city."""
    return f'{city} is sunny'
```

**差异**：

| 维度 | LangChain | Pydantic AI |
|------|-----------|-------------|
| 绑定方式 | `bind_tools([...])` 显式 | 装饰器直接绑 Agent |
| 上下文/依赖 | 通过闭包或 `RunnableConfig` | 一等公民 `RunContext[Deps]` |
| schema 来源 | docstring + Pydantic args | 同上，但格式选项更显式 |
| 重试机制 | 没有标准做法 | 内置 `ModelRetry` + `retries` |
| 工具组合 | `Toolkit` | `Toolset`（更模块化，见第 3 篇）|

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 模型乱填参数 / 不调工具 | docstring 写得太糙，模型读不懂 | 用 `google` 风格补 `Args:`，每个参数加描述 |
| 工具拿不到 deps，`ctx.deps` 是 None | 用了 `@agent.tool_plain` 而不是 `@agent.tool` | 改成 `@agent.tool` 并加 `ctx: RunContext[...]` |
| `RunContext[T]` 但 T 写错了 | `deps_type` 必须和 `RunContext[T]` 的 T 一致 | 统一类型，建议用 dataclass / BaseModel |
| 模型一直重试 | `ModelRetry` 的消息太模糊 | 提示里告诉它**该怎么改** |
| 业务异常没 raise `ModelRetry` 直接挂 | 普通异常会终止 run | 用 `ModelRetry` 包一层 |
| 返回值过大塞爆 context | 工具吐了 10MB JSON | 返回前裁剪 / 摘要 / 仅返回 ID |
| `require_parameter_descriptions=True` 后报 UserError | 忘了给某个参数写描述 | 补 `Args:` 段落 |
| 异步工具偶发卡住 | CPU 密集任务直接写在 `async def` 里阻塞事件循环 | 改 `await asyncio.to_thread(...)` |

---

## 12. 生产建议

1. **统一 docstring 风格**：项目里一律 `google`，配 ruff `D` 规则强制
2. **每个工具都加 `Args:` + 类型提示**，schema 质量直接决定调用准确度
3. **返回值尽量精简**，工具返回前主动裁剪到 < 1KB（除非确实要返回大段内容）
4. **校验型异常一律 `ModelRetry`**，业务真错才 raise 普通异常终止 run
5. **生产开 `require_parameter_descriptions=True`**，避免上线后 schema 残废
6. **同名工具一定挂不同 Agent / Toolset**，避免命名冲突（详见第 3 篇）

---

## 13. 本章 demo

完整可运行代码：[`demos/tools/01_function_tools.py`](../../demos/tools/01_function_tools.py)

跑通后下一篇：[02-advanced-tools.md](02-advanced-tools.md) — prepare 钩子、多模态返回、`ToolReturn` 等高级特性。
