# LangChain 04：Output Parsers 与结构化输出

> Output Parser 负责把 `AIMessage` 转成业务能直接用的类型：字符串、JSON 或 Pydantic 对象。不过现在的模型大都支持原生结构化输出，首选是 `with_structured_output`（写 agent 时用 `create_agent(response_format=...)`），Parser 只剩一些特殊场景还用得上。这篇把两条路都讲一遍，最后给一个选型流程。

---

## 1. 为什么要 Parser

模型默认输出是 `AIMessage`，content 是字符串：

```python
resp = model.invoke("给我三种水果的 JSON")
# AIMessage(content='```json\n{"fruits": ["apple", ...]}\n```')
```

业务代码不想跟 `AIMessage` 打交道，更不想自己写正则抠 JSON。Parser 做的就是这件事，而且它本身也是 Runnable，能直接接在 chain 的末尾：

```python
chain = prompt | model | JsonOutputParser()
result = chain.invoke({...})  # 已经是 dict
```

---

## 2. 几种方案的演进

| 方案 | 难度 | 可靠性 | 现状 |
|------|------|--------|------|
| 字符串 prompt + 正则 | 高 | 低 | 已过时 |
| `JsonOutputParser` 提示模型输出 JSON | 中 | 中 | 旧模型可用 |
| `PydanticOutputParser` | 中 | 中 | 旧模型可用 |
| `with_structured_output(schema)` | 低 | 高 | 首选 |
| `bind_tools([schema])` 工具调用 | 低 | 高 | 内部机制 |

能用 `with_structured_output` 就用它。它会按供应商选合适的实现：OpenAI 默认走 JSON Schema 严格模式，Anthropic 用 tool use。

> 写 agent（循环里有工具调用）时别对模型用 `with_structured_output`，应该用 `create_agent(response_format=...)`，见本篇第 6 节。

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

model = ChatOpenAI(model="gpt-6-luna")
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

OpenAI 系列模型（含千问等 OpenAI 兼容接口）支持三种内部实现：

```python
model.with_structured_output(Joke)                              # 默认就是 json_schema
model.with_structured_output(Joke, method="function_calling")   # 走工具调用（langchain-openai 0.3 之前的默认）
model.with_structured_output(Joke, method="json_mode")          # 仅约束 JSON 合法，不一定符合 schema
```

默认值变过：`langchain-openai` 从 0.3 起默认是 `json_schema`。这是 OpenAI 的严格结构化输出，能保证字段齐全；网上不少文章还写着默认走 function_calling，那已经过时了。代价是 schema 限制更多，比如顶层不能是 `Union`（见第 7 节）。遇到不兼容的 schema 或模型，可以显式改回 `method="function_calling"`。

### 3.5 include_raw

想同时拿到原始的 `AIMessage`：

```python
structured = model.with_structured_output(Joke, include_raw=True)
result = structured.invoke("...")
# {"raw": AIMessage(...), "parsed": Joke(...), "parsing_error": None}
```

解析失败时可以拿原始消息做兜底处理。

---

## 4. 经典 Output Parser 一览

### 4.1 StrOutputParser

最简单的一个，把 `AIMessage` 转成 `str`：

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

效果和 `JsonOutputParser(pydantic_object=...)` 一样，区别是返回 Pydantic 对象。

### 4.4 CSV / List

```python
from langchain_core.output_parsers import CommaSeparatedListOutputParser
parser = CommaSeparatedListOutputParser()
# 模型回 "苹果, 香蕉, 橙子" → ["苹果","香蕉","橙子"]
```

### 4.5 DatetimeOutputParser

```python
from langchain_classic.output_parsers import DatetimeOutputParser   # 1.x 起在 langchain-classic
parser = DatetimeOutputParser()
# 模型回符合格式的字符串 → datetime 对象
```

### 4.6 EnumOutputParser

把输出限制在几个枚举值里：

```python
from enum import Enum
from langchain_classic.output_parsers import EnumOutputParser

class Color(Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"

parser = EnumOutputParser(enum=Color)
```

### 4.7 OutputFixingParser / RetryOutputParser

JSON 解析失败时，让另一个 LLM 来修：

```python
from langchain_classic.output_parsers import OutputFixingParser
fixing = OutputFixingParser.from_llm(parser=parser, llm=model)
```

> 4.5 到 4.7 这几个 Parser 在 1.x 里都搬进了 `langchain-classic`（`pip install langchain-classic`）。有了 `with_structured_output` 之后基本用不到，留着主要是方便读老代码。

`RetryOutputParser` 用法类似，区别是重试时会带上原来的 prompt。

---

## 5. JsonOutputParser 流式

`JsonOutputParser` 支持流式输出不完整的 JSON，模型还没生成完，就能先拿到一个部分填充的 dict：

```python
parser = JsonOutputParser()
chain = prompt | model | parser

for partial in chain.stream({"input": "..."}):
    print(partial)  # 边生成边补全的 dict
```

内部用的是 partial-json 解析。前端需要边生成边渲染时，这个功能很好用。

## 6. agent 里的结构化输出：create_agent(response_format=...)

写 agent 时想让最终答案是一个对象，就传 `response_format`，结果在返回值的 `structured_response` 里：

```python
from pydantic import BaseModel
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy, ProviderStrategy

class Issue(BaseModel):
    title: str
    severity: str

agent = create_agent(model, tools=[...], response_format=Issue)   # 直接传 schema = 自动选策略
out = agent.invoke({"messages": [{"role": "user", "content": "登录页 500 报错，很紧急"}]})
print(out["structured_response"])      # Issue(title=..., severity=...)
```

有两种策略。`ProviderStrategy(schema)` 用模型自己的原生结构化输出，最可靠，但要模型支持。`ToolStrategy(schema)` 把 schema 包装成一个工具让模型去调用，凡是支持 tool calling 的模型都能用，校验失败时会自动把错误交回模型重试。直接传 schema 的话，LangChain 会按模型能力自动选一种。schema 可以是 Pydantic、`dataclass`、`TypedDict` 或 JSON Schema；注意 dataclass 返回的是 dataclass 实例，不是 dict。

---

## 7. 多分支抽取：Union 要包一层

有时模型要在几种 schema 里选一种返回。0.x 时可以直接写 `with_structured_output(Union[A, B])`，现在默认的 `json_schema` 模式下会直接报 `TypeError`。解决办法是把 Union 放进外层对象的一个字段里：

```python
from typing import Union

class Joke(BaseModel):
    setup: str
    punchline: str

class FactStatement(BaseModel):
    fact: str
    source: str

class Answer(BaseModel):
    result: Union[Joke, FactStatement]     # 让模型在两种形状里二选一

model_with = model.with_structured_output(Answer)

resp = model_with.invoke("讲个笑话").result          # Joke(...)
resp = model_with.invoke("告诉我一个事实").result     # FactStatement(...)
```

agent 里不受这个限制，`response_format=ToolStrategy(Union[Joke, FactStatement])` 可以直接传 Union。

---

## 8. 自定义 Parser

继承 `BaseOutputParser`：

```python
from langchain_core.output_parsers import BaseOutputParser

class UpperCaseParser(BaseOutputParser[str]):
    def parse(self, text: str) -> str:
        return text.upper()

chain = prompt | model | UpperCaseParser()
```

要支持流式，再实现 `_transform`，具体看源码。

---

## 9. 一段对比 demo

完整代码：[`demos/langchain/04_structured.py`](../../demos/langchain/04_structured.py)

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

model = ChatOpenAI(model="gpt-6-luna")

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

## 10. 选型决策树

```
要结构化输出吗？
├─ 否 → StrOutputParser
└─ 是
   ├─ 用支持 function calling 的模型？（GPT/Claude/Gemini/千问）
   │  ├─ 裸模型 → ✅ with_structured_output(schema)
   │  └─ agent 里 → ✅ create_agent(response_format=schema)
   ├─ 本地小模型 / 不支持工具？
   │  ├─ JsonOutputParser + Few-shot
   │  └─ 失败率高？→ OutputFixingParser 兜底
   └─ 需要从大段文本提取信息？
      └─ with_structured_output + Few-shot prompt
```

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `with_structured_output` 报错 schema 不支持 | 某些字段类型如 `set` 没法序列化；`json_schema` 模式下顶层不能是 `Union` | 改 `list`；Union 包一层对象 |
| `from langchain.output_parsers import EnumOutputParser` 找不到 | 1.x 搬到了 langchain-classic | `from langchain_classic.output_parsers import ...` |
| 返回字段全是 None | 模型不知道字段是什么意思 | 给 Pydantic 字段加 `description` |
| 字段顺序乱 | OpenAI function call 不保证顺序 | 顺序本来就不重要，按 key 取 |
| 数字字段返回了字符串 | 模型回的是 `"123"` | 交给 Pydantic 校验并自动转换类型 |
| 开了流式，结果却一次性全出来 | `JsonOutputParser` / `PydanticOutputParser` 本身会逐步吐出部分结果，卡住的通常是链里不支持流式的一步（如普通函数包成的 `RunnableLambda`） | 检查中间步骤，必要时改成生成器函数 |
| 开了 `include_raw=True` 却忘了取 `parsed` | 返回值是 dict | 用 `result["parsed"]` |

---

下一篇讲 LCEL：[LangChain 05：LCEL 与 Runnable 体系](05-lcel.md)
