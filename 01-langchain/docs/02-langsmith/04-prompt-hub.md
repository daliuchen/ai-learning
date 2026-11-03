# LangSmith 04：Prompt Hub 与 Playground

> Prompt Hub 让 prompt 像代码一样有版本、有 owner，能在界面里调试、做 A/B，代码里用 `client.pull_prompt("name")` 就能取到。prompt 不再是散落在各处的字符串，改它也不用每次都发版。

版本：以 `langsmith 0.14.x` 为准。LangChain 1.x 里已经没有 `langchain.hub`，改用 `langsmith.Client().pull_prompt / push_prompt`；`push_prompt` 需要 LangSmith 账号。

---

## 1. 为什么要把 prompt 拿出代码

prompt 写在代码里，时间一长就会遇到这些麻烦：长 prompt 混在代码 diff 里很难评审；改一处要全局搜索替换；想做 A/B 得自己写开关；产品或运营想改个措辞，也得等工程师发版；团队里新人想找写得好的 prompt 参考，也没有统一的地方可查。

Prompt Hub 把 prompt 放到带版本控制的界面里管理，这些问题大多就解决了。

---

## 2. 三种用法

| 用法 | 适用 |
|------|------|
| Public Hub | 公开分享的 prompt，可以搜索复用 |
| 私有 Hub（个人 / 组织） | 存放自己或团队的 prompt |
| Playground | 在界面里直接调试，不一定要保存 |

---

## 3. 拉取公开 Prompt

```python
from langsmith import Client

client = Client()
prompt = client.pull_prompt("rlm/rag-prompt", dangerously_pull_public_prompt=True)
print(prompt.input_variables)     # ['context', 'question']
```

有两点要注意：

- 拉别人公开的 prompt，必须显式加 `dangerously_pull_public_prompt=True`。Hub 里存的是序列化的 LangChain 对象，陌生人上传的内容不一定可信，所以 SDK 默认拒绝，并报 `ValueError: Pulling a public prompt by owner/name is disabled by default...`。拉自己账号或组织下的 prompt 不需要这个参数。
- 拉公开 prompt 不需要 API Key，push 和拉私有 prompt 才需要。

返回的是 `ChatPromptTemplate`，可以直接接进 chain：

```python
from langchain_openai import ChatOpenAI
chain = prompt | ChatOpenAI(model="gpt-6-luna")
```

公开 prompt 可以在 https://smith.langchain.com/hub 搜索，例如通用的 RAG prompt `rlm/rag-prompt`。以前常用的 `hwchase17/openai-tools-agent`、`hwchase17/react-chat` 是给旧的 `AgentExecutor`（已搬进 `langchain-classic`）用的，新项目不需要，直接写 `system_prompt`。

---

## 4. 推送私有 Prompt

```python
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}"),
    ("human", "{q}"),
])

# 第一次推送；不写 owner 前缀就推到当前账号 / 组织下
url = client.push_prompt("role-qa", object=prompt)

# 后续更新自动 commit 新版本
prompt2 = ChatPromptTemplate.from_messages([
    ("system", "你是 {role}，遵守规则：1. 简洁 2. 中文"),
    ("human", "{q}"),
])
client.push_prompt("role-qa", object=prompt2)
```

每次 push 都会产生一个新 commit，在 Hub 界面里能看到前后 diff。

---

## 5. 按版本拉取

默认拉最新版本：

```python
prompt = client.pull_prompt("my-handle/role-qa")
```

指定 commit 或 tag：

```python
prompt = client.pull_prompt("my-handle/role-qa:abc12345")      # commit hash
prompt = client.pull_prompt("my-handle/role-qa:production")    # commit tag
```

可以给某个 commit 打 tag（比如 `production`），代码里只引用 tag。之后在界面里把 tag 指向别的 commit，线上用的 prompt 就换了，不需要发版。

---

## 6. 私有 prompt 元数据

```python
client.push_prompt(
    "role-qa",
    object=prompt,
    description="角色扮演问答 prompt",
    is_public=False,
    tags=["qa"],                  # 仓库标签
    commit_tags=["v3-stable"],    # 给这次 commit 打的 tag
    commit_description="收紧输出规则",
)
```

---

## 7. Playground

入口在左侧菜单的 Playground，可以做这些事：

- 用任意模型、任意 prompt 调试
- 变量插值
- 并排对比两个模型或两个 prompt
- 从 trace 直接打开到 Playground 重跑，发现 bad case 后马上改 prompt 试
- 保存为 Hub prompt

Playground 最大的好处是不写代码也能调 prompt，非工程师也能上手。

---

## 8. 怎么组织 prompt 的命名和版本

```
my-org/
├── chat-qa             - 主问答
├── chat-qa-v2          - 试验版（按 tag 灰度）
├── rag-answer
├── rag-rewrite-query
├── agent-system
└── agent-router
```

prompt 的标识是 `owner/name[:commit 或 tag]`，名字里不能再带 `/`，分组只能靠前缀约定。

代码里写：

```python
prompt = client.pull_prompt(f"my-org/chat-qa:{settings.PROMPT_TAG}")
```

`PROMPT_TAG` 放在配置中心（Apollo / Nacos）里，更新 prompt 就不用发版。

---

## 9. Prompt 与 Eval 联动

每次改完 prompt，先在 Playground 手动跑几条，再切到 Compare 标签页，在数据集上跑一遍 evaluation，看分数有没有提升。改 prompt、在数据集上评估、看分数决定是否上线，LangSmith 整套功能就是围绕这个循环设计的。

---

## 10. 本章 demo

完整代码：[`demos/langsmith/04_prompt_hub.py`](../../demos/langsmith/04_prompt_hub.py)

```python
# demos/langsmith/04_prompt_hub.py（节选）
import os
from langchain_openai import ChatOpenAI
from langsmith import Client

client = Client()
MODEL = os.environ.get("QWEN_MODEL", "gpt-6-luna")

# 1) 拉公开 prompt（不需要 LangSmith key）
rag_prompt = client.pull_prompt("rlm/rag-prompt", dangerously_pull_public_prompt=True)
print("RAG prompt 变量：", rag_prompt.input_variables)

# 2) 用拉到的 prompt 构造 chain
chain = rag_prompt | ChatOpenAI(model=MODEL)
print(chain.invoke({"context": "LCEL = LangChain Expression Language", "question": "LCEL 是什么？"}).content)

# 3) 推私有 prompt 需要 LANGSMITH_API_KEY，默认关闭，避免污染你的账号
#    client.push_prompt("role-qa", object=my_prompt)
```

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError: langchain.hub` | 1.x 已移除 | 改用 `langsmith.Client().pull_prompt` |
| `pull_prompt` 报 "Pulling a public prompt ... disabled by default" | 拉他人公开 prompt 的安全开关 | 确认可信后加 `dangerously_pull_public_prompt=True` |
| `pull_prompt` 404 | handle 写错，或者是没有权限的私有 prompt | 检查命名和 API Key 权限 |
| push 后没有变化 | 内容和最新 commit 相同 | 正常现象，不会产生新 commit |
| 拉的 prompt 是 `PromptTemplate` 不是 `ChatPromptTemplate` | 这个 prompt 当初按非 Chat 模板保存 | 重新 push 时用 Chat 版 |
| 没看到 commit 历史 | 界面没切到 Versions 标签 | 看界面右侧的 Commits |

---

下一篇：[LangSmith 05：Monitoring 监控 / Dashboards / Alerts / Online Eval](05-monitoring.md)
