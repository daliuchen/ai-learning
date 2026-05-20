# LangSmith 04：Prompt Hub 与 Playground

> **一句话**：Prompt Hub 让 prompt 像代码一样有版本、有 owner、能在 UI 调试、能 A/B、能从代码里 `hub.pull("name")` 一键获取。这是把 prompt 从"散落在代码字符串"升级到"可治理资产"的核心工具。

---

## 1. 痛点

代码里 prompt 字符串的烦恼：

- 写得长 → 难评审
- 改了一次 → 全代码搜替换
- A/B 两个版本 → 自己手动开关
- 非工程师改 → 必须发版
- 不会写 prompt → 没有"中心仓库"找参考

Prompt Hub 把这一切搬到 UI + 版本控制。

---

## 2. 三种用法

| 用法 | 适用 |
|------|------|
| **Public Hub** | 全球工程师分享 prompt，搜索复用 |
| **私有 Hub（个人 / 组织）** | 你自己的 prompt 资产 |
| **Playground** | UI 即开即用调试，不一定要存 |

---

## 3. 拉取公开 Prompt

```python
from langchain import hub

prompt = hub.pull("rlm/rag-prompt")
print(prompt)
```

返回的就是一个 `ChatPromptTemplate`，可以直接接到 chain：

```python
from langchain_openai import ChatOpenAI
chain = prompt | ChatOpenAI(model="gpt-4o-mini")
```

常用公开 prompt（搜 https://smith.langchain.com/hub）：

- `rlm/rag-prompt` — RAG 通用
- `hwchase17/openai-tools-agent` — OpenAI tools agent
- `hwchase17/react-chat` — ReAct + 聊天历史
- `langchain-ai/openai-functions-template` — function call 模板

---

## 4. 推送私有 Prompt

```python
from langchain import hub
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}"),
    ("human", "{q}"),
])

# 第一次推送
hub.push("my-handle/role-qa", prompt)

# 后续更新自动 commit 新版本
prompt2 = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}，遵守规则：1. 简洁 2. 中文"),
    ("human", "{q}"),
])
hub.push("my-handle/role-qa", prompt2)
```

每次 push 产生一个新 commit，Hub UI 能看到 diff。

---

## 5. 按版本拉取

默认拉 latest：

```python
prompt = hub.pull("my-handle/role-qa")
```

指定 commit：

```python
prompt = hub.pull("my-handle/role-qa:abc123")  # commit hash
prompt = hub.pull("my-handle/role-qa:production")  # tag
```

可以给某个 commit 打 tag（如 `production`），代码引用 tag，运维 UI 切换 tag 指向不同 commit，实现"不发版改 prompt"。

---

## 6. 私有 prompt 元数据

```python
hub.push(
    "my-handle/role-qa",
    prompt,
    new_repo_description="角色扮演问答 prompt",
    new_repo_is_public=False,
    tags=["v3-stable", "qa"],
)
```

---

## 7. Playground

UI 入口：左侧菜单 **Playground**。能做的事：

- 任意模型 / 任意 prompt 调试
- 变量插值
- 一键对比两个模型 / 两个 prompt
- 把 trace 一键打开到 Playground 重跑（找到 bad case → 改 prompt 试）
- 保存为 Hub prompt

Playground 是 LangSmith 提效最直接的功能之一，**不写代码也能调 prompt**。

---

## 8. Prompt 版本化最佳实践

```
my-org/
├── chat/qa             - 主问答
├── chat/qa-v2          - 试验版（按 tag 灰度）
├── rag/answer
├── rag/rewrite-query
├── agent/system
└── agent/router
```

代码里写：

```python
prompt = hub.pull(f"my-org/chat/qa:{settings.PROMPT_TAG}")
```

`PROMPT_TAG` 走配置中心（apollo / Nacos），prompt 更新无需发版。

---

## 9. Prompt 与 Eval 联动

每次改完 prompt，先在 Playground 跑几条手测，再切换"Compare" tab 一键跑数据集 evaluation，看分数是否提升。**Prompt + Dataset + Evaluator** 是 LangSmith 设计的核心闭环。

---

## 10. demo

```python
# demos/langsmith/04_prompt_hub.py
import os
from dotenv import load_dotenv
from langchain import hub
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

load_dotenv()
assert os.getenv("LANGSMITH_API_KEY")

# 1) 拉公开 prompt
rag_prompt = hub.pull("rlm/rag-prompt")
print("RAG prompt 变量：", rag_prompt.input_variables)

# 2) 推私有 prompt（注释默认关闭，避免污染你的账号）
"""
my_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}，使用 {language} 回答"),
    ("human", "{q}"),
])
hub.push("you/role-qa", my_prompt)
"""

# 3) 用拉到的 prompt 构造 chain
chain = rag_prompt | ChatOpenAI(model="gpt-4o-mini")
print(chain.invoke({"context": "LCEL = LangChain Expression Language", "question": "LCEL 是什么？"}).content)
```

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `hub.pull` 404 | handle 错或私有未授权 | 检查命名 / API Key 权限 |
| push 报"already exists" | 已存在仓库 | 直接 push（自动新版本） |
| 拉的 prompt 是 `PromptTemplate` 不是 `ChatPromptTemplate` | 旧版 prompt | 重新 push 时用 Chat 版 |
| 没看到 commit 历史 | UI 没切到 Versions 标签 | UI 右侧 Commits |

---

## 12. 本章 demo

[`demos/langsmith/04_prompt_hub.py`](../../demos/langsmith/04_prompt_hub.py)

下一篇：[05-monitoring.md](05-monitoring.md)
