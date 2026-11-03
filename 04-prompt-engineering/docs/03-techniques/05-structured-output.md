# PE Technique 05：结构化输出（JSON / XML / Schema）

> 在 prompt 里写「请返回 JSON」，怎么调都不会完全稳定。生产环境应该用 API 自带的 structured output 或 tool use 来强制 schema。这篇介绍三家各自的 API 写法，以及该选哪一种。

---

## 1. 输出约束的三个层次

| 层次 | 怎么做 | 能保证什么 |
|------|--------|--------|
| L1: prompt 描述 | "请返回 JSON 格式" | 什么都不保证，多数时候是合法 JSON |
| L2: response format | API 参数指定 JSON mode | 合法 JSON，字段和类型未必对 |
| L3: 强制 schema | Tool use / structured output + Pydantic | strict 模式下返回的 JSON 一定符合 schema；残余失败来自拒答、截断，或 schema 用了不被支持的特性 |

生产环境直接用 L3。

---

## 2. L1：纯 prompt 描述（不建议用于生产）

```python
SYSTEM = """返回 JSON 对象，含字段 name (string), age (int)，不要任何 markdown 包装。"""
```

这样写，模型可能会套上 ```json 代码围栏，字段类型可能跑偏（age 写成「30 岁」），偶尔多出或漏掉字段，偶尔还会附一段解释文字。只适合调试和做原型。

---

## 3. L2：JSON mode

OpenAI 和 Gemini 有一种模式，保证输出是合法 JSON，但不保证符合 schema。Claude 没有单独的 JSON mode，直接用 L3 的结构化输出。

### Anthropic
早期常用 prefill（预先填一个 `{` 作为 assistant 的开头）逼模型输出 JSON，但 Claude 4.6 起已不支持 prefill，带 prefill 的请求会返回 400。请直接用 L3 的 `output_config.format`（见 4.2），别再这样写：

```python
# ❌ Claude 4.6+ 返回 400
messages=[
    {"role": "user", "content": "提取数据..."},
    {"role": "assistant", "content": "{"},  # ← prefill 已移除
]
```

### OpenAI

```python
resp = client.chat.completions.create(
    model="gpt-6-luna",
    response_format={"type": "json_object"},
    messages=[...],
)
```

### Gemini

```python
resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="...",
    config={"response_mime_type": "application/json"},
)
```

### Qwen（百炼，OpenAI 兼容）
同样支持 `response_format={"type": "json_object"}`。用这个模式时，prompt 里必须出现「JSON」字样，否则接口会报错。

L2 保证了 JSON 合法，但字段对不对、类型对不对，仍然只能靠 prompt 描述。

---

## 4. L3：强制 schema（推荐）

### 4.1 OpenAI Structured Outputs

```python
from pydantic import BaseModel

class Person(BaseModel):
    name: str
    age: int
    email: str | None = None

# Responses API（官方推荐）
resp = client.responses.parse(
    model="gpt-6-luna",
    input=[{"role": "user", "content": "Alice, 30 岁"}],
    text_format=Person,
)
person = resp.output_parsed  # Person(name='Alice', age=30, email=None)

# Chat Completions 等价写法
resp = client.chat.completions.parse(
    model="gpt-6-luna",
    messages=[{"role": "user", "content": "Alice, 30 岁"}],
    response_format=Person,
)
person = resp.choices[0].message.parsed
```

`strict` 模式下返回的 JSON 一定符合 schema（schema 里有不被支持的特性时，请求会直接报错，而不是悄悄放宽）。没拿到结果的情况只剩两种：模型拒答，输出里会有 `refusal` 项，要先判断它，再读 `output_parsed`；输出被 `max_output_tokens` 截断，JSON 不完整。

### 4.2 Anthropic Structured Outputs（`output_config.format`）

Claude 现在原生支持结构化输出：用 JSON Schema 约束响应格式。SDK 的 `messages.parse` 可以直接传 Pydantic 类，返回校验过的实例：

```python
class Person(BaseModel):
    name: str
    age: int
    email: str | None = None

resp = client.messages.parse(
    model="claude-haiku-5-5",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Alice, 30 岁"}],
    output_format=Person,            # SDK 会转成 output_config={"format": {...}}
)
person = resp.parsed_output          # Person(name='Alice', age=30, email=None)
```

不用 Pydantic 时，直接写 `output_config={"format": {"type": "json_schema", "schema": {...}}}`，结果 JSON 在 `resp.content[-1].text` 里。

> 旧写法是用 `tool_choice` 强制模型调用某个 tool 来拿 JSON。在 Fable 5.1 / Opus 5.5 / Sonnet 5.5 上，强制 `tool_choice`（`any` / `tool`）会返回 400。需要工具调用时，用 `tool_choice: auto`，在 tool 上加 `strict: true`（保证参数符合 schema），再在 prompt 里点名这个工具；只想拿 JSON 就用上面的结构化输出。分类任务也可以用带 enum 字段的 tool，或者结构化输出。
> 另外，`citations` 和 `output_config.format` 不能同时使用，会返回 400。

### 4.3 Gemini Structured Output

```python
from pydantic import BaseModel

class Person(BaseModel):
    name: str
    age: int

resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="Alice, 30 岁",
    config={
        "response_mime_type": "application/json",
        "response_json_schema": Person.model_json_schema(),   # 也可用 "response_schema": Person
    },
)
person = Person.model_validate_json(resp.text)
```

### 4.4 跨家：Pydantic AI 统一接口

```python
from pydantic import BaseModel
from pydantic_ai import Agent

class Person(BaseModel):
    name: str
    age: int

agent = Agent("openai:gpt-6-luna", output_type=Person)
result = agent.run_sync("Alice, 30 岁")
print(result.output)  # Person(name='Alice', age=30)
```

Pydantic AI 默认用工具调用（Tool Output）拿结构化结果，换哪家模型都是同一套写法；想用模型原生的 JSON Schema 结构化输出，显式写 `output_type=NativeOutput(Person)`（见 [Pydantic AI 06](../../../02-pydantic-ai/docs/01-basics/06-output-types.md)）。要同时对接多家模型的话，我很推荐它。

---

## 5. XML 输出（适合 Claude，不依赖任何 API 特性）

Claude 对 XML 标签的遵循度很高：

```python
SYSTEM = """从用户输入中抽取信息。

返回格式：

<extraction>
<name>...</name>
<age>...</age>
<email>...</email>   (如果没有就 <email/>)
</extraction>
"""
```

Claude 几乎总会按这个格式输出。XML 有几个好处：比 JSON 容错，缺一个字段不会让整体解析失败；层级深的时候比 JSON 好读；还能混进自然语言段落，比如 `<reasoning>` 加 `<answer>`。

解析可以用正则：

```python
import re
text = resp.content[-1].text
name = re.search(r"<name>(.*?)</name>", text, re.S).group(1).strip()
```

用 `lxml` 或 `BeautifulSoup` 会更稳。

---

## 6. 嵌套和数组

schema 嵌套很深时，strict 模式仍然保证格式合法，但有两个现实问题：各家对嵌套深度、属性总数、enum 数量都有上限，超了请求会被拒；字段越多越深，模型越容易在内容上填错（格式对，值不对），纯 prompt 描述的方式更是会明显变差。

嵌套很深时，可以把输出拆成几步（参考 [Decomposition 任务拆解](./04-decomposition.md)），用 Pydantic 把复杂结构拆成几个简单的类，或者配合 Pydantic 使用 discriminated union（可辨识联合类型）。

---

## 7. enum 字段

enum 是最有效的强约束之一：

```python
from typing import Literal

class Classification(BaseModel):
    category: Literal["bug", "feature", "complaint", "praise"]
    confidence: float
```

structured output 和开了 `strict` 的 tool use 都能保证 category 只取这 4 个值之一，比在 prompt 里写「类别必须是……」可靠得多。

---

## 8. 解析失败怎么办

即使用了 structured output，也会拿不到结果：模型拒答时 `parsed` 是 `None`、`refusal` 有内容；输出被 token 上限截断时 SDK 抛 `LengthFinishReasonError`；被内容过滤拦下时抛 `ContentFilterFinishReasonError`。这些都要兜底：

```python
import logging

import openai
from pydantic import ValidationError

logger = logging.getLogger(__name__)

def safe_extract(text: str, max_retries: int = 2) -> Person | None:
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.parse(
                model="gpt-6-luna",
                response_format=Person,
                messages=[{"role": "user", "content": text}],
            )
        except (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError, ValidationError):
            logger.warning("第 %d 次抽取失败", attempt + 1, exc_info=True)
            continue  # 截断时可以调大 max_tokens 再试
        msg = resp.choices[0].message
        if msg.refusal:
            logger.info("模型拒答：%s", msg.refusal)
            return None  # 拒答重试通常没用
        return msg.parsed
    return None
```

另一种是自我修正：发现校验失败，就把错误信息发回给模型，让它重新生成。

```python
messages = [{"role": "user", "content": "..."}]
resp = client.chat.completions.create(model="gpt-6.1-terra", messages=messages, ...)
content = resp.choices[0].message.content
try:
    data = MyModel.model_validate_json(content)
except ValidationError as e:
    messages.append({"role": "assistant", "content": content})
    messages.append({"role": "user", "content": f"上面的 JSON 不符合 schema，错误：{e}。请重新生成。"})
    resp2 = client.chat.completions.create(...)
```

---

## 9. 怎么选

```
是否需要严格 schema？
├── 否 → L1 prompt 描述
└── 是 → 用哪个模型？
       ├── OpenAI 任意 → response_format=YourPydanticModel
       ├── Claude → output_config.format（messages.parse）
       ├── Gemini → response_json_schema / response_schema
       └── 跨多家 → Pydantic AI 抽象
```

---

## 10. demo：抽取 + 严格 schema

```python
"""演示 structured output 对比 prompt 描述"""
from pydantic import BaseModel, Field
from typing import Literal
from openai import OpenAI

client = OpenAI()

class Product(BaseModel):
    name: str = Field(description="产品名")
    price: float = Field(description="价格数字")
    currency: Literal["CNY", "USD", "EUR"]
    sku: str | None = None

DESCRIPTIONS = [
    "MacBook Air M3，售价 ¥9999",
    "Logitech MX Master 3S, $79.99, model MXM3S",
    "罗技 MX Master 鼠标 €120",
    "Some random text not a product",
]

for d in DESCRIPTIONS:
    try:
        resp = client.chat.completions.parse(
            model="gpt-6-luna",
            messages=[{"role": "user", "content": f"提取产品信息：\n{d}"}],
            response_format=Product,
        )
    except Exception as e:  # 截断、内容过滤、网络错误等
        print(f"{d} → 失败: {e}")
        continue
    msg = resp.choices[0].message
    print(f"{d} → {msg.parsed if msg.parsed else '拒答: ' + str(msg.refusal)}")
```

---

## 11. 跨手册关联

| 关联 | 链接 |
|------|------|
| Pydantic AI structured output | [Pydantic AI 06：结构化输出（Output Types）](../../../02-pydantic-ai/docs/01-basics/06-output-types.md) |
| LangChain `with_structured_output` | [LangChain 04：Output Parsers 与结构化输出](../../../01-langchain/docs/01-langchain/04-output-parsers.md) |
| MCP tool inputSchema | [MCP Server 01：用 Tools 暴露可调用的操作](../../../03-mcp/docs/02-server/01-tools.md) |

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| 靠 prompt 写「请返回 JSON」 | 改用 API 强制 schema |
| OpenAI 只用 `create` 不用 `parse` | 换成 `parse` / `text_format`，才有严格 schema |
| Claude 用 prefill 或强制 `tool_choice` 拿 JSON | 4.6+/5.5 会返回 400，改用 `output_config.format` |
| schema 嵌套过深 | 拆 schema，用 Pydantic 拆成多个类 |
| 用普通字符串描述 enum，而不是 Literal | 只有 Literal 才是强约束 |
| 解析失败没有兜底 | 加重试，并让模型自行修正 |

---

## 13. 下一步

- [Few-shot 示例怎么设计](./06-examples-design.md)
- [边界条件与 Refusal 行为塑造](./07-boundaries-refusal.md)
- [跨模型可移植 + 成本性能权衡](../06-models/04-cross-model.md)：用 Pydantic AI 统一多家 API

## 参考资料

- OpenAI Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Anthropic Structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Anthropic Tool Use: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
- Gemini Structured Output: https://ai.google.dev/gemini-api/docs/structured-output
- Pydantic AI: https://ai.pydantic.dev
