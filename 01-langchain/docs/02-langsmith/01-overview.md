# LangSmith 01：总览与第一条 Trace

> **一句话**：LangSmith 是 LangChain 官方推出的 LLM 应用平台，集 Tracing（可观测性）、Datasets（数据集）、Evaluation（评估）、Prompt Hub（提示版本管理）、Monitoring（监控）于一体，是 LangChain/LangGraph 应用的"标配后台"。

---

## 1. 为什么需要 LangSmith

LLM 应用和传统应用最大的区别是：

- **输入输出不可控**：同样 prompt 不同时候不同结果
- **链路深**：一次请求里 retriever / LLM / tool / parser 来回几次
- **错误难复现**：模型偶发幻觉、工具偶尔超时
- **质量评估难**：没有"对错"标准

普通 APM（Datadog/Sentry）看不到这些细节。LangSmith 专门解决：

1. **Tracing**：树形展示一次请求里每个 step 的输入/输出/耗时/token，相同 trace 可一键回放
2. **Datasets**：把生产里发现的 bad case 一键存成数据集，作为回归测试样本
3. **Evaluation**：用 LLM-as-Judge / 自定义 evaluator 跑数据集，比较不同 prompt / model 的得分
4. **Prompt Hub**：prompt 像代码一样有版本，前端 Playground 调好直接拉到生产
5. **Monitoring**：生产环境 P95 延迟、错误率、token 消耗、用户反馈

---

## 2. 与 LangChain 的关系

- LangSmith 是**独立产品**，不强制要求用 LangChain
- 但用 LangChain / LangGraph 时**几乎零配置**就接上了
- 也可用 `@traceable` 装饰器追踪任意 Python 代码（包括纯 OpenAI SDK 调用）

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

跑前面的任何 demo：

```python
# 假设环境变量已配置
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

prompt = ChatPromptTemplate.from_template("说一句关于 {x} 的诗")
chain = prompt | ChatOpenAI(model="gpt-4o-mini") | StrOutputParser()
chain.invoke({"x": "春天"})
```

跑完到 https://smith.langchain.com 进 `my-project` 项目，能看到一条 trace：

```
RunnableSequence
├── ChatPromptTemplate   12 ms
├── ChatOpenAI            832 ms  in=12 out=24 $0.0002
└── StrOutputParser       0.3 ms
```

点进去能看到完整 input/output/messages，还能在右上角点 "Playground" 把这次输入直接送到 Playground 修改 prompt 重跑。

---

## 6. 三个核心概念

| 概念 | 含义 |
|------|------|
| **Project** | 一组 trace 的命名空间，按 service 拆 |
| **Trace** | 一次请求的根 run |
| **Run** | trace 里的每个节点（chain / llm / tool / retriever） |
| **Dataset** | 一组 example，每个 example 是 `(input, output)` |
| **Experiment** | 一次评估运行，把某 chain 跑过数据集 |
| **Feedback** | 给 trace 打分（人工或自动） |

---

## 7. 用 @traceable 追踪任意函数

不限于 LangChain：

```python
from langsmith import traceable
import openai

client = openai.Client()

@traceable(run_type="llm", name="my_chat")
def chat(prompt: str) -> str:
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return r.choices[0].message.content

@traceable
def my_app(question: str) -> str:
    a = chat(f"用一句话回答：{question}")
    return a.upper()

my_app("Python 是什么")
```

LangSmith 会自动把 `my_app` 当 root run，`chat` 作为子 run，关系可视化。

支持的 `run_type`：`chain` / `llm` / `tool` / `retriever` / `embedding` / `parser` / `prompt`。

---

## 8. 上下文管理：trace / run_id

### 8.1 指定 run_id（便于自定义关联）

```python
chain.invoke(x, config={"run_id": "00000000-0000-0000-0000-000000000001"})
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

LangSmith UI 里可按 tag / metadata 过滤 trace。**强烈建议**把 `user_id`、`session_id`、版本号写进 metadata，便于事后分析。

---

## 10. 同步用户反馈

```python
from langsmith import Client

client = Client()
run_id = chain.invoke(x, config={"run_id": "..."}, return_run_id=True)
# 用户点了 👍
client.create_feedback(
    run_id=run_id,
    key="user_thumbs",
    score=1,
    comment="useful",
)
```

LangSmith 里这条 run 会带 1 颗星，按反馈过滤可以做"问题样本"集。

---

## 11. 自托管 / Region

LangSmith 提供：
- SaaS（默认）
- Self-hosted（Enterprise）
- EU region

Self-host 改 `LANGSMITH_ENDPOINT` 即可，SDK 行为完全一致。

---

## 12. demo

```python
# demos/langsmith/01_overview.py
import os
from dotenv import load_dotenv
load_dotenv()
assert os.getenv("LANGSMITH_API_KEY"), "请配置 LANGSMITH_API_KEY"

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langsmith import traceable

@traceable(name="prepare")
def prepare(topic: str) -> str:
    return topic.strip().lower()

chain = (
    ChatPromptTemplate.from_template("写一首关于 {x} 的两行诗")
    | ChatOpenAI(model="gpt-4o-mini")
    | StrOutputParser()
)

@traceable
def app(topic: str) -> str:
    cleaned = prepare(topic)
    return chain.invoke({"x": cleaned}, config={
        "tags": ["demo"],
        "metadata": {"topic": cleaned},
    })

print(app("春天"))
print("访问 LangSmith 项目页面查看 trace")
```

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| trace 没出现 | `LANGSMITH_TRACING` 没设 / key 错 | 确认环境变量、project 名 |
| trace 出现在 default project | 没设 `LANGSMITH_PROJECT` | 设环境变量或 `Client(project=...)` |
| 嵌套 traceable 看不到父子关系 | 上下文断了（多进程/线程） | 用 `with langsmith.tracing_context(...)` |
| 流式 token 在 trace 里只看到聚合 | 正常，trace 默认聚合 chunk | 用 events 查看 chunk-by-chunk |
| 敏感数据被上传 | 默认全量上传输入输出 | 设 `LANGSMITH_HIDE_INPUTS=true`/`HIDE_OUTPUTS` |

---

## 14. 本章 demo

[`demos/langsmith/01_overview.py`](../../demos/langsmith/01_overview.py)

下一篇：[02-tracing.md](02-tracing.md)
