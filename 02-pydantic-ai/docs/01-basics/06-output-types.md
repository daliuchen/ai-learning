# Pydantic AI 06：结构化输出（Output Types）

> 把 `output_type` 设成 Pydantic Model 或别的 Python 类型，Agent 返回的就是校验过的对象，不用再自己处理模型回了非法 JSON 的情况。这篇讲它怎么做到的、有哪几种模式、校验失败时会发生什么。

以 `pydantic-ai 2.64.0` 为准。输出重试预算统一写成 `retries={"output": N}`。

---

## 1. `output_type` 能填什么

```python
from dataclasses import dataclass
from pydantic import BaseModel
from pydantic_ai import Agent

# 1) 默认：str
Agent("openai:gpt-6.1-sol")
# r.output -> str

# 2) Pydantic Model
class Invoice(BaseModel):
    amount: float
    vendor: str

Agent("openai:gpt-6.1-sol", output_type=Invoice)
# r.output -> Invoice

# 3) 任意 Python 类型（标量、容器）——非对象类型会被自动包一层对象再交给模型
Agent("openai:gpt-6.1-sol", output_type=int)
Agent("openai:gpt-6.1-sol", output_type=list[str])
Agent("openai:gpt-6.1-sol", output_type=dict[str, float])

# 4) Union / 列表：模型自己挑（每个成员注册成一个单独的输出工具）
Agent("openai:gpt-6.1-sol", output_type=[Invoice, Receipt])     # 或 Invoice | Receipt

# 5) dataclass / TypedDict
@dataclass
class Person:
    name: str
    age: int
Agent("openai:gpt-6.1-sol", output_type=Person)

# 6) 允许 None
Agent("openai:gpt-6.1-sol", output_type=Invoice | None)
```

Pydantic AI 内部统一用 `TypeAdapter`，能塞进 Pydantic `TypeAdapter` 的几乎都能当 output_type。更多形态：

| 形态 | 例子 | 说明 |
|------|------|------|
| 输出函数 | `[run_sql_query, SQLFailure]` | 模型「调用」这个函数，返回值作为最终输出 |
| `TextOutput(func)` | `TextOutput(split_into_words)` | 模型用纯文本回，再交给函数处理（函数必须是单个带 `str` 类型标注的参数） |
| `Choices({...})` | 意图分类 | 运行时才知道的一组选项，每个选项带说明 |
| `StructuredDict(schema, name=...)` | 一份 JSON Schema | 拿不到 Python 类型、只有 schema 时用 |
| 图片输出 | `BinaryImage` | 模型能产图时（见官方 Image Output） |

---

## 2. 工作原理

设了 `output_type=Invoice` 之后，Pydantic AI 默认做三件事：

1. 用 `Invoice.model_json_schema()` 生成 JSON Schema
2. 把 schema 当成工具注册给模型（默认的 Tool Output 模式）
3. 模型必须调用这个输出工具来返回 Invoice，Agent 拿到 args 后用 Pydantic 校验

校验失败时：

- 把 Pydantic 的错误信息拼成反馈，作为 `RetryFeedbackPart`（或被重试的工具返回）发回模型
- 模型重试（预算 `retries={"output": N}`，默认 1 次）
- 预算用完仍失败，抛 `UnexpectedModelBehavior`

也就是说，Pydantic AI 把结构化输出转成了工具调用，用的是模型最稳定的那项能力。

> V2 注意：V1 里检查消息历史中 `RetryPromptPart` 的代码不再匹配，重试反馈现在是 `RetryFeedbackPart` 或 `ToolReturnPart(outcome="retried")`。

---

## 3. 三种 Output 模式

`ToolOutput` / `NativeOutput` / `PromptedOutput`，都从 `pydantic_ai` 直接导入：

| 模式 | 原理 | 何时用 |
|------|------|--------|
| ToolOutput（默认） | 把 schema 注册成「输出工具」，让模型 call 它 | 几乎所有模型都支持，最稳 |
| NativeOutput | 用模型原生的「JSON Schema 结构化输出」，强制只输出符合 schema 的文本 | 支持的模型才能用，部分模型不能和工具同时用 |
| PromptedOutput | 把 schema 写进 instructions，让模型照着输出，再解析校验 | 没有工具调用也没有原生结构化输出的模型；有时质量反而更好 |

```python
from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput

# Tool Output：可以给每个输出工具起名、单独设重试
Agent("openai:gpt-6.1-sol", output_type=[ToolOutput(Fruit, name="return_fruit"), ToolOutput(Vehicle, name="return_vehicle")])
# Native Output：把一整组类型当作一个 schema
Agent("openai:gpt-6.1-sol", output_type=NativeOutput([Fruit, Vehicle], name="Fruit_or_vehicle"))
# Prompted Output：schema 塞进指令，可自定义模板
Agent("openai:gpt-6.1-sol", output_type=PromptedOutput([Vehicle, Device], template="Gimme some JSON: {schema}"))
```

一个和 LangChain 不同的默认：LangChain 直接传 schema 时会按 model profile 自动选原生结构化输出还是工具调用；Pydantic AI 默认用 profile 里的 `default_structured_output_mode`，内置 profile 几乎都是 `'tool'`，要原生模式得显式写 `NativeOutput`。用 `model.profile` 的 `default_structured_output_mode` / `supports_json_schema_output` 可以查模型默认和支持情况（见[Capabilities（能力）与 ModelProfile](../03-advanced/06-capabilities.md)）。

> 千问 `qwen-flash` 这类 OpenAI 兼容端点三种模式都能用，但表现有差别：Tool Output 最稳；换成 Native Output 时，同一个 prompt 下字段内容的语言可能和 Tool Output 不同；Prompted Output 的字段值（如 `severity`）可能落在预期的枚举之外，所以枚举字段最好用 `Literal` 约束。

### 3.1 ToolOutput（默认）

```python
agent = Agent("openai:gpt-6.1-sol", output_type=Invoice)
# 等价于
agent = Agent("openai:gpt-6.1-sol", output_type=ToolOutput(Invoice))
```

可以定制工具名和重试：

```python
ToolOutput(Invoice, name="return_invoice", max_retries=3)
```

每个输出工具各有一个重试计数器，用的是 agent 重试预算里「输出」那一份（`retries={"output": N}`，或用 `ToolOutput(max_retries=N)` 单独设）。

### 3.2 NativeOutput（最严格）

```python
from pydantic_ai import NativeOutput

agent = Agent("openai:gpt-6.1-sol", output_type=NativeOutput(Invoice))
```

代价：schema 有限制（比如开放 `dict[str, Any]` 在严格模式下不被接受），并且不是所有模型都支持；不支持时会抛 `UserError: Native structured output is not supported by this model.`。

### 3.3 PromptedOutput（兜底）

```python
from pydantic_ai import PromptedOutput

agent = Agent(
    "ollama:qwen3:14b",
    output_type=PromptedOutput(Invoice, template="返回符合 schema 的 JSON：\n{schema}"),
)
```

适合本地、老旧或不支持工具的模型，可靠性在三种里最低。

---

## 4. Union 输出：让模型自选

模型不知道用户给的到底是发票还是收据：

```python
from pydantic import BaseModel
from pydantic_ai import Agent

class Invoice(BaseModel):
    amount: float
    vendor: str

class Receipt(BaseModel):
    item: str
    qty: int

agent = Agent("openai-chat:qwen-flash", output_type=[Invoice, Receipt])

r = agent.run_sync("阿里云 ¥1280")
print(type(r.output))  # Invoice

r = agent.run_sync("买了 3 个苹果")
print(type(r.output))  # Receipt
```

内部会注册成多个工具，模型挑一个调用，比塞进一个大 schema 更不容易选错。也可以把结构化和自由文本混着用：

```python
agent = Agent("openai:gpt-6.1-sol", output_type=[Invoice, str])
# 模型能解析出来 → Invoice；不能解析 → 返回 str 说明原因（比如追问）
```

---

## 5. `@agent.output_validator` 后置校验

Pydantic 能校验结构和字段约束，但有些校验做不了：要做 IO 的（查数据库）、异步的、跨字段的业务规则。这些放在 `output_validator`：

```python
from pydantic_ai import Agent, ModelRetry, RunContext

class SQLQuery(BaseModel):
    query: str

agent = Agent("openai:gpt-6.1-sol", output_type=SQLQuery, deps_type=DB)

@agent.output_validator
async def check_sql(ctx: RunContext[DB], output: SQLQuery) -> SQLQuery:
    try:
        await ctx.deps.execute(f"EXPLAIN {output.query}")    # 真去数据库里 EXPLAIN 一下
    except Exception as e:
        raise ModelRetry(f"SQL 无效：{e}")
    return output
```

- 抛 `ModelRetry` 会把错误信息发给模型让它重试，每抛一次消耗一次输出重试预算（默认 1 次），用完抛 `UnexpectedModelBehavior`
- 抛其他异常会直接中断运行
- 预算用 `retries={"output": N}` 设（V1 的 `output_retries=` 已不存在）
- 校验器可以是 `async` 也可以是 sync；返回值会替换 `output`，可以做规范化

流式运行（`run_stream`）时，validator 会被调用多次：每收到一段部分输出调一次，拿到完整输出再调一次。只想校验完整结果的话，检查 `ctx.partial_output`：

```python
@agent.output_validator
def validate(ctx: RunContext, output: str) -> str:
    if ctx.partial_output:
        return output            # 部分输出先放过
    if len(output) < 50:
        raise ModelRetry("Output is too short.")
    return output
```

所以 **validator 里不要有副作用**（写文件、发消息），它可能被调多次，重试时也会再跑。

---

## 6. 输出函数（output functions）

输出也可以写成函数，让模型调用它来结束 run。输出函数和工具很像：参数由模型给、经 Pydantic 校验，可以拿 `RunContext`、可以抛 `ModelRetry`。区别在于模型必须调其中一个，调了运行就结束，结果不再回给模型：

```python
import re
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry

tables = {"users": [{"id": 1, "name": "Ethan"}]}

def run_sql_query(query: str) -> list[dict]:
    """Run a SQL query on the database."""
    m = re.match(r"SELECT (.+) FROM (\w+)", query)
    if m:
        if m.group(1) != "*":
            raise ModelRetry("Only 'SELECT *' is supported, you'll have to do column filtering manually.")
        if (table := m.group(2)) not in tables:
            raise ModelRetry(f"Unknown table '{table}'. Available tables: {', '.join(tables)}.")
        return tables[table]
    raise ModelRetry(f"Unsupported query: '{query}'.")

class SQLFailure(BaseModel):
    """An unrecoverable failure. Only use this when you can't change the query to make it work."""
    explanation: str

sql_agent = Agent[None, list[dict] | SQLFailure](
    "openai:gpt-6.1-sol",
    output_type=[run_sql_query, SQLFailure],
    instructions="You are a SQL agent that can run SQL queries on a database.",
)
```

模型要么交一条能跑的查询（结果直接作为输出），要么承认失败交一个 `SQLFailure`；查询有问题时 `ModelRetry` 把原因告诉它。docstring 写的是什么时候该用它，模型就靠这个选输出工具。这种写法让工具和输出的边界变得模糊，适合模型决策、执行、结束一气呵成的场景，也是多 agent 程序化交接的一种写法。

---

## 7. 运行时才知道的选项：`Choices`

`Literal` 和 `Enum` 要求选项在写代码时就确定。屏幕上当前可用的操作、一次搜索返回的记录，这类选项运行时才有，用 `Choices`：

```python
from pydantic_ai import Agent, Choices

Intent = Choices(
    {
        "refund": "The customer wants their money back.",
        "replace": "The customer wants a working unit instead.",
        "escalate": "Nobody on this tier can resolve it.",
    },
    name="customer_intent",
    description="What the customer is asking for.",
)
agent = Agent("openai:gpt-6.1-sol", output_type=Intent)
result = agent.run_sync("The blender arrived smashed. Just send me another one.")
print(result.output)   # replace
```

每个选项的说明会进 schema，输出是其中一个键（`str`）。`Choices()` 返回的是一个类型，所以也能用作 Pydantic 模型的字段或工具参数。

---

## 8. 实战：发票信息抽取

```python
from datetime import date
from pydantic import BaseModel, Field
from pydantic_ai import Agent

class LineItem(BaseModel):
    name: str
    qty: int
    price: float

class Invoice(BaseModel):
    invoice_no: str = Field(description="发票号")
    vendor: str = Field(description="开票方")
    invoice_date: date = Field(description="开票日期，ISO 格式")
    items: list[LineItem]
    total: float

    def model_post_init(self, __context) -> None:
        computed = sum(it.qty * it.price for it in self.items)
        if abs(computed - self.total) > 0.01:
            raise ValueError(f"total {self.total} 与明细累加 {computed} 不符")

agent = Agent(
    "openai-chat:qwen-flash",
    output_type=Invoice,
    instructions="你是发票信息抽取助手，从文本中精确提取发票字段。",
    retries=3,
)

text = """
发票号 IV-2024-001
开票方：阿里云计算有限公司
日期：2024-01-15
明细：
  - 弹性计算 x1 ¥800.00
  - 对象存储 x2 ¥240.00
合计：¥1280.00
"""

r = agent.run_sync(text)
print(r.output.model_dump_json(indent=2))
```

- `Field(description=...)` 会写进 schema 给模型看，是引导模型填对字段最有效的办法
- `model_post_init` 做交叉校验：校验失败会变成给模型的反馈、消耗输出重试预算（本例 `retries=3`）；用 TestModel 离线跑时它造不出自洽的数据，会因预算耗尽而失败
- `date` 类型会被自动序列化成 ISO 格式；`model_dump_json` 不需要（也不接受）`default=` 参数

---

## 9. vs LangChain

| 任务 | LangChain 1.x | Pydantic AI |
|------|---------------|-------------|
| Pydantic 输出 | `create_agent(..., response_format=Invoice)` / `model.with_structured_output(Invoice)` | `Agent(..., output_type=Invoice)` |
| 多 schema | `Union[A, B]` | `output_type=[A, B]` |
| Native 模式 | `ProviderStrategy` / `method="json_schema"` | `NativeOutput(Schema)` |
| 工具模式 | `ToolStrategy` | `ToolOutput(Schema)`（默认） |
| 自定义校验 | 自己套校验 / 工具里抛错 + middleware | `@agent.output_validator` + `ModelRetry` |
| 提示模型重试 | 校验错误可回喂（`ToolStrategy` 的错误处理） | 内置：`ModelRetry` / 校验错误自动回喂，预算 `retries` |

LangChain 1.x 等价：

```python
from langchain.agents import create_agent
agent = create_agent("openai:gpt-6.1-sol", tools=[], response_format=Invoice)
print(agent.invoke({"messages": [{"role": "user", "content": "阿里云 ¥1280"}]})["structured_response"])
```

Pydantic AI 等价：

```python
agent = Agent("openai:gpt-6.1-sol", output_type=Invoice)
print(agent.run_sync("阿里云 ¥1280").output)
```

几处差异：

- LangChain 默认按模型能力自动选策略；Pydantic AI 默认走 Tool Output（由 profile 决定，内置的几乎都是 tool），要原生必须显式写
- Pydantic AI 的 `output_type` 能放联合类型、输出函数、`TextOutput`、`Choices`，更灵活
- 校验错误回喂给模型，在 Pydantic AI 里是默认行为

---

## 10. 选型决策树

```
要结构化输出吗？
├─ 否 → output_type=str（默认）
└─ 是
   ├─ 模型支持原生 JSON Schema 输出，且想要强保证 → NativeOutput(Schema)
   ├─ 否则用默认 ToolOutput（OpenAI / Anthropic / Gemini / 千问等工具调用都稳定）
   ├─ 模型既不支持工具也不支持原生结构化 → PromptedOutput
   └─ 需要"多种输出形态二选一" → output_type=[A, B]
```

---

## 11. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| 字段全是 None / 乱填 | 缺 `Field(description=...)` 引导 | 给关键字段加描述 |
| `dict[str, Any]` 报 schema 错（Native 模式） | 严格 schema 不支持开放 dict | 改成具体字段或换 `ToolOutput` |
| `UserError: Native structured output is not supported` | 模型 profile 不支持原生输出（例如 `TestModel`） | 换 `ToolOutput`，或换支持的模型 |
| Union 时模型一直选错的那个 | 两个 schema 太相似 | 给 `ToolOutput(name=...)` 起语义化名字，写清 docstring |
| 校验失败重试一直失败 | `ModelRetry` 的错误信息太抽象 | `raise ModelRetry(f"字段 {field}: {detail}")` 写清楚 |
| `TypeError: Agent.__init__() got an unexpected keyword argument 'output_retries'` | V2 去掉了 | `retries={"output": N}` |
| `UserError: TextOutput must take a function taking a single str argument` | `TextOutput` 的函数缺 `str` 标注或参数个数不对 | 写成 `def f(text: str) -> ...` |
| validator 在流式里被多次触发 | 每个部分输出都会校验 | 用 `ctx.partial_output` 区分 |
| `model_dump_json(default=str)` 报错 | `BaseModel.model_dump_json` 没有 `default` 参数 | 去掉；日期等类型 Pydantic 自动序列化 |

---

## 12. 本章 demo

完整可运行代码：[`demos/basics/06_output_types.py`](../../demos/basics/06_output_types.py)

下一章：[Pydantic AI 07：消息与对话历史（Messages & History）](07-messages-history.md)。
