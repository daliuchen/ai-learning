# Pydantic AI 06：结构化输出（Output Types）

> **一句话**：把 `output_type` 设成 Pydantic Model 或任意 Python 类型，Agent 就保证返回**结构化、经过校验**的对象，不再有"模型回了非法 JSON"的烦恼。

---

## 1. `output_type` 全谱

```python
from pydantic_ai import Agent
from pydantic import BaseModel
from typing import Union

# 1) 默认：str
Agent("openai:gpt-4o")
# r.output -> str

# 2) Pydantic Model
class Invoice(BaseModel):
    amount: float
    vendor: str

Agent("openai:gpt-4o", output_type=Invoice)
# r.output -> Invoice

# 3) 任意 Python 类型（标量、容器）
Agent("openai:gpt-4o", output_type=int)
Agent("openai:gpt-4o", output_type=list[str])
Agent("openai:gpt-4o", output_type=dict[str, float])

# 4) Union：模型自己挑
Agent("openai:gpt-4o", output_type=Union[Invoice, Receipt])
# 或新写法：output_type=[Invoice, Receipt]

# 5) dataclass / TypedDict
@dataclass
class Person: name: str; age: int
Agent("openai:gpt-4o", output_type=Person)
```

Pydantic AI 内部统一用 `TypeAdapter`，能塞进 Pydantic `TypeAdapter` 的几乎都能当 output_type。

---

## 2. 工作原理

`output_type=Invoice` 之后，Pydantic AI 干了三件事：

1. 用 `Invoice.model_json_schema()` 生成 JSON Schema
2. 把 schema 当 **工具**（默认 ToolOutput 模式）注册给模型
3. 模型必须调用该"工具"返回 Invoice，Agent 拿到 args 后 `Invoice.model_validate(args)` 校验

校验失败时，Agent 会：

- 把 Pydantic 的错误信息 `e.errors()` 拼成新消息
- 让模型 retry（最多 `retries` 次）
- 仍失败则抛 `UnexpectedModelBehavior`

也就是说，Pydantic AI **把结构化输出转化成了工具调用**，复用模型最稳定的能力。

---

## 3. 三种 Output 模式

`ToolOutput` / `NativeOutput` / `PromptedOutput`：

| 模式 | 原理 | 何时用 |
|------|------|--------|
| **ToolOutput**（默认） | 把 schema 注册成工具，让模型 call 它 | 大多数模型，最稳 |
| **NativeOutput** | 用 OpenAI / Gemini 的 "Structured Outputs" 原生功能 | gpt-4o / gpt-4o-mini / gemini-1.5+，**强保证** |
| **PromptedOutput** | 把 schema 塞进 system prompt，让模型自由发挥 | 不支持工具的本地模型 |

### 3.1 ToolOutput（默认）

```python
agent = Agent("openai:gpt-4o", output_type=Invoice)
# 等价于
from pydantic_ai.output import ToolOutput
agent = Agent("openai:gpt-4o", output_type=ToolOutput(Invoice))
```

可以定制工具名和重试：

```python
ToolOutput(Invoice, name="return_invoice", max_retries=3)
```

### 3.2 NativeOutput（最严格）

OpenAI 新 API 的 "Structured Outputs" 模式，**保证 100% 合 schema**：

```python
from pydantic_ai.output import NativeOutput

agent = Agent(
    "openai:gpt-4o-2024-08-06",
    output_type=NativeOutput(Invoice),
)
```

代价：每次请求略慢，且 schema 有限制（不能用 `dict[str, Any]` 等开放结构）。

### 3.3 PromptedOutput（兜底）

```python
from pydantic_ai.output import PromptedOutput

agent = Agent(
    "ollama:qwen2.5",   # 假设这个本地模型不支持工具
    output_type=PromptedOutput(
        Invoice,
        template="返回符合 schema 的 JSON：\n{schema}",
    ),
)
```

适合本地/老旧/不支持工具的模型，**可靠性最低**。

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

agent = Agent("openai:gpt-4o-mini", output_type=[Invoice, Receipt])

r = agent.run_sync("阿里云 ¥1280")
print(type(r.output))  # Invoice

r = agent.run_sync("买了 3 个苹果")
print(type(r.output))  # Receipt
```

内部把它们注册成多个工具，模型挑一个调用。

也可以混搭"结构化 + 自由文本"：

```python
agent = Agent("openai:gpt-4o", output_type=[Invoice, str])

# 模型能解析出来 → Invoice
# 不能解析 → 返回 str 说明原因
```

---

## 5. `@agent.output_validator` 后置校验

Pydantic 的字段校验不够时，写自定义校验：

```python
from pydantic_ai import Agent, ModelRetry, RunContext

class SQLQuery(BaseModel):
    query: str

agent = Agent("openai:gpt-4o", output_type=SQLQuery, deps_type=DB)

@agent.output_validator
async def check_sql(ctx: RunContext[DB], output: SQLQuery) -> SQLQuery:
    try:
        await ctx.deps.execute(f"EXPLAIN {output.query}")
    except Exception as e:
        raise ModelRetry(f"SQL 无效：{e}")
    return output
```

要点：

- 抛 `ModelRetry` 会让 agent 把错误信息发给模型让它**重试**
- 抛其他异常会直接失败
- 重试次数受 `output_retries` 和 `retries` 双重限制
- 校验器可以是 `async` 也可以是 sync
- 返回值会替换 `output`，可以做规范化

---

## 6. 工具调用作为输出（output functions）

进阶用法：把"输出"也写成一个 function，让模型调用它来结束 run：

```python
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

class Failure(BaseModel):
    reason: str

def run_sql(ctx: RunContext, query: str) -> list[dict]:
    """执行 SQL 并返回结果，作为最终输出"""
    return ctx.deps.execute(query)

agent = Agent(
    "openai:gpt-4o",
    output_type=[run_sql, Failure],
)
```

模型调 `run_sql` → 函数执行 → 返回值就是 `result.output`。

这模糊了"工具"和"输出"的边界，**适合"模型决策→执行→结束"模式**。

---

## 7. 实战：发票信息抽取

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
    date: date
    items: list[LineItem]
    total: float

    def model_post_init(self, __context) -> None:
        computed = sum(it.qty * it.price for it in self.items)
        if abs(computed - self.total) > 0.01:
            raise ValueError(f"total {self.total} 与明细累加 {computed} 不符")

agent = Agent(
    "openai:gpt-4o",
    output_type=Invoice,
    system_prompt="你是发票信息抽取助手，从文本中精确提取发票字段。",
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
print(r.output.model_dump_json(indent=2, default=str))
```

注意：

- `Field(description=...)` 会写进 schema 给模型看，是**最重要的引导手段**
- `model_post_init` 做交叉校验，发现不对会触发 `retries`
- 类型 `date` 会被自动序列化成 ISO 格式

---

## 8. vs LangChain

| 任务 | LangChain | Pydantic AI |
|------|-----------|-------------|
| Pydantic 输出 | `model.with_structured_output(Invoice)` | `Agent(..., output_type=Invoice)` |
| 多 schema | `Union[A, B]` 传给 `with_structured_output` | `output_type=[A, B]` |
| Native 模式 | `method="json_schema"` | `NativeOutput(Schema)` |
| 自定义校验 | 外面套 `@validator` 或 `OutputFixingParser` | `@agent.output_validator` 加 `ModelRetry` |
| 提示模型重试 | `OutputFixingParser` 用另一个 LLM 修复 | 内置：抛 `ModelRetry` 自动 retry |

LangChain 等价：

```python
from pydantic import BaseModel
from langchain_openai import ChatOpenAI

class Invoice(BaseModel):
    amount: float; vendor: str

model = ChatOpenAI(model="gpt-4o-mini")
structured = model.with_structured_output(Invoice)
print(structured.invoke("阿里云 ¥1280"))
```

Pydantic AI 等价：

```python
agent = Agent("openai:gpt-4o-mini", output_type=Invoice)
print(agent.run_sync("阿里云 ¥1280").output)
```

差异：

- LangChain 把"结构化"和"调用"分开（先 `with_structured_output` 再 `invoke`）
- Pydantic AI 把"结构化"作为 Agent 的属性（一次声明，一直生效）
- Pydantic AI 自动支持**工具循环 + 结构化输出共存**（LangChain 要切到 LangGraph）

---

## 9. 选型决策树

```
要结构化输出吗？
├─ 否 → output_type=str（默认）
└─ 是
   ├─ 用 OpenAI gpt-4o / Gemini 1.5+ ？
   │  ├─ 想要 100% schema 保证 → NativeOutput(Schema)
   │  └─ 否则用默认 ToolOutput
   ├─ 用 Anthropic / Groq / Mistral？
   │  └─ 默认 ToolOutput 即可（这些模型工具调用稳定）
   ├─ 用 Ollama 本地小模型？
   │  ├─ 模型支持工具（qwen2.5、llama3.3）→ ToolOutput
   │  └─ 不支持 → PromptedOutput
   └─ 需要"多种输出形态二选一"？
      └─ output_type=[A, B]
```

---

## 10. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| 字段全是 None | 缺 `Field(description=...)` 引导 | 给关键字段加描述 |
| `dict[str, Any]` 报 schema 错 | OpenAI Native 不支持开放 dict | 改成具体字段或换 `ToolOutput` |
| Union 时模型一直选错的那个 | 两个 schema 太相似 | 给 `ToolOutput(name=...)` 起个语义化名字 |
| 校验失败重试一直失败 | `ModelRetry` 的错误信息太抽象 | `raise ModelRetry(f"字段 {field}: {detail}")` 写清楚 |
| 数字回成字符串 | 模型不严格 | Pydantic 会自动 coerce，不用担心 |
| `output_type=set[str]` 报错 | set 不能 JSON 序列化 | 改 `list[str]` 自己去重 |
| `output_type=list[Invoice]` 旧版本不行 | 老 0.0.x 顶层 list 限制 | 包一层：`output_type=Invoices`（含 `items: list[Invoice]`）|
| 流式 + 结构化输出对接困难 | 部分字段流式不可用 | 用 `ctx.partial_output` 判断 |

---

## 11. 本章 demo

完整可运行代码：[`demos/basics/06_output_types.py`](../../demos/basics/06_output_types.py)

下一章：[07-messages-history.md](07-messages-history.md) —— 消息与对话历史。
