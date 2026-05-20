# LangChain 03：Prompt Templates 提示模板

> **一句话**：Prompt Template 把"字符串拼接"升级为"声明式模板对象"，支持变量插值、消息历史占位、Few-shot 选择，是 LangChain 里继 ChatModel 之后第二重要的抽象。

---

## 1. 为什么不用 f-string

直觉上你可能想这样：

```python
prompt = f"你是 {role}，回答问题：{question}"
model.invoke(prompt)
```

简单但有三个缺陷：

1. **不可序列化**：没法存到 LangSmith Prompt Hub / 数据库
2. **难以局部修改**：要在 system 后插一段历史消息？字符串拼接很丑
3. **无法和 Chain 解耦**：测试 prompt 必须跑模型

Prompt Template 解决这些问题：

```python
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}。"),
    ("human", "{question}"),
])

prompt.invoke({"role": "Python 老师", "question": "什么是装饰器？"})
```

返回 `ChatPromptValue`，能直接喂给 model，或 `.to_messages()` 拆出消息列表。

---

## 2. 两类核心模板

| 模板 | 输入 | 输出 | 何时用 |
|------|------|------|--------|
| `PromptTemplate` | str | `StringPromptValue` | LLM（旧补全模型） |
| `ChatPromptTemplate` | str | `ChatPromptValue` | ChatModel（**主流**） |

现代 LLM 几乎都是 chat 模型，**99% 场景用 `ChatPromptTemplate`**。

---

## 3. ChatPromptTemplate 三种创建方式

### 3.1 元组列表（最常用）

```python
prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}。"),
    ("human", "{question}"),
])
```

支持的 role 字符串：`"system" | "human" | "ai" | "tool" | "placeholder"`。

### 3.2 显式 Message 类

```python
from langchain_core.prompts import SystemMessagePromptTemplate, HumanMessagePromptTemplate

prompt = ChatPromptTemplate.from_messages([
    SystemMessagePromptTemplate.from_template("你是 {role}。"),
    HumanMessagePromptTemplate.from_template("{question}"),
])
```

啰嗦但更直观，适合 IDE 没 type hint 时。

### 3.3 from_template（单 human）

```python
prompt = ChatPromptTemplate.from_template("简短回答：{question}")
```

等价于一条 HumanMessage。

---

## 4. 变量插值与 Partial

模板默认用 `{var}` 风格的 f-string 变量。如果想要部分填充：

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

`partial` 返回新模板，原模板不变。

`partial` 还支持回调函数：

```python
from datetime import datetime

prompt = ChatPromptTemplate.from_messages([
    ("system", "当前时间：{now}"),
    ("human", "{question}"),
]).partial(now=lambda: datetime.now().isoformat())
```

每次 invoke 时回调被执行，时间会刷新。

---

## 5. MessagesPlaceholder：插入消息历史

聊天机器人最常见需求：把历史对话塞进 prompt：

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

固定几个例子拼到 prompt：

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
from langchain_community.vectorstores import Chroma

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
    vectorstore_cls=Chroma,
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

可选 Selector：
- `SemanticSimilarityExampleSelector`：向量相似
- `LengthBasedExampleSelector`：按长度截断（防止 prompt 超长）
- `MaxMarginalRelevanceExampleSelector`：多样性
- `NGramOverlapExampleSelector`：n-gram 重叠

---

## 7. 自定义模板格式：Jinja2 / mustache

默认是 Python 的 f-string，如果你的 prompt 里要写大括号（如代码示例），切到 Jinja2 更友好：

```python
prompt = ChatPromptTemplate.from_messages(
    [
        ("system", "你是一个老师"),
        ("human", "回答 {{question}}，输出 JSON: {\"answer\": ...}"),
    ],
    template_format="jinja2",
)
```

注意只有 `from_template` 支持 mustache/jinja2，元组列表里逐条传入时需要指定 `template_format`。

---

## 8. Prompt 序列化与 Hub

LangChain 内置 LangSmith Prompt Hub，可以把 prompt 推到云端：

```python
# 拉取
from langchain import hub
prompt = hub.pull("rlm/rag-prompt")
print(prompt)

# 推送
hub.push("my-handle/my-prompt", prompt)
```

或者本地序列化：

```python
prompt.save("my_prompt.yaml")

from langchain_core.prompts import load_prompt
prompt2 = load_prompt("my_prompt.yaml")
```

第 18 篇会专门讲 Prompt Hub。

---

## 9. PromptTemplate（字符串模板）

旧的补全模型才用，了解即可：

```python
from langchain_core.prompts import PromptTemplate

t = PromptTemplate.from_template("将以下文本翻译为英文：\n{text}")
print(t.invoke({"text": "你好世界"}))
# StringPromptValue(text='将以下文本翻译为英文：\n你好世界')
```

---

## 10. 进阶：在 prompt 里调用工具描述

ReAct 类 Prompt 经常需要在 system 里列出工具列表：

```python
from langchain.tools.render import render_text_description
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

完整 chain：

```python
chain = prompt | model | parser
```

`prompt` 的输入变量 = `chain.invoke(...)` 字典的 keys。所以扩展模板变量时记得同步更新调用方。

如果你想看 prompt 真实渲染结果：

```python
rendered = prompt.invoke({"role": "...", "question": "..."})
print(rendered.to_string())     # 字符串形式
print(rendered.to_messages())   # Message 列表
```

调试 prompt 时这是最有用的两个方法。

---

## 12. 一个综合 demo

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

chain = prompt | ChatOpenAI(model="gpt-4o-mini", temperature=0) | StrOutputParser()

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
| `KeyError: 'history'` | placeholder 必须提供变量，即使为空 | 传 `{"history": []}` |
| `{` 转义出问题 | f-string 默认占位 | `template_format="jinja2"` 或 `{{` 转义 |
| 模型完全没看到你新加的内容 | 忘了改 `from_messages`，只改了字符串拼接 | 用 `prompt.invoke(...).to_string()` 检查 |
| 上线后 prompt 难改 | 写死在代码里 | 用 LangSmith Hub 或 YAML 加载 |

---

## 14. 本章 demo

[`demos/langchain/03_prompts.py`](../../demos/langchain/03_prompts.py)

下一篇：[04-output-parsers.md](04-output-parsers.md)
