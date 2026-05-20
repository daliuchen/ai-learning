# LangChain 04：Output Parsers 与结构化输出

> **一句话**：Output Parser 把 `AIMessage` 转成业务能用的类型（字符串/JSON/Pydantic 对象）。在现代模型时代，`with_structured_output` 是绝对首选，Parser 只在特殊场景才用。

---

## 1. 为什么要 Parser

模型默认输出是 `AIMessage`，content 是字符串：

```python
resp = model.invoke("给我三种水果的 JSON")
# AIMessage(content='```json\n{"fruits": ["apple", ...]}\n```')
```

业务代码不想处理 `AIMessage`，也不想自己写正则提取 JSON。Parser 帮你做这件事，且**它本身是 Runnable，能直接接在 chain 末端**：

```python
chain = prompt | model | JsonOutputParser()
result = chain.invoke({...})  # 已经是 dict
```

---

## 2. 解决方案演进路线

| 方案 | 难度 | 可靠性 | 现状 |
|------|------|--------|------|
| 字符串 prompt + 正则 | 高 | 低 | ❌ 已过时 |
| `JsonOutputParser` 提示模型输出 JSON | 中 | 中 | 旧模型可用 |
| `PydanticOutputParser` | 中 | 中 | 旧模型可用 |
| `with_structured_output(schema)` | **低** | **高** | ✅ **首选** |
| `bind_tools([schema])` 工具调用 | 低 | 高 | 内部机制 |

**推荐**：能用 `with_structured_output` 就用它，它会自动选最佳实现（OpenAI 用 function calling 或 JSON Mode，Anthropic 用 tool use）。

---

## 3. with_structured_output 详解

### 3.1 用 Pydantic 模型

```python
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI

class Joke(BaseModel):
    """一个笑话"""
    setup: str = Field(description="笑话的引子")
    punchline: str = Field(description="笑点")
    rating: int = Field(description="自评分 1-10")

model = ChatOpenAI(model="gpt-4o-mini")
structured = model.with_structured_output(Joke)

joke = structured.invoke("讲一个程序员笑话")
print(joke.setup)
print(joke.punchline)
print(joke.rating)
```

### 3.2 用 TypedDict / dict schema

不想引入 Pydantic 时：

```python
from typing_extensions import TypedDict, Annotated

class Joke(TypedDict):
    setup: Annotated[str, ..., "引子"]
    punchline: Annotated[str, ..., "笑点"]
    rating: Annotated[int, ..., "1-10 分"]

structured = model.with_structured_output(Joke)
joke = structured.invoke("讲一个笑话")  # 返回 dict
```

### 3.3 用 JSON Schema

```python
schema = {
    "title": "Joke",
    "type": "object",
    "properties": {
        "setup": {"type": "string"},
        "punchline": {"type": "string"},
    },
    "required": ["setup", "punchline"],
}
structured = model.with_structured_output(schema)
```

### 3.4 method 参数

OpenAI 系列模型支持三种内部实现：

```python
model.with_structured_output(Joke, method="function_calling")  # 默认
model.with_structured_output(Joke, method="json_mode")         # 仅约束 JSON 合法，不一定符合 schema
model.with_structured_output(Joke, method="json_schema")       # OpenAI 新版严格模式（推荐 gpt-4o 系列）
```

`json_schema` 模式在新模型上**保证字段齐全**，比 function calling 更严格。

### 3.5 include_raw

如果想同时拿原始 `AIMessage`：

```python
structured = model.with_structured_output(Joke, include_raw=True)
result = structured.invoke("...")
# {"raw": AIMessage(...), "parsed": Joke(...), "parsing_error": None}
```

适合做错误回退处理。

---

## 4. 经典 Output Parser 一览

### 4.1 StrOutputParser

最朴素，`AIMessage` → `str`：

```python
from langchain_core.output_parsers import StrOutputParser
chain = prompt | model | StrOutputParser()
chain.invoke({...})  # str
```

### 4.2 JsonOutputParser

```python
from langchain_core.output_parsers import JsonOutputParser

parser = JsonOutputParser()
# 把 parser.get_format_instructions() 拼到 prompt 里告诉模型
prompt = ChatPromptTemplate.from_messages([
    ("system", "{format_instructions}"),
    ("human", "{input}"),
]).partial(format_instructions=parser.get_format_instructions())

chain = prompt | model | parser
chain.invoke({"input": "返回 {fruits: [...]}"})  # dict
```

`JsonOutputParser` 还能配 Pydantic：

```python
parser = JsonOutputParser(pydantic_object=Joke)
```

### 4.3 PydanticOutputParser

```python
from langchain_core.output_parsers import PydanticOutputParser
parser = PydanticOutputParser(pydantic_object=Joke)
```

效果同 `JsonOutputParser(pydantic_object=...)`，只是返回 Pydantic 对象。

### 4.4 CSV / List

```python
from langchain.output_parsers import CommaSeparatedListOutputParser
parser = CommaSeparatedListOutputParser()
# 模型回 "苹果, 香蕉, 橙子" → ["苹果","香蕉","橙子"]
```

### 4.5 DatetimeOutputParser

```python
from langchain.output_parsers import DatetimeOutputParser
parser = DatetimeOutputParser()
# 模型回符合格式的字符串 → datetime 对象
```

### 4.6 EnumOutputParser

限制输出为枚举值：

```python
from enum import Enum
from langchain.output_parsers import EnumOutputParser

class Color(Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"

parser = EnumOutputParser(enum=Color)
```

### 4.7 OutputFixingParser / RetryOutputParser

JSON 解析失败时让另一个 LLM 修复：

```python
from langchain.output_parsers import OutputFixingParser
fixing = OutputFixingParser.from_llm(parser=parser, llm=model)
```

`RetryOutputParser` 类似但会带上原 prompt 一起重试。

---

## 5. JsonOutputParser 流式

`JsonOutputParser` 支持**部分 JSON 流式**，模型还没生成完时就能拿到不完整的 dict：

```python
parser = JsonOutputParser()
chain = prompt | model | parser

for partial in chain.stream({"input": "..."}):
    print(partial)  # 边生成边补全的 dict
```

这是 LangChain 在工程上做得很漂亮的一个点，内部用了 partial-json 解析。

---

## 6. 多分支抽取：with_structured_output + Union

模型有时要返回多种 schema 之一：

```python
from typing import Union

class Joke(BaseModel):
    setup: str
    punchline: str

class FactStatement(BaseModel):
    fact: str
    source: str

model_with = model.with_structured_output(Union[Joke, FactStatement])

resp = model_with.invoke("讲个笑话")     # 返回 Joke
resp = model_with.invoke("告诉我一个事实")  # 返回 FactStatement
```

底层会作为多个 function 让模型自选。

---

## 7. 自定义 Parser

继承 `BaseOutputParser`：

```python
from langchain_core.output_parsers import BaseOutputParser

class UpperCaseParser(BaseOutputParser[str]):
    def parse(self, text: str) -> str:
        return text.upper()

chain = prompt | model | UpperCaseParser()
```

支持流式的话实现 `_transform`，详见源码。

---

## 8. 一段对比 demo

```python
# demos/langchain/04_structured.py
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser

class Issue(BaseModel):
    """GitHub Issue"""
    title: str = Field(description="标题")
    severity: str = Field(description="严重程度 low/medium/high")
    tags: list[str] = Field(description="标签")

model = ChatOpenAI(model="gpt-4o-mini")

# ===== 方式 A：with_structured_output（推荐） =====
structured = model.with_structured_output(Issue)
print(structured.invoke("登录页 500 报错，需要尽快修复"))

# ===== 方式 B：JsonOutputParser =====
parser = JsonOutputParser(pydantic_object=Issue)
prompt = ChatPromptTemplate.from_messages([
    ("system", "提取 issue 信息，{format_instructions}"),
    ("human", "{input}"),
]).partial(format_instructions=parser.get_format_instructions())
chain = prompt | model | parser
print(chain.invoke({"input": "登录页 500 报错，需要尽快修复"}))
```

---

## 9. 选型决策树

```
要结构化输出吗？
├─ 否 → StrOutputParser
└─ 是
   ├─ 用支持 function calling 的模型？（GPT/Claude/Gemini）
   │  └─ ✅ with_structured_output(schema)
   ├─ 本地小模型 / 不支持工具？
   │  ├─ JsonOutputParser + Few-shot
   │  └─ 失败率高？→ OutputFixingParser 兜底
   └─ 需要从大段文本提取信息？
      └─ with_structured_output + Few-shot prompt
```

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `with_structured_output` 报错 schema 不支持 | 某些字段类型如 `set` 没法序列化 | 改 `list` |
| 返回字段全为 None | prompt 没引导，模型不知道字段含义 | Pydantic 加 `description` |
| 字段顺序混乱 | OpenAI function call 不保证顺序 | 字段顺序无意义，按 key 取 |
| 数字字段返回字符串 | 模型回了 `"123"` | 用 Pydantic 自动校验 + 类型转换 |
| 流式但 parser 卡住 | StrOutputParser 才支持 token 级流式，Json/Pydantic 是 chunk 级 | 接受 chunk 级即可 |
| `include_raw=True` 后忘了取 `parsed` | 返回是 dict | `result["parsed"]` |

---

## 11. 本章 demo

[`demos/langchain/04_structured.py`](../../demos/langchain/04_structured.py)

下一篇：[05-lcel.md](05-lcel.md) — LangChain 灵魂 LCEL 全解。
