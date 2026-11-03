# LangChain 03：Prompt Templates 提示模板

> 手拼字符串写 Prompt，项目一大就难以维护。Prompt Template 把提示词变成声明式的模板对象，支持变量插值、消息历史占位和 Few-shot 示例选择。在 LangChain 的各种抽象里，它的重要性仅次于 ChatModel。

---

## 1. 为什么不用 f-string

最直接的写法是这样：

```python
prompt = f"你是 {role}，回答问题：{question}"
model.invoke(prompt)
```

简单是简单，但字符串没法序列化，存不进 LangSmith Prompt Hub 或数据库；想在 system 后面插一段历史消息，拼接代码会很难看；而且 prompt 和调用绑死在一起，想单独测一下 prompt 也得跑一遍模型。

换成 Prompt Template：

```python
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}。"),
    ("human", "{question}"),
])

prompt.invoke({"role": "Python 老师", "question": "什么是装饰器？"})
```

返回的是 `ChatPromptValue`，可以直接传给 model，也可以用 `.to_messages()` 拆成消息列表。

---

## 2. 两类核心模板

| 模板 | 输入 | 输出 | 何时用 |
|------|------|------|--------|
| `PromptTemplate` | 变量 dict | `StringPromptValue` | LLM（旧补全模型） |
| `ChatPromptTemplate` | 变量 dict | `ChatPromptValue` | ChatModel（主流） |

现在的 LLM 基本都是 chat 模型，绝大多数场景用 `ChatPromptTemplate` 就对了。

---

## 3. ChatPromptTemplate 三种创建方式

### 3.1 元组列表（最常用）

```python
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}。"),
    ("human", "{question}"),
])
```

支持的 role 字符串：`"system"`、`"human"`（或 `"user"`）、`"ai"`（或 `"assistant"`）、`"placeholder"`。

### 3.2 显式 Message 类

```python
from langchain_core.prompts import SystemMessagePromptTemplate, HumanMessagePromptTemplate

prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template("你是 {role}。"),
    HumanMessagePromptTemplate.from_template("{question}"),
])
```

写起来啰嗦一些，但更直观，IDE 没有类型提示时比较好用。

### 3.3 from_template（单 human）

```python
prompt = ChatPromptTemplate.from_template("简短回答：{question}")
```

效果和只放一条 HumanMessage 一样。

---

## 4. 变量插值与 Partial

模板默认用 f-string 风格的 `{var}` 变量。如果只想先填一部分：

```python
from langchain_core.prompts import ChatPromptTemplate

base = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}，使用 {language} 回答。"),
    ("human", "{question}"),
])

# 预先填好一部分
zh_python_tutor = base.partial(role="Python 老师", language="中文")

# 后续只填剩余
zh_python_tutor.invoke({"question": "什么是 GIL？"})
```

`partial` 返回一个新模板，原模板不受影响。它也可以接收函数：

```python
from datetime import datetime

prompt = ChatPromptTemplate.from_messages([
    ("system", "当前时间：{now}"),
    ("human", "{question}"),
]).partial(now=lambda: datetime.now().isoformat())
```

函数在每次 invoke 时执行，所以时间每次都是新的。

---

## 5. MessagesPlaceholder：插入消息历史

聊天机器人几乎都要把历史对话放进 prompt：

```python
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一个友好的助手。"),
    MessagesPlaceholder("history"),
    ("human", "{question}"),
])

result = prompt.invoke({
    "history": [
        ("human", "我叫小明"),
        ("ai", "你好小明！"),
    ],
    "question": "我叫什么名字？",
})

for m in result.to_messages():
    print(type(m).__name__, m.content)
```

输出：

```
SystemMessage 你是一个友好的助手。
HumanMessage  我叫小明
AIMessage     你好小明！
HumanMessage  我叫什么名字？
```

简写形式：

```python
prompt = ChatPromptTemplate.from_messages([
    ("system", "..."),
    ("placeholder", "{history}"),
    ("human", "{question}"),
])
```

---

## 6. Few-shot Prompt

### 6.1 静态 Few-shot

把固定的几个例子拼进 prompt：

```python
from langchain_core.prompts import (
    ChatPromptTemplate, FewShotChatMessagePromptTemplate,
)

example_prompt = ChatPromptTemplate.from_messages([
    ("human", "{input}"),
    ("ai", "{output}"),
])

few_shot = FewShotChatMessagePromptTemplate(
    example_prompt=example_prompt,
    examples=[
        {"input": "2+2", "output": "4"},
        {"input": "3+5", "output": "8"},
    ],
)

final = ChatPromptTemplate.from_messages([
    ("system", "你是一个加法计算器，只回答数字。"),
    few_shot,
    ("human", "{input}"),
])

print(final.invoke({"input": "10+12"}).to_string())
```

### 6.2 动态 Few-shot（按相似度选）

```python
from langchain_core.example_selectors import SemanticSimilarityExampleSelector
from langchain_openai import OpenAIEmbeddings
from langchain_core.vectorstores import InMemoryVectorStore   # 或 langchain_chroma.Chroma / FAISS 等

examples = [
    {"input": "苹果", "output": "水果"},
    {"input": "土豆", "output": "蔬菜"},
    {"input": "鲨鱼", "output": "鱼类"},
    {"input": "金毛", "output": "犬类"},
    # ... 上百条
]

selector = SemanticSimilarityExampleSelector.from_examples(
    examples=examples,
    embeddings=OpenAIEmbeddings(),
    vectorstore_cls=InMemoryVectorStore,
    k=2,
)

few_shot = FewShotChatMessagePromptTemplate(
    example_prompt=ChatPromptTemplate.from_messages([
        ("human", "{input}"),
        ("ai", "{output}"),
    ]),
    example_selector=selector,
    input_variables=["input"],
)

final = ChatPromptTemplate.from_messages([
    ("system", "根据示例对输入进行分类。"),
    few_shot,
    ("human", "{input}"),
])

# 输入"哈士奇" → selector 自动找最像的"金毛"等做示例
final.invoke({"input": "哈士奇"}).to_messages()
```

可用的 Selector：
- `SemanticSimilarityExampleSelector`：向量相似
- `LengthBasedExampleSelector`：按长度截断（防止 prompt 超长）
- `MaxMarginalRelevanceExampleSelector`：多样性
- `NGramOverlapExampleSelector`：n-gram 重叠（在 `langchain_community.example_selectors`，需要 `pip install nltk`）

---

## 7. 自定义模板格式：Jinja2 / mustache

默认是 Python 的 f-string 格式。prompt 里如果要写大括号（比如放代码示例），切到 Jinja2 会省事很多：

```python
prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "你是一个老师"),
        ("human", "回答 {{question}}，输出 JSON: {\"answer\": ...}"),
    ],
    template_format="jinja2",
)
```

需要先 `pip install jinja2`（不是默认依赖）。`template_format` 对整个 `from_messages` 生效；mustache 用 `"mustache"`，写法同样是 `{{变量}}`。Jinja2 会执行模板里的表达式，所以用户输入只能作为变量传入，不能拿来当模板本身。

---

## 8. Prompt 序列化与 Hub

LangSmith 的 Prompt Hub 可以把 prompt 存到云端。1.x 里 `from langchain import hub` 已经搬走了（现在在 `langchain_classic.hub`），建议直接用 LangSmith SDK：

```python
from langsmith import Client

client = Client()

# 拉取（拉别人公开的 prompt 必须显式确认）
prompt = client.pull_prompt("rlm/rag-prompt", dangerously_pull_public_prompt=True)
print(prompt)

# 推送（需要 LANGSMITH_API_KEY）
client.push_prompt("my-handle/my-prompt", object=prompt)
```

也可以在本地序列化。老的 `prompt.save("x.yaml")` / `load_prompt` 在 `langchain-core 1.2.21` 起已被弃用（`ChatPromptTemplate.save` 甚至直接 `NotImplementedError`），改用 `langchain_core.load`：

```python
from langchain_core.load import dumps, loads

text = dumps(prompt, pretty=True)      # 得到 JSON 字符串，可写文件
prompt2 = loads(text)
```

Prompt Hub 在 LangSmith 04 篇里细讲。

---

## 9. PromptTemplate（字符串模板）

只有旧的补全模型才用，知道有这么个东西就行：

```python
from langchain_core.prompts import PromptTemplate

t = PromptTemplate.from_template("将以下文本翻译为英文：\n{text}")
print(t.invoke({"text": "你好世界"}))
# StringPromptValue(text='将以下文本翻译为英文：\n你好世界')
```

---

## 10. 进阶：在 prompt 里调用工具描述

以前模型不支持 tool calling 时，要用文本版 ReAct，在 system 里手写工具列表。现在的模型都原生支持 tool calling，一般直接用 `bind_tools` 或 `create_agent`。下面这段只作了解：

```python
from langchain_core.tools.render import render_text_description
from langchain_core.tools import tool

@tool
def calc(expr: str) -> str:
    """计算数学表达式。"""
    return str(eval(expr))

@tool
def search(q: str) -> str:
    """网络搜索。"""
    return "..."

tools_desc = render_text_description([calc, search])

prompt = ChatPromptTemplate.from_messages([
    ("system", "你可以使用以下工具：\n{tools}\n按 ReAct 格式回答。"),
    ("human", "{input}"),
]).partial(tools=tools_desc)
```

---

## 11. 与 Chain 协作

放进 chain 里：

```python
chain = prompt | model | parser
```

`chain.invoke(...)` 传入的字典 key 就是 prompt 的输入变量，模板加了变量，调用方也要跟着改。

想看 prompt 实际渲染出来是什么样：

```python
rendered = prompt.invoke({"role": "...", "question": "..."})
print(rendered.to_string())     # 字符串形式
print(rendered.to_messages())   # Message 列表
```

调试 prompt 时，我最常用的就是这两个方法。

---

## 12. 一个综合 demo

完整代码：[`demos/langchain/03_prompts.py`](../../demos/langchain/03_prompts.py)

```python
# demos/langchain/03_prompts.py
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, FewShotChatMessagePromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

load_dotenv()

# 1) Few-shot 示例
example_prompt = ChatPromptTemplate.from_messages([
    ("human", "{input}"),
    ("ai", "{output}"),
])
few_shot = FewShotChatMessagePromptTemplate(
    example_prompt=example_prompt,
    examples=[
        {"input": "2+2", "output": "4"},
        {"input": "3*4", "output": "12"},
    ],
)

# 2) 主 Prompt（含历史和示例）
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是数学计算器，只回答数字结果，无任何文字。"),
    few_shot,
    MessagesPlaceholder("history"),
    ("human", "{input}"),
])

chain = prompt | ChatOpenAI(model="gpt-6-luna", temperature=0) | StrOutputParser()

history = [
    ("human", "5*5"),
    ("ai", "25"),
]
print(chain.invoke({"input": "6*7", "history": history}))
```

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `KeyError: 'history'` | placeholder 的变量必须传，空的也要传 | 传 `{"history": []}` |
| `{` 转义出问题 | 默认按 f-string 解析占位符 | `template_format="jinja2"`，或写成 `{{` 转义 |
| 模型完全没看到新加的内容 | 只改了字符串拼接，没改 `from_messages` | 用 `prompt.invoke(...).to_string()` 检查 |
| 上线后 prompt 难改 | 写死在代码里 | 用 LangSmith Prompt Hub（`pull_prompt`）或配置中心 |
| `from langchain import hub` 报错 | 1.x 已移出 | 改用 `langsmith.Client().pull_prompt`，或 `from langchain_classic import hub` |
| `prompt.save()` 报 NotImplementedError / 弃用警告 | 1.2.21 起弃用 | 用 `langchain_core.load.dumps/loads` |

---

下一篇：[LangChain 04：Output Parsers 与结构化输出](04-output-parsers.md)
