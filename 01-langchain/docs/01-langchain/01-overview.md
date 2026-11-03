# LangChain 01：整体架构与生态全景

> LangChain 是一套框架，不只是一个库。它把 LLM 应用拆成模型、提示、工具、记忆、检索、编排六类标准组件，组件之间接口统一，几十行代码就能搭出一个能上线的应用。这一篇先把整个生态的包结构和几层关系理清楚，后面各篇再逐块展开。

---

## 1. 为什么需要 LangChain

直接调 OpenAI / Anthropic SDK 也能写 LLM 应用，那为什么还要 LangChain？

放到实际项目里看，只用 SDK 的话，下面这五件事迟早都得自己做一遍：

1. 多模型切换：今天用 GPT-6，明天评估 Claude，后天加 DeepSeek，每家的调用方式、参数格式、消息格式都不一样
2. 提示词管理：Prompt 散落在代码各处，多语言、多版本、A/B 实验都很难维护
3. 结构化输出：模型经常输出不合法的 JSON 或者缺字段，需要重试、校验、修复
4. 工具调用：各家 Function Calling 格式不同，错误处理、并行调用、多轮调用都要自己写
5. 检索增强（RAG）：从加载文档、切块、向量化、检索到拼 Prompt，至少要拼十来个组件

LangChain 给这些问题提供了统一接口，再配上大量适配器：

```
LangChain = 标准抽象 + 上百个 Provider 实现 + 一套统一编排语法（LCEL）
```

---

## 2. LangChain 的生态全景

很多人以为 LangChain 是一个包，其实它早就拆成了多个独立的包。2025 年 10 月发布 1.0 之后，`langchain` 主包有意做了瘦身，只保留构建 agent 需要的部分，旧的 chains / retrievers / hub 都搬进了 `langchain-classic`：

```
langchain-core      ← 核心抽象（Runnable / Message / Document / Prompt 等）
   ↑ 被所有其他包依赖
   │
   ├── langchain                ← 1.x：create_agent、middleware、messages、tools、init_chat_model、init_embeddings
   │                              （底层依赖 langgraph 来跑 agent 循环）
   ├── langchain-classic        ← 旧的 chains / AgentExecutor / retrievers / hub / indexing（遗留代码用）
   ├── langchain-text-splitters ← 文本切分
   ├── langchain-community      ← 社区集成（loader、检索器、向量库等）
   ├── langchain-experimental   ← 实验性功能
   │
   ├── langchain-openai         ← OpenAI 专属适配器（也用来接一切 OpenAI 兼容接口，如千问、DeepSeek）
   ├── langchain-anthropic      ← Anthropic 专属
   ├── langchain-google-genai   ← Google Gemini
   ├── langchain-ollama         ← Ollama 本地模型
   └── ...（数十个 partner 包）
```

> 版本：全书以 `langchain 1.4`、`langgraph 1.2`、`langsmith 0.14` 为准。1.0 有两个大变化：
> 1. 构建 agent 的标准入口是 `from langchain.agents import create_agent`，原来的 `AgentExecutor`、`langgraph.prebuilt.create_react_agent` 都不再推荐；
> 2. 旧 API（`ConversationChain`、`create_retrieval_chain`、`langchain.hub`、`EnsembleRetriever` 等）只存在于 `langchain-classic`，导入路径从 `langchain.xxx` 变成 `langchain_classic.xxx`。
>
> 全书最重要的 LCEL / Runnable（`prompt | model | parser`）还在 `langchain-core` 里，1.0 没有改动，可以放心学。

LangChain 之外还有几个配套项目：

| 项目 | 定位 | 关系 |
|------|------|------|
| LangChain | LLM 应用框架：模型、提示、工具、检索、`create_agent`（本书 LangChain 01-14 篇） | 主体 |
| LangGraph | 底层的有状态编排运行时：持久化、人在回路、流式（本书 LangGraph 01-12 篇） | LangChain 的 agent 跑在它上面，也可以单独用 |
| LangSmith | 可观测性、评估、Prompt 管理、部署（本书 LangSmith 01-05 篇） | 平台服务，配合上面两个用 |
| Deep Agents | 建在 LangChain/LangGraph 之上、开箱即用的 agent harness（规划、文件系统、子 agent） | 想要现成的长任务 agent 时用，本书只在 LangChain 08 篇提一下 |

1.0 之后这几层的关系是：LangGraph 是运行时，LangChain 的 `create_agent` 是建在它上面的高层 agent 抽象（返回的就是一张 LangGraph 编译图），Deep Agents 再往上包一层 harness。用 `create_agent` 时拿到的持久化（checkpointer）、人在回路（interrupt）、流式，其实都是 LangGraph 提供的。需要手写复杂控制流时，再直接下到 LangGraph 的 `StateGraph`。

---

## 3. 六块核心组件

LangChain 把 LLM 应用抽象成六块，每一块对应一组核心接口。

### 3.1 Models（模型层）

```python
from langchain_core.language_models import BaseChatModel
```

模型分两种：
- `BaseChatModel`：对话模型，输入 `List[BaseMessage]`，输出 `AIMessage`，现在的主流
- `BaseLLM`：补全模型，输入 `str`，输出 `str`，已经逐渐被对话模型取代

实际项目里几乎只用 `ChatModel`。

### 3.2 Messages（消息层）

```python
from langchain_core.messages import (
    HumanMessage, AIMessage, SystemMessage, ToolMessage, FunctionMessage
)
```

LangChain 用 `Message` 对象表示对话里的每一条消息，不用再手拼 `role: user, content: ...` 这样的字典。

### 3.3 Prompts（提示层）

```python
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
```

Prompt 写成模板对象，支持变量插值、Few-shot 和消息历史占位。

### 3.4 Output Parsers（输出解析）

```python
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
```

把模型返回的 `AIMessage` 转成业务需要的格式：字符串、JSON 或 Pydantic 对象。

### 3.5 Tools（工具）

```python
from langchain_core.tools import tool
```

把任意 Python 函数包装成 LLM 能调用的工具，JSON Schema 自动生成。

### 3.6 Retrievers（检索器）

```python
from langchain_core.retrievers import BaseRetriever
```

对「给一个 query，返回相关 Document 列表」这件事的统一抽象。向量检索、全文检索、混合检索都是它的实现。

---

## 4. 把组件串起来的 LCEL

六块组件是原料，LCEL（LangChain Expression Language）负责把它们粘起来。

LCEL 围绕一个统一接口 `Runnable`。所有组件都实现了 `Runnable`，所以能用管道符 `|` 串起来：

```python
chain = prompt | model | parser
```

任何 `Runnable` 都自带这几种调用方式：
- `.invoke(input)` 同步执行
- `.ainvoke(input)` 异步执行
- `.batch([input1, input2])` 批量执行
- `.stream(input)` 流式
- `.astream_events(...)` 事件流（细粒度流式）

所以写完 `prompt | model | parser`，同步、异步、批量、流式四种执行方式就都有了，不用每种各写一遍。在我看来 LCEL 是 LangChain 里最值得花时间学的抽象，第 5 篇会专门讲。

---

## 5. Hello World

先跑一个最小例子，确认环境装好了。

### 5.1 一条最小的链

```python
# demos/langchain/01_hello_lcel.py
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一位 {role}，请用 {style} 的语气回答。"),
    ("human", "{question}"),
])

model = ChatOpenAI(model="gpt-6-luna", temperature=0.3)   # 换成千问等 OpenAI 兼容模型：设环境变量 OPENAI_BASE_URL，再改 model 名即可
parser = StrOutputParser()

chain = prompt | model | parser

result = chain.invoke({
    "role": "Python 资深工程师",
    "style": "简洁",
    "question": "Python 的 GIL 是什么？一句话讲清楚。",
})

print(result)
```

这段代码里有三点值得留意：
1. `prompt`、`model`、`parser` 是三种完全不同的对象，却都能用 `|` 串起来
2. 传给 `chain.invoke()` 的字典，key 必须能填满 prompt 里的所有变量
3. 输出已经是字符串，不是 `AIMessage`，因为最后一步的 parser 做了转换

### 5.2 一个最小 Agent（1.x 的新入口）

链的流程是你写死的，agent 则让模型自己决定调哪个工具。1.x 里一个函数就够了：

```python
from langchain.agents import create_agent
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """查询城市天气。"""
    return f"{city} 晴 25℃"

agent = create_agent(
    "openai:gpt-6-luna",              # 也可以直接传一个 ChatOpenAI(...) 对象
    tools=[get_weather],
    system_prompt="你是简洁的天气助手。",
)

out = agent.invoke({"messages": [{"role": "user", "content": "北京天气怎么样？"}]})
print(out["messages"][-1].content)
```

`create_agent` 返回一张 LangGraph 编译图，所以 `invoke / stream / checkpointer / interrupt` 都能直接用。第 08 篇会拿它和旧的 `AgentExecutor` 做对照。

---

## 6. `|` 背后发生了什么

第一次看到 `prompt | model | parser` 时，很多人会疑惑：三种不同的对象，怎么能用同一个 `|` 串起来？答案在 `Runnable.__or__`：

```python
# langchain_core.runnables.base.Runnable（简化）
class Runnable:
    def __or__(self, other):
        return RunnableSequence(first=self, last=other)

    def invoke(self, input):
        raise NotImplementedError
```

每次 `a | b` 都返回一个新的 `RunnableSequence`，它的 `invoke` 是这样实现的：

```python
def invoke(self, input):
    output = self.first.invoke(input)
    output = self.last.invoke(output)
    return output
```

LCEL 就是函数组合（Function Composition）的语法糖。只要每个组件实现了 `invoke(input) -> output`，就能接起来。

---

## 7. 一个 RAG 应用的数据流

拿最常见的 RAG 问答应用举例，数据是这样流动的：

```
            ┌──────────────────────────────────────────────────────┐
            │              用户问题 "GIL 是什么？"                  │
            └──────────────────────────────────────────────────────┘
                                  │
                                  ▼
                          ┌───────────────┐
                          │  Retriever    │  ← 从向量库取相关文档
                          └───────────────┘
                                  │
                                  ▼
                  ┌───────────────────────────┐
                  │   PromptTemplate          │  ← 拼"基于以下文档回答..."
                  │   {context} + {question}  │
                  └───────────────────────────┘
                                  │
                                  ▼
                          ┌───────────────┐
                          │   ChatModel   │  ← GPT-6 / Claude / ...
                          └───────────────┘
                                  │
                                  ▼
                          ┌───────────────┐
                          │ OutputParser  │  ← 转字符串/JSON/Pydantic
                          └───────────────┘
                                  │
                                  ▼
                            最终答案
```

用 LCEL 写出来只有一条表达式：

```python
chain = (
    {"context": retriever, "question": RunnablePassthrough()}
    | prompt
    | model
    | StrOutputParser()
)
```

第 13 篇的 RAG 实战会把它展开。

---

## 8. 安装与版本对齐

LangChain 从 1.0 开始遵循语义化版本，`1.x` 之内保证 API 稳定，不会像 0.x 时那样小版本也破坏兼容。不要再装 `langchain<1.0`，另外老教程里的 `from langchain.chains import ...` 在新版里会直接报 `ModuleNotFoundError`。

```bash
pip install \
  "langchain>=1.4" \
  "langchain-openai>=1.7" \
  "langchain-community>=0.4" \
  "langgraph>=1.2" \
  "langsmith>=0.14"

# 需要旧 API（AgentExecutor / chains / hub / EnsembleRetriever 等）时再装
pip install langchain-classic
```

Python 版本要求 ≥ 3.10。验证安装：

```python
from importlib.metadata import version
for pkg in ["langchain", "langchain-core", "langgraph", "langsmith"]:
    print(pkg, version(pkg))
```

> 不是每个包都提供 `__version__` 属性，统一用 `importlib.metadata.version` 查最省事。

---

## 9. 常见误区

| 误区 | 实际情况 |
|------|------|
| LangChain 太重，自己撸更轻 | 业务一复杂，你多半会重新造一遍类似的东西，而且更糙 |
| LangChain 把简单事情复杂化 | 简单的事 `prompt \| model \| parser` 三行就够，复杂场景才需要高级 API |
| LangChain 锁定供应商 | 正好相反，它最大的用处就是屏蔽供应商差异 |
| 用 LangChain 就要用 LangGraph | 简单链（`prompt \| model \| parser`）用不到；不过 `create_agent` 底层就是 LangGraph，用到 checkpointer / interrupt 时自然会接触 |
| LangChain 1.0 把旧代码全删了 | 没删，搬到了 `langchain-classic`，装上它并把 `langchain.xxx` 改成 `langchain_classic.xxx` 即可 |
| LangChain 性能差 | 绝大部分耗时在 LLM 调用本身，框架开销通常可以忽略 |

---

## 10. 本章 demo

完整代码：[`demos/langchain/01_hello_lcel.py`](../../demos/langchain/01_hello_lcel.py)

下一篇讲 Chat Models：[LangChain 02：Chat Models 与 Messages](02-chat-models.md)
