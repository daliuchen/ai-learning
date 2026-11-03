# LangChain 05：LCEL 与 Runnable 体系

> LCEL 用管道符 `|` 把一个个 Runnable 串成数据流。LangChain 现在的 API 几乎都建在它上面，读懂了 LCEL，其他组件怎么拼在一起也就清楚了。这篇从 Runnable 协议讲起，再到几个组合原语、事件流和调试方法。

---

## 1. LCEL 是什么

LCEL 全称 LangChain Expression Language，是一套建立在 `Runnable` 协议上的组合 DSL。

写 LCEL 时只描述数据怎么流动，不用自己写循环和 await。所有组件都实现了 `invoke/batch/stream`，组合出来的链自然也有这些方法。每一步会自动生成 LangSmith trace，能并行的地方自动并行，流式也是自动打通的。

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

每个 LCEL 组件都要实现 `Runnable[Input, Output]`，核心方法如下：

```python
class Runnable(Generic[Input, Output]):
    def invoke(self, input: Input, config: Optional[RunnableConfig] = None) -> Output: ...
    async def ainvoke(self, input: Input, config: Optional[RunnableConfig] = None) -> Output: ...
    def batch(self, inputs: List[Input], ...) -> List[Output]: ...
    async def abatch(self, inputs: List[Input], ...) -> List[Output]: ...
    def stream(self, input: Input, ...) -> Iterator[Output]: ...
    async def astream(self, input: Input, ...) -> AsyncIterator[Output]: ...
```

只要实现了 `invoke`，其他方法都有默认实现：`batch` 默认用线程池并发调 `invoke`，`stream` 默认等 `invoke` 返回后一次性吐出整个结果。

`Runnable` 重载了 `|`（`__or__` / `__ror__`），`|` 另一侧的对象会被自动转换：

```python
a | b          →  RunnableSequence(a, b)
{"x": a} | b   →  RunnableSequence(RunnableParallel({"x": a}), b)
```

第二种容易被忽略：dict 字面量会被自动包成 `RunnableParallel`，里面的每个 value 并行执行。普通函数会被包成 `RunnableLambda`，生成器函数会被包成 `RunnableGenerator`。

---

## 3. LCEL 的组合原语

### 3.1 RunnableSequence：串联

```python
seq = a | b | c
```

等价于：

```python
from langchain_core.runnables import RunnableSequence
seq = RunnableSequence(a, b, c)
```

执行：`c.invoke(b.invoke(a.invoke(x)))`

### 3.2 RunnableParallel：并联

把同一个输入分给多个分支并行执行，结果合并成一个 dict：

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

dict 只有出现在 `|` 链里才会自动转成 RunnableParallel，单独定义时要显式写 `RunnableParallel({...})`。

### 3.3 RunnableLambda：把任意函数变成 Runnable

```python
from langchain_core.runnables import RunnableLambda

def shout(text: str) -> str:
    return text.upper() + "!!!"

shouter = RunnableLambda(shout)
print(shouter.invoke("hello"))  # HELLO!!!

# 等价的链
chain = prompt | model | StrOutputParser() | RunnableLambda(shout)
```

用 `@chain` 装饰器写起来更简洁：

```python
from langchain_core.runnables import chain as as_chain

@as_chain
def shout(text: str) -> str:
    return text.upper()
```

### 3.4 RunnablePassthrough：直通与注入

最常见的用法是把输入原样往下传，同时往字典里加新的 key：

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

### 3.5 RunnableBranch：条件分支

```python
from langchain_core.runnables import RunnableBranch

router = RunnableBranch(
    (lambda x: "?" in x["q"], faq_chain),         # 条件 + 分支
    (lambda x: x["q"].startswith("代码"), code_chain),
    default_chain,                                 # 兜底
)

router.invoke({"q": "什么是 LCEL？"})
```

也可以用 `RunnableLambda` 写一个更灵活的路由：

```python
def route(info):
    if "代码" in info["topic"]:
        return code_chain
    return general_chain

chain = first_step | route   # route 返回的 Runnable 会被 invoke
```

函数返回的 Runnable 会被自动 invoke，所以路由函数只管挑出下一步是谁就行。

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

model = ChatOpenAI(model="gpt-6-luna", temperature=0).configurable_fields(
    temperature=ConfigurableField(id="temp"),
    model_name=ConfigurableField(id="model"),
)

chain = prompt | model | StrOutputParser()

# 运行时切配置
chain.with_config(configurable={"temp": 0.9, "model": "gpt-6.1-sol"}).invoke(...)
```

#### 整体备选 chain

```python
chain.configurable_alternatives(
    ConfigurableField(id="chain_kind"),
    default_key="qa",
    summarize=summary_chain,
).with_config(configurable={"chain_kind": "summarize"}).invoke(...)
```

多个业务场景共用一个入口时可以这样做，前端传不同的 `configurable` 就能切换流程。

---

## 4. 数据流类型转换

一条 LCEL chain 里，上一步的输出类型必须是下一步能接受的输入：

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
| `ChatModel` | `str / list[BaseMessage] / ChatPromptValue` | `AIMessage` |
| `StrOutputParser` | `AIMessage / str` | `str` |
| `JsonOutputParser` | `AIMessage / str` | `dict` |
| `Retriever` | `str` | `List[Document]` |
| `RunnableLambda(fn)` | 任意 | 任意 |

两个组件对不上时，中间加一个 `RunnableLambda` 或 `RunnablePassthrough.assign` 做转换。

---

## 5. 用一个 RAG 例子串起来

需求是：给一个问题，先从向量库检索文档，再把文档和问题一起交给 LLM 回答。

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

拆开看：`{...}` 是一个 RunnableParallel，两个分支并行执行。`"context"` 分支里，retriever 拿到 `"LCEL 是什么？"` 返回文档列表，再由 `format_docs` 拼成字符串；`"question"` 分支用 `RunnablePassthrough()` 把输入字符串原样放进字典。整个 `{}` 的输出是 `{"context": "...", "question": "LCEL 是什么？"}`，正好对上 prompt 里的两个变量。

---

## 6. astream_events：细粒度事件流

`.stream()` 只能拿到 chain 最后一步的 chunk。链里有多个 LLM 调用、retriever、tool 时，如果想实时看到每一步的事件：

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

做 ChatGPT 那种界面时要用到它：既要逐 token 输出，又要显示「正在搜索...」这类中间状态。

---

## 7. config 的传递机制

`config` 在 LCEL chain 里会自动传给每个子 Runnable。比如：

```python
chain.invoke(x, config={"tags": ["session-1"], "callbacks": [my_handler]})
```

这份配置会传到 chain 里的每个组件，不用手动转发。

`batch` 的并发数由 `max_concurrency` 控制：

```python
chain.batch(inputs, config={"max_concurrency": 10})
```

---

## 8. RunnableGenerator：流式自定义

有时候想在流的中间插一步自己的转换：

```python
from typing import Iterator

def add_prefix(stream: Iterator[str]) -> Iterator[str]:
    for chunk in stream:
        yield "> " + chunk

chain = prompt | model | StrOutputParser() | add_prefix   # 生成器函数在 | 链里自动包成 RunnableGenerator
```

直接把生成器函数放进 `|` 链，LangChain 会把它包成 `RunnableGenerator`，`add_prefix` 拿到的是上游的 chunk 流，逐个处理。想把意图写明白，也可以显式写：

```python
from langchain_core.runnables import RunnableGenerator

chain = prompt | model | StrOutputParser() | RunnableGenerator(add_prefix)
```

不要写成 `RunnableLambda(add_prefix)`：`RunnableLambda(f)` 会先把上游输出攒成完整的值再交给 `f`，这里 `f` 拿到的是整段字符串，`for chunk in stream` 变成了逐字符遍历，每个字符前都会加上 `"> "`。

---

## 9. 调试 LCEL：get_graph / print_ascii

```python
chain.get_graph().print_ascii()      # 需要 pip install grandalf
```

输出大致是这样：

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

链一复杂，靠它看结构比读代码快得多。

---

## 10. 什么时候不用 LCEL

遇到循环、状态机这类复杂控制流，**直接用 LangGraph**。LCEL 适合的是流程固定的场景。

| 场景 | 推荐 |
|------|------|
| 简单 RAG / 翻译 / 摘要 / 结构化提取 | LCEL |
| 顺序很清晰的多步流程 | LCEL |
| 需要 if/else 分支 | LCEL（RunnableBranch） |
| Agent / 循环 / 多 Agent / Human-in-loop | LangGraph |
| 需要持久化、断点续跑 | LangGraph |

`AgentExecutor` 已经搬进 `langchain-classic`，新项目的 agent 一律用 `langchain.agents.create_agent`（底层就是 LangGraph）。LCEL 本身并没有被淘汰，在 1.x 里写固定流程，`prompt | model | parser` 仍然是最短的写法。

---

## 11. 综合 demo

完整代码：[`demos/langchain/05_lcel.py`](../../demos/langchain/05_lcel.py)

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
model = ChatOpenAI(model="gpt-6-luna", temperature=0)

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
| `dict` 没有自动并行 | dict 定义在 `|` 链外面 | 放进 `|` 链里，或显式 `RunnableParallel(...)` |
| 流式没生效 | 链中间有不支持流式的 `RunnableLambda` | 改成「迭代器进、生成器出」的函数，用 `RunnableGenerator` 包装 |
| 想逐 chunk 处理，结果被攒成了整段 | 给 `RunnableLambda` 传的是普通函数 | 改用 `RunnableGenerator`，函数签名是迭代器进、生成器出 |
| `config` 没传到子链 | 手动 invoke 时没带上 | 一律写成 `def fn(x, config): chain.invoke(x, config)` |
| 类型不匹配，报错看不懂 | LCEL 没有静态类型检查 | 用 `chain.get_graph().print_ascii()` 看结构（需装 `grandalf`） |

---

下一篇讲流式：[LangChain 06：Streaming 流式输出](06-streaming.md)
