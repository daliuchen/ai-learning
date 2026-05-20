# LangChain 01：整体架构与生态全景

> **一句话**：LangChain 不是一个库，而是一套把 LLM 应用拆成"模型 / 提示 / 工具 / 记忆 / 检索 / 编排"六个标准积木的框架，让你 30 行代码就能搭出生产可用的 LLM 应用。

---

## 1. 为什么需要 LangChain

直接调 OpenAI / Anthropic SDK 也能写 LLM 应用，那为什么还要 LangChain？

把这个问题代入实际项目你会发现，**裸调 SDK 会反复造五个轮子**：

1. **多模型切换**：今天用 GPT-4，明天评估 Claude，后天加 DeepSeek，每次 SDK 调用、参数格式、消息格式都不一样
2. **提示词管理**：Prompt 散落在代码各处，多语言、多版本、A/B 实验难以维护
3. **结构化输出**：模型经常输出非 JSON 或字段缺失，需要重试 + 校验 + 修复
4. **工具调用**：Function Calling 各家格式不同，错误处理、并行调用、多轮调用要自己实现
5. **检索增强（RAG）**：从文档加载、切块、向量化、检索到拼 Prompt，至少 10 个组件要拼装

LangChain 把上述问题做成了**标准接口 + 大量适配器**：

```
LangChain = 标准抽象 + 上百个 Provider 实现 + 一套统一编排语法（LCEL）
```

---

## 2. LangChain 的生态全景

很多人把"LangChain"当成一个包，实际上 2024 年后官方把它拆成了**多个独立包**：

```
langchain-core      ← 核心抽象（Runnable / Message / Document / Prompt 等）
   ↑ 被所有其他包依赖
   │
   ├── langchain                ← 业务相关链、Agent、Retriever
   ├── langchain-community      ← 社区集成（中等质量、活跃）
   ├── langchain-experimental   ← 实验性功能
   │
   ├── langchain-openai         ← OpenAI 专属适配器
   ├── langchain-anthropic      ← Anthropic 专属
   ├── langchain-google-genai   ← Google Gemini
   ├── langchain-deepseek       ← DeepSeek
   ├── langchain-ollama         ← Ollama 本地模型
   └── ...（数十个 partner 包）
```

除此之外还有三个"姐妹项目"：

| 项目 | 定位 | 关系 |
|------|------|------|
| **LangChain** | LLM 应用框架（本系列 1-14 章） | 主体 |
| **LangGraph** | 复杂状态机/Agent 编排（本系列 15-26 章） | 上层框架，可独立用 |
| **LangSmith** | 可观测性 + Eval + Prompt 管理（本系列 15-19 章） | 平台服务，配合上面两个用 |

**关键点**：LangGraph 与 LangChain 现在是平级关系，LangGraph 不依赖 langchain 主包，只依赖 langchain-core。换句话说你**可以只用 LangGraph 而不用 LangChain**，但**用 LangChain 写复杂 Agent 时官方推荐你切到 LangGraph**。

---

## 3. 六块核心积木

LangChain 把 LLM 应用抽象成六块积木，每一块都对应一组核心接口：

### 3.1 Models（模型层）

```python
from langchain_core.language_models import BaseChatModel
```

两种主要模型：
- `BaseChatModel`：对话模型，输入 `List[BaseMessage]`，输出 `AIMessage`（**当前主流**）
- `BaseLLM`：补全模型，输入 `str`，输出 `str`（已逐渐被对话模型取代）

实际项目几乎只用 `ChatModel`。

### 3.2 Messages（消息层）

```python
from langchain_core.messages import (
    HumanMessage, AIMessage, SystemMessage, ToolMessage, FunctionMessage
)
```

LangChain 用 `Message` 对象统一表达对话片段，不再有"openai role:user content:..." 这种字典字符串。

### 3.3 Prompts（提示层）

```python
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate
```

把 Prompt 写成模板对象，支持变量插值、Few-shot、消息历史占位。

### 3.4 Output Parsers（输出解析）

```python
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
```

把模型输出从 `AIMessage` 转换成业务需要的格式（字符串/JSON/Pydantic 对象）。

### 3.5 Tools（工具）

```python
from langchain_core.tools import tool
```

把任意 Python 函数包装成 LLM 可以调用的工具，自动生成 JSON Schema。

### 3.6 Retrievers（检索器）

```python
from langchain_core.retrievers import BaseRetriever
```

统一封装"给一个 query，返回相关 Document 列表"的抽象，向量库/全文/混合检索都是它的实现。

---

## 4. LangChain 的灵魂：LCEL

如果说六块积木是"原材料"，那么 LCEL (LangChain Expression Language) 就是"胶水"。

LCEL 的核心是一个统一接口 `Runnable`，所有积木都实现了 `Runnable`，因此可以用 `|` 管道符串起来：

```python
chain = prompt | model | parser
```

任何 `Runnable` 都自动具备：
- `.invoke(input)` 同步执行
- `.ainvoke(input)` 异步执行
- `.batch([input1, input2])` 批量执行
- `.stream(input)` 流式
- `.astream_events(...)` 事件流（细粒度流式）

这意味着你写 `prompt | model | parser`，就**自动得到了同步/异步/批量/流式四种执行能力**，不用为每种 case 写一遍代码。LCEL 是 LangChain 最值得学的一个抽象，第 5 篇会专门展开。

---

## 5. Hello World

下面用 30 秒跑通一个最小例子，验证安装是否正常。

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

model = ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
parser = StrOutputParser()

chain = prompt | model | parser

result = chain.invoke({
    "role": "Python 资深工程师",
    "style": "简洁",
    "question": "Python 的 GIL 是什么？一句话讲清楚。",
})

print(result)
```

**关键观察**：
1. `prompt`、`model`、`parser` 是三种完全不同的对象，但都能用 `|` 串起来
2. `chain.invoke()` 接收的字典 key 必须能填满 prompt 的所有变量
3. 输出已经是字符串而不是 `AIMessage`，因为最后一个 parser 把它转换了

---

## 6. LangChain 是怎么"魔术"地把它们串起来的

很多人初次看到 `prompt | model | parser` 会很懵：三种不同对象怎么可能用同一个 `|` 串起来？秘密在 `Runnable.__or__`：

```python
# langchain_core.runnables.base.Runnable（简化）
class Runnable:
    def __or__(self, other):
        return RunnableSequence(first=self, last=other)

    def invoke(self, input):
        raise NotImplementedError
```

每次 `a | b` 都返回一个新的 `RunnableSequence`，它的 `invoke` 实现就是：

```python
def invoke(self, input):
    output = self.first.invoke(input)
    output = self.last.invoke(output)
    return output
```

也就是说 LCEL 本质上是**函数组合**（Function Composition）的语法糖。只要每个组件实现了 `invoke(input) -> output`，就能链起来。

理解这一点后，你会发现 LangChain 不是"框架"那种重量级东西，本质上是个**约定+组合工具**。

---

## 7. 一张图：典型应用的数据流

下面是一个 RAG 问答应用的数据流，看一遍你就能掌握 LangChain 的全貌：

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
                          │   ChatModel   │  ← GPT-4 / Claude / ...
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

这条数据流在 LCEL 里就一行：

```python
chain = (
    {"context": retriever, "question": RunnablePassthrough()}
    | prompt
    | model
    | StrOutputParser()
)
```

第 13 篇 RAG 实战会详细展开。

---

## 8. 安装与版本对齐

LangChain 自 0.1 起进入"版本严格管理"阶段，**强烈不要**装 `langchain<0.1`，老 API 已经全部废弃。

```bash
pip install \
  "langchain>=0.3.0" \
  "langchain-openai>=0.2.0" \
  "langchain-community>=0.3.0" \
  "langgraph>=0.2.0" \
  "langsmith>=0.1.0"
```

验证安装：

```python
import langchain, langchain_core, langgraph
print(langchain.__version__, langchain_core.__version__, langgraph.__version__)
```

---

## 9. 常见误区

| 误区 | 真相 |
|------|------|
| LangChain 太重，自己撸更轻 | 业务复杂度上来后你会重新发明它，且写得更糙 |
| LangChain 把简单事情复杂化 | 简单事情用 `prompt \| model \| parser` 三行即可，复杂事情才用高级 API |
| LangChain 锁定供应商 | 恰恰相反，LangChain 的核心价值就是抽象掉供应商差异 |
| 用 LangChain 就要用 LangGraph | LangGraph 是上层选项，简单链不需要 |
| LangChain 性能差 | 性能瓶颈 99% 在 LLM 调用本身，框架开销可忽略 |

---

## 10. 本章 demo

完整可运行代码：[`demos/langchain/01_hello_lcel.py`](../../demos/langchain/01_hello_lcel.py)

跑通后下一章开始系统讲 Chat Models：[02-chat-models.md](02-chat-models.md)
