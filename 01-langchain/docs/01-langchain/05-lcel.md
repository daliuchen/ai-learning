# LangChain 05：LCEL 与 Runnable 体系

> **一句话**：LCEL = "用 `|` 把 Runnable 串成数据流"。它是 LangChain 现代 API 的灵魂，掌握了 LCEL 才算真正掌握了 LangChain。

---

## 1. LCEL 是什么

**LangChain Expression Language**，本质是一套基于 `Runnable` 协议的组合 DSL。

设计目标：

1. **声明式**：你只描述数据流，不写循环/await
2. **统一接口**：所有组件都实现 `invoke/batch/stream`，组合后自动继承所有能力
3. **可观测**：每个步骤自动产生 LangSmith trace
4. **无锁性能**：自动并行、自动流式

```python
chain = prompt | model | parser

# 自动具备的能力
chain.invoke(x)        # 同步
await chain.ainvoke(x) # 异步
chain.batch([x, y])    # 批量并行
chain.stream(x)        # 流式
chain.astream_events(x, version="v2")  # 细粒度事件
```

---

## 2. Runnable 协议

每个 LCEL 组件必须实现 `Runnable[Input, Output]`，核心方法：

```python
class Runnable(Generic[Input, Output]):
    def invoke(self, input: Input, config: Optional[RunnableConfig] = None) -> Output: ...
    async def ainvoke(self, input: Input, config: Optional[RunnableConfig] = None) -> Output: ...
    def batch(self, inputs: List[Input], ...) -> List[Output]: ...
    async def abatch(self, inputs: List[Input], ...) -> List[Output]: ...
    def stream(self, input: Input, ...) -> Iterator[Output]: ...
    async def astream(self, input: Input, ...) -> AsyncIterator[Output]: ...
```

只要实现了 `invoke`（或异步），LangChain 用 `_default_stream` 等默认实现把其他方法补齐。

`Runnable` 内置魔法方法：

```python
a | b     →  RunnableSequence(a, b)
{"x": a}  →  RunnableParallel({"x": a})
```

后者很关键：dict 字面量会自动包成 `RunnableParallel`，并行运行所有 value。

---

## 3. LCEL 的"原语"

### 3.1 RunnableSequence — 串联

```python
seq = a | b | c
```

等价于：

```python
from langchain_core.runnables import RunnableSequence
seq = RunnableSequence(a, b, c)
```

执行：`c.invoke(b.invoke(a.invoke(x)))`

### 3.2 RunnableParallel — 并联

把同一个输入分发到多个分支并行执行，结果合并成 dict：

```python
from langchain_core.runnables import RunnableParallel

multi = RunnableParallel({
    "joke": prompt_joke | model | StrOutputParser(),
    "poem": prompt_poem | model | StrOutputParser(),
})

multi.invoke({"topic": "猫"})
# {"joke": "...", "poem": "..."}
```

简写：

```python
chain = {
    "joke": prompt_joke | model | StrOutputParser(),
    "poem": prompt_poem | model | StrOutputParser(),
} | summarize_prompt | model
```

注意：**dict 出现在 `|` 链里才会自动转 RunnableParallel**，单独定义需要显式写 `RunnableParallel({...})`。

### 3.3 RunnableLambda — 把任意函数变成 Runnable

```python
from langchain_core.runnables import RunnableLambda

def shout(text: str) -> str:
    return text.upper() + "!!!"

shouter = RunnableLambda(shout)
print(shouter.invoke("hello"))  # HELLO!!!

# 等价的链
chain = prompt | model | StrOutputParser() | RunnableLambda(shout)
```

`@chain` 装饰器是更甜的语法：

```python
from langchain_core.runnables import chain as as_chain

@as_chain
def shout(text: str) -> str:
    return text.upper()
```

### 3.4 RunnablePassthrough — 直通 + 注入

最常见的用法：把输入原样传下去，同时往字典里加新 key：

```python
from langchain_core.runnables import RunnablePassthrough

chain = (
    RunnablePassthrough.assign(
        upper=lambda x: x["text"].upper(),
        length=lambda x: len(x["text"]),
    )
    | prompt
    | model
)

chain.invoke({"text": "hello"})
# prompt 看到的输入：{"text": "hello", "upper": "HELLO", "length": 5}
```

### 3.5 RunnableBranch — 条件分支

```python
from langchain_core.runnables import RunnableBranch

router = RunnableBranch(
    (lambda x: "?" in x["q"], faq_chain),         # 条件 + 分支
    (lambda x: x["q"].startswith("代码"), code_chain),
    default_chain,                                 # 兜底
)

router.invoke({"q": "什么是 LCEL？"})
```

也可以用 `RunnableLambda` 写更灵活的 router：

```python
def route(info):
    if "代码" in info["topic"]:
        return code_chain
    return general_chain

chain = first_step | route   # route 返回的 Runnable 会被 invoke
```

返回 Runnable 会被自动 invoke，这是个非常方便的小细节。

### 3.6 配置：with_config / configurable_fields

#### 静态配置

```python
chain = (prompt | model).with_config({
    "run_name": "answer-qa",
    "tags": ["qa", "prod"],
    "metadata": {"user_id": "u1"},
    "callbacks": [...],
    "max_concurrency": 5,
})
```

#### 动态可配置参数

```python
from langchain_core.runnables import ConfigurableField

model = ChatOpenAI(model="gpt-4o-mini", temperature=0).configurable_fields(
    temperature=ConfigurableField(id="temp"),
    model_name=ConfigurableField(id="model"),
)

chain = prompt | model | StrOutputParser()

# 运行时切配置
chain.with_config(configurable={"temp": 0.9, "model": "gpt-4o"}).invoke(...)
```

#### 整体备选 chain

```python
chain.configurable_alternatives(
    ConfigurableField(id="chain_kind"),
    default_key="qa",
    summarize=summary_chain,
).with_config(configurable={"chain_kind": "summarize"}).invoke(...)
```

适合多业务场景共用入口，前端传不同 `configurable` 切换流程。

---

## 4. 数据流类型转换

一条 LCEL chain 里，每一步的输入输出类型必须能对接：

```
{"x": 1}                  ChatPromptTemplate
  ─────▶ ChatPromptValue ────▶ ChatModel
                                  │
                                  ▼
                              AIMessage  StrOutputParser
                                  ─────▶ str
```

常见类型映射：

| 组件 | input | output |
|------|-------|--------|
| `ChatPromptTemplate` | `dict` | `ChatPromptValue` |
| `ChatModel` | `dict / str / list[BaseMessage] / ChatPromptValue` | `AIMessage` |
| `StrOutputParser` | `AIMessage / str` | `str` |
| `JsonOutputParser` | `AIMessage / str` | `dict` |
| `Retriever` | `str` | `List[Document]` |
| `RunnableLambda(fn)` | 任意 | 任意 |

如果两个组件不匹配，加 `RunnableLambda` / `RunnablePassthrough.assign` 适配即可。

---

## 5. 一个 RAG 例子彻底吃透 LCEL

我们要实现：

> 给一个问题，先从向量库检索文档，把文档和问题塞给 LLM 回答。

```python
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    ("system", "根据以下文档回答：\n{context}"),
    ("human", "{question}"),
])

def format_docs(docs):
    return "\n\n".join(d.page_content for d in docs)

# retriever.invoke("...") 返回 List[Document]
rag_chain = (
    {
        "context": retriever | format_docs,
        "question": RunnablePassthrough(),
    }
    | prompt
    | model
    | StrOutputParser()
)

rag_chain.invoke("LCEL 是什么？")
```

关键点：
- `{...}` 是 RunnableParallel，并行运行两个分支
- `"context": retriever | format_docs`：retriever 拿到 `"LCEL 是什么？"` 返回文档列表，然后 `format_docs` 拼成字符串
- `"question": RunnablePassthrough()`：把输入字符串直接放进字典
- 整个 `{}` 输出 `{"context": "...", "question": "LCEL 是什么？"}`，刚好对上 prompt 的变量

---

## 6. astream_events：细粒度事件流

`.stream()` 只能流 chain 最末端的 chunk。如果链里有多个 LLM 调用、retriever、tool，想监听每一步的实时事件：

```python
async for event in chain.astream_events({"...": "..."}, version="v2"):
    kind = event["event"]   # 如 on_chat_model_stream / on_chain_start / on_retriever_end
    name = event["name"]
    data = event["data"]
    if kind == "on_chat_model_stream":
        print(data["chunk"].content, end="")
    elif kind == "on_retriever_end":
        print(f"\n>>> 检索到 {len(data['output'])} 个文档")
```

事件类型一览：

```
on_chain_start / on_chain_stream / on_chain_end
on_chat_model_start / on_chat_model_stream / on_chat_model_end
on_llm_start / on_llm_stream / on_llm_end
on_tool_start / on_tool_end
on_retriever_start / on_retriever_end
on_prompt_start / on_prompt_end
```

这是构建 UI 流式应用（如 ChatGPT 那种 token-by-token + 显示"正在搜索..."状态）的核心 API。

---

## 7. config 的传递机制

`config` 在 LCEL chain 里**自动透传**到每个子 Runnable。比如：

```python
chain.invoke(x, config={"tags": ["session-1"], "callbacks": [my_handler]})
```

会传给 chain 里的每一个组件，无须显式转发。

`max_concurrency` 控制 `batch` 的并发：

```python
chain.batch(inputs, config={"max_concurrency": 10})
```

---

## 8. RunnableGenerator：流式自定义

有时你想在流里插入自己的转换：

```python
from typing import Iterator

def add_prefix(stream: Iterator[str]) -> Iterator[str]:
    for chunk in stream:
        yield "> " + chunk

chain = prompt | model | StrOutputParser() | RunnableLambda(add_prefix)
```

注意 `RunnableLambda` 在传入函数返回 generator 时会自动用作 streaming transform。

---

## 9. 调试 LCEL：get_graph / print_ascii

```python
chain.get_graph().print_ascii()
```

会输出类似：

```
              +---------------------------+
              | Parallel<context,question>|
              +---------------------------+
                          *
                          *
              +-----------------------+
              | ChatPromptTemplate    |
              +-----------------------+
                          *
                          *
              +-----------------------+
              | ChatOpenAI            |
              +-----------------------+
                          *
                          *
              +-----------------------+
              | StrOutputParser       |
              +-----------------------+
```

复杂链调试神器。

---

## 10. 何时**不**用 LCEL

LCEL 不是万能的，**复杂控制流（循环、状态机）建议直接用 LangGraph**。

| 场景 | 推荐 |
|------|------|
| 简单 RAG / 翻译 / 摘要 / 结构化提取 | LCEL |
| 顺序很清晰的多步流程 | LCEL |
| 需要 if/else 分支 | LCEL（RunnableBranch） |
| Agent / 循环 / 多 Agent / Human-in-loop | **LangGraph** |
| 需要持久化、断点续跑 | **LangGraph** |

官方文档已经把 `AgentExecutor` 标记为 legacy，新项目都建议用 LangGraph。

---

## 11. 综合 demo

```python
# demos/langchain/05_lcel.py
from dotenv import load_dotenv
from langchain_core.runnables import (
    RunnableParallel, RunnablePassthrough, RunnableLambda, RunnableBranch,
)
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

load_dotenv()
model = ChatOpenAI(model="gpt-4o-mini", temperature=0)

# ===== 并行：同时生成笑话和诗 =====
joke = ChatPromptTemplate.from_template("讲一个关于 {topic} 的短笑话") | model | StrOutputParser()
poem = ChatPromptTemplate.from_template("写一首关于 {topic} 的两行诗") | model | StrOutputParser()
both = RunnableParallel(joke=joke, poem=poem)

# ===== 注入额外字段 =====
enriched = RunnablePassthrough.assign(
    upper_topic=lambda x: x["topic"].upper(),
)

# ===== 条件分支 =====
classifier = ChatPromptTemplate.from_template(
    "给以下问题分类，只回复 'code' / 'general'：{q}"
) | model | StrOutputParser()

code_chain = ChatPromptTemplate.from_template("以代码方式回答：{q}") | model | StrOutputParser()
general_chain = ChatPromptTemplate.from_template("普通回答：{q}") | model | StrOutputParser()

router = (
    RunnablePassthrough.assign(kind=classifier)
    | RunnableBranch(
        (lambda x: "code" in x["kind"], code_chain),
        general_chain,
    )
)

print(both.invoke({"topic": "Python"}))
print(router.invoke({"q": "写一个冒泡排序"}))
print(router.invoke({"q": "讲讲春天"}))
```

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `dict` 不被自动并行 | 在链 `|` 外定义的 dict | 放进 `|` 链里，或显式 `RunnableParallel(...)` |
| 流式不流 | 链中间有非流式 Lambda | Lambda 返回 generator，或用 `RunnableGenerator` |
| `RunnableLambda` 报 NotIterable | 同步函数被当流式调用 | 显式实现 `__call__` 流式 |
| `config` 没传到子链 | 自己 invoke 时丢了 | 永远 `def fn(x, config): chain.invoke(x, config)` |
| 类型不匹配报错信息晦涩 | LCEL 不是静态类型 | 用 `chain.get_graph().print_ascii()` 看结构 |

---

## 13. 本章 demo

[`demos/langchain/05_lcel.py`](../../demos/langchain/05_lcel.py)

下一篇：[06-streaming.md](06-streaming.md) — 全方位 Streaming。
