# PE Technique 05：结构化输出 —— JSON / XML / Schema

> **一句话**：靠 prompt 写"请返回 JSON" 永远不稳。**用 API 内置的 structured output / tool use 强制 schema** 才是生产级方案。本篇讲三家的具体 API + 选择指南。

---

## 1. 三个层次的输出约束

| 层次 | 怎么做 | 稳定性 |
|------|--------|--------|
| **L1: prompt 描述** | "请返回 JSON 格式" | ~90% 合法 |
| **L2: response format** | API 参数指定 JSON mode | ~99% 合法 JSON，schema 未必对 |
| **L3: 强制 schema** | Tool use / structured output + Pydantic | ~99.9%，schema 100% 正确 |

**生产**：直接上 L3。

---

## 2. L1：纯 prompt 描述（不推荐生产）

```python
SYSTEM = """返回 JSON 对象，含字段 name (string), age (int)，不要任何 markdown 包装。"""
```

问题：
- 模型可能加 ```json fences
- 字段类型可能跑偏（age 写成 "30 岁"）
- 偶尔加额外字段或省略字段
- 偶尔输出解释文字

只适合**调试 / 原型**。

---

## 3. L2：JSON mode

三家都支持"保证输出合法 JSON"（但不保证 schema）：

### Anthropic
没有专门 JSON mode，但配合**prefill** 可以约束：

```python
resp = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=500,
    system="...",
    messages=[
        {"role": "user", "content": "提取数据..."},
        {"role": "assistant", "content": "{"},  # ← prefill
    ],
)
# resp.content[0].text 以 { 开头，几乎一定是 JSON
```

### OpenAI

```python
resp = client.chat.completions.create(
    model="gpt-4o-mini",
    response_format={"type": "json_object"},
    messages=[...],
)
```

### Gemini

```python
resp = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="...",
    config={"response_mime_type": "application/json"},
)
```

L2 解决了"合法 JSON" 问题，但**字段对不对、类型对不对**还得靠 prompt 描述。

---

## 4. L3：强制 schema（推荐）

### 4.1 OpenAI Structured Outputs

```python
from pydantic import BaseModel

class Person(BaseModel):
    name: str
    age: int
    email: str | None = None

resp = client.beta.chat.completions.parse(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Alice, 30 岁"}],
    response_format=Person,
)
person = resp.choices[0].message.parsed  # Person(name='Alice', age=30, email=None)
```

OpenAI 在 2024 后期推出 **Structured Outputs**，schema 100% 满足。

### 4.2 Anthropic Tool Use

Claude 没专门 "structured output"，但可以用 **tool use** 实现等价效果：

```python
import json
TOOL = {
    "name": "record_person",
    "description": "记录人员信息",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "email": {"type": "string", "nullable": True},
        },
        "required": ["name", "age"],
    },
}

resp = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=500,
    tools=[TOOL],
    tool_choice={"type": "tool", "name": "record_person"},  # 强制调用
    messages=[{"role": "user", "content": "Alice, 30 岁"}],
)
# 从 tool_use 块提数据
for block in resp.content:
    if block.type == "tool_use":
        data = block.input  # {"name": "Alice", "age": 30}
```

`tool_choice` 强制调用 → 等同于"必须输出符合此 schema"。

### 4.3 Gemini Structured Output

```python
from pydantic import BaseModel

class Person(BaseModel):
    name: str
    age: int

resp = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="Alice, 30 岁",
    config={
        "response_mime_type": "application/json",
        "response_schema": Person,
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

agent = Agent("openai:gpt-4o-mini", output_type=Person)
result = agent.run_sync("Alice, 30 岁")
print(result.output)  # Person(name='Alice', age=30)
```

Pydantic AI 自动选择正确的 API 模式（OpenAI structured output / Anthropic tool use / Gemini schema）。强烈推荐。

---

## 5. XML 输出（Claude 友好）

Claude 训练数据里 XML 标签**特别有效**：

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

Claude 几乎一定按这个 XML 输出。优点：

- 比 JSON 容错（缺字段不会让整体不可解析）
- 嵌套层级深时比 JSON 更易读
- 可以混入自然语言段（比如 `<reasoning>` + `<answer>`）

解析：

```python
import re
text = resp.content[0].text
name = re.search(r"<name>(.*?)</name>", text, re.S).group(1).strip()
```

或用 `lxml` / `BeautifulSoup` 更稳。

---

## 6. 嵌套 + 数组

Schema 嵌套很深时 structured output 还稳吗？经验上：

| 嵌套深度 | structured output | tool use | prompt 描述 |
|---------|-------------------|----------|-------------|
| 1 层 | ✅ 100% | ✅ 100% | ~95% |
| 2-3 层 | ✅ ~99% | ✅ ~99% | ~85% |
| 4+ 层 | ✅ ~95% | ✅ ~95% | ~60% |

深嵌套建议：

- 拆分输出（参考 [04-decomposition.md](./04-decomposition.md)）
- 用 Pydantic，把"复杂结构"分解成多个简单类
- discriminated union（联合类型）配合 Pydantic 用

---

## 7. enum 字段

强 enum 约束最有效：

```python
from typing import Literal

class Classification(BaseModel):
    category: Literal["bug", "feature", "complaint", "praise"]
    confidence: float
```

structured output / tool use 都能强制 category 只取这 4 个值之一。比 prompt "类别必须是..." 强 10 倍。

---

## 8. 失败处理：parse error 怎么办

哪怕用了 structured output，仍有极小概率 parse 失败。设计兜底：

```python
from pydantic import ValidationError

def safe_extract(text: str, max_retries: int = 2) -> Person | None:
    for attempt in range(max_retries):
        try:
            resp = client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                response_format=Person,
                messages=[{"role": "user", "content": text}],
            )
            return resp.choices[0].message.parsed
        except (ValidationError, json.JSONDecodeError) as e:
            if attempt == max_retries - 1:
                logger.exception("解析失败 max_retries 次")
                return None
            # 把错误反馈给模型再试
            ...
    return None
```

**自我修正**模式（detect → ask for fix）：

```python
messages = [{"role": "user", "content": "..."}]
resp = client.chat.completions.create(model="gpt-4o", messages=messages, ...)
content = resp.choices[0].message.content
try:
    data = MyModel.model_validate_json(content)
except ValidationError as e:
    messages.append({"role": "assistant", "content": content})
    messages.append({"role": "user", "content": f"上面的 JSON 不符合 schema，错误：{e}。请重新生成。"})
    resp2 = client.chat.completions.create(...)
```

---

## 9. 选哪条路？决策树

```
是否需要严格 schema？
├── 否 → L1 prompt 描述
└── 是 → 用哪个模型？
       ├── OpenAI 任意 → response_format=YourPydanticModel
       ├── Claude → tool use + tool_choice
       ├── Gemini → response_schema
       └── 跨多家 → Pydantic AI 抽象
```

---

## 10. demo：抽取 + 严格 schema

```python
# demos/techniques/05_structured_extract.py
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
    resp = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"提取产品信息：\n{d}"}],
        response_format=Product,
    )
    try:
        p = resp.choices[0].message.parsed
        print(f"{d} → {p}")
    except Exception as e:
        print(f"{d} → 失败: {e}")
```

---

## 11. 跨手册关联

| 关联 | 链接 |
|------|------|
| Pydantic AI structured output | ../../02-pydantic-ai/docs/01-basics/06-output-types.md |
| LangChain `with_structured_output` | ../../01-langchain/docs/01-langchain/07-tools.md |
| MCP tool inputSchema | ../../03-mcp/docs/02-server/01-tools.md |

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **靠 prompt "请返回 JSON"** | 用 API 强制 schema |
| **OpenAI 用 ChatCompletion 不用 beta.parse** | 切到 parse API 才有 strict schema |
| **Claude 用 prompt 描述代替 tool use** | tool use 才是 Claude 的 structured output |
| **schema 嵌套过深** | 拆 schema / 用 Pydantic 拆类 |
| **enum 用字符串 enum 描述代替 Literal** | Literal 才能强约束 |
| **parse 失败没兜底** | 加 retry + 让模型修正 |

---

## 13. 下一步

- 📖 好 few-shot 设计 → [06-examples-design.md](./06-examples-design.md)
- 📖 边界 / 拒绝 → [07-boundaries-refusal.md](./07-boundaries-refusal.md)
- 📖 跨 API 适配（Pydantic AI 一统） → [06-models/04-cross-model.md](../06-models/04-cross-model.md)

## 参考资料

- OpenAI Structured Outputs: https://platform.openai.com/docs/guides/structured-outputs
- Anthropic Tool Use: https://docs.anthropic.com/en/docs/build-with-claude/tool-use
- Gemini Structured Output: https://ai.google.dev/gemini-api/docs/structured-output
- Pydantic AI: https://ai.pydantic.dev
