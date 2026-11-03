# LangSmith 01：总览与第一条 Trace

> LangSmith 是 LangChain 官方的 LLM 应用平台，把 Tracing（可观测性）、Datasets（数据集）、Evaluation（评估）、Prompt Hub（提示词版本管理）和 Monitoring（监控）放在一起。用 LangChain 或 LangGraph 写的应用，几乎都会配它当后台。这一篇从注册开始，跑出第一条 trace。

版本：以 `langsmith` Python SDK 0.14.x、LangChain 1.4 / LangGraph 1.2 为准，示例模型用千问（OpenAI 兼容接口）。LangSmith 另有 Studio、Deployment（部署 agent）等功能，本书不展开。

---

## 1. 为什么需要 LangSmith

LLM 应用比传统应用难排查得多。同一个 prompt，不同时候跑出来的结果可能不一样；一次请求里 retriever、LLM、tool、parser 要来回调好几次；模型偶尔幻觉、工具偶尔超时，出了问题很难复现；输出的好坏也没有一个明确的对错标准。

Datadog、Sentry 这类普通 APM 看不到这些细节。LangSmith 提供的是这几块：

1. Tracing：用树形结构展示一次请求里每一步的输入、输出、耗时和 token，还能拿同样的输入重放
2. Datasets：把生产环境里发现的 bad case 直接存成数据集，当回归测试样本
3. Evaluation：用 LLM-as-Judge 或自定义 evaluator 跑数据集，比较不同 prompt、不同模型的得分
4. Prompt Hub：prompt 像代码一样有版本，在 Playground 里调好后，生产代码直接拉取
5. Monitoring：生产环境的 P95 延迟、错误率、token 消耗和用户反馈

---

## 2. 与 LangChain 的关系

LangSmith 是独立产品，不要求你用 LangChain。只是用 LangChain / LangGraph 时，设几个环境变量就接上了，几乎不用额外配置。不用 LangChain 的代码，也可以用 `@traceable` 装饰器追踪，包括直接调 OpenAI SDK 的代码。

---

## 3. 注册与 API Key

1. 访问 https://smith.langchain.com 注册
2. 进 Settings → API Keys → Create API Key
3. 拷贝 `lsv2_pt_xxx`

---

## 4. 接入：环境变量

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=lsv2_pt_xxx
export LANGSMITH_PROJECT=my-project        # 不填默认 "default"
export LANGSMITH_ENDPOINT=https://api.smith.langchain.com   # Self-host 可改
```

或 `.env` + `python-dotenv`。

---

## 5. 第一条 Trace

随便跑一个前面的 demo：

```python
# 假设环境变量已配置
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

prompt = ChatPromptTemplate.from_template("说一句关于 {x} 的诗")
chain = prompt | ChatOpenAI(model="gpt-6-luna") | StrOutputParser()  # 换成你的模型，例如千问：model="qwen-flash" + OPENAI_BASE_URL
chain.invoke({"x": "春天"})
```

跑完到 https://smith.langchain.com 进 `my-project` 项目，能看到一条类似下面的 trace（耗时、token 和费用都是示意）：

```
RunnableSequence
├── ChatPromptTemplate   12 ms
├── ChatOpenAI            832 ms  in=12 out=24 $0.0002
└── StrOutputParser       0.3 ms
```

点进去能看到完整的 input、output 和 messages。右上角的 Playground 按钮可以把这次输入直接带进 Playground，改了 prompt 再重跑。

---

## 6. 核心概念

| 概念 | 含义 |
|------|------|
| Project | 一组 trace 的命名空间，按 service 拆 |
| Trace | 一次请求的根 run |
| Run | trace 里的每个节点（chain / llm / tool / retriever） |
| Dataset | 一组 example，每个 example 是 `(input, output)` |
| Experiment | 一次评估运行，把某 chain 跑过数据集 |
| Feedback | 给 trace 打分（人工或自动） |

---

## 7. 用 @traceable 追踪任意函数

这个装饰器不限于 LangChain 代码：

```python
from langsmith import traceable
import openai

client = openai.Client()

@traceable(run_type="llm", name="my_chat")
def chat(prompt: str) -> str:
    r = client.chat.completions.create(
        model="gpt-6-luna",
        messages=[{"role": "user", "content": prompt}],
    )
    return r.choices[0].message.content

@traceable
def my_app(question: str) -> str:
    a = chat(f"用一句话回答：{question}")
    return a.upper()

my_app("Python 是什么")
```

LangSmith 会把 `my_app` 当作 root run，`chat` 作为它的子 run，在界面上画出调用关系。

支持的 `run_type`：`chain` / `llm` / `tool` / `retriever` / `embedding` / `parser` / `prompt`。

---

## 8. 上下文管理：trace / run_id

### 8.1 指定 run_id（便于自定义关联）

```python
chain.invoke(x, config={"run_id": uuid.UUID("00000000-0000-0000-0000-000000000001")})   # run_id 要传 UUID 对象
```

### 8.2 拿当前 run 上下文

```python
from langsmith import get_current_run_tree

@traceable
def step():
    rt = get_current_run_tree()
    print(rt.id, rt.name, rt.metadata)
```

### 8.3 trace context manager（不用装饰器）

```python
from langsmith import trace

with trace(name="my_pipeline", inputs={"q": "hi"}) as run:
    answer = ...
    run.end(outputs={"answer": answer})
```

---

## 9. metadata / tags

```python
chain.invoke(x, config={
    "tags": ["prod", "vip-user"],
    "metadata": {"user_id": "u123", "session": "s456"},
    "run_name": "ask-bot",
})
```

在 LangSmith 界面里可以按 tag 和 metadata 过滤 trace。建议把 `user_id`、`session_id` 和版本号都写进 metadata，出了问题才能按用户、按会话、按版本去找。

---

## 10. 同步用户反馈

```python
import uuid
from langsmith import Client

client = Client()
run_id = uuid.uuid4()                      # 自己生成 run_id，传进 config
chain.invoke(x, config={"run_id": run_id})
# 用户点了 👍
client.create_feedback(
    run_id=run_id,
    key="user_thumbs",
    score=1,
    comment="useful",
)
```

这条 run 在 LangSmith 里就带上了这个反馈分数。之后按反馈过滤，就能把差评的请求挑出来，整理成问题样本集。

---

## 11. 自托管 / Region

除了默认的 SaaS，LangSmith 还有 Self-hosted（Enterprise 版）和 EU region 可选。自托管时只要改 `LANGSMITH_ENDPOINT`，SDK 的用法完全一样。

---

## 12. 本章 demo

完整代码：[`demos/langsmith/01_overview.py`](../../demos/langsmith/01_overview.py)

```python
# demos/langsmith/01_overview.py（节选）
import os
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langsmith import traceable

load_dotenv()
MODEL = os.environ.get("QWEN_MODEL", "gpt-6-luna")

@traceable(name="prepare")
def prepare(topic: str) -> str:
    return topic.strip().lower()

chain = ChatPromptTemplate.from_template("写一首关于 {x} 的两行诗") | ChatOpenAI(model=MODEL) | StrOutputParser()

@traceable(name="my_poem_app")
def app(topic: str) -> str:
    cleaned = prepare(topic)
    return chain.invoke({"x": cleaned}, config={"tags": ["demo"], "metadata": {"topic": cleaned}})

print(app("春天"))
```

没有 `LANGSMITH_API_KEY` 时代码照常运行，只是不上传 trace，所以这个 demo 不用注册账号也能跑；要看 trace 就配上 key 并设 `LANGSMITH_TRACING=true`。

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| trace 没出现 | `LANGSMITH_TRACING` 没设 / key 错（旧名 `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` 仍兼容，新代码用 `LANGSMITH_*`） | 确认环境变量、project 名 |
| trace 出现在 default project | 没设 `LANGSMITH_PROJECT` | 设环境变量或 `ls.tracing_context(project_name=...)` |
| 嵌套 traceable 看不到父子关系 | 上下文断了（多进程/线程） | 用 `with langsmith.tracing_context(...)` / 显式传 `parent` |
| 流式 token 在 trace 里只看到聚合结果 | 正常现象，trace 默认把 chunk 合并 | 要看每个 chunk 就用 events |
| 敏感数据被上传 | 默认全量上传输入输出 | 设 `LANGSMITH_HIDE_INPUTS=true`/`HIDE_OUTPUTS` |
| 单测里 `GenericFakeChatModel` 的迭代器被提前耗尽 | tracing 开着时模型对象被序列化上传，遍历了迭代器 | 测试里关掉 tracing（`LANGSMITH_TRACING=false`） |
| 全局环境里开着 tracing，本地调试数据被传到云端 | 环境变量对所有 LangChain 代码生效 | 脚本里用 `tracing_context(enabled=False)`，或在 shell 里显式关 |

---

下一篇：[LangSmith 02：Tracing 完整指南](02-tracing.md)
