# LangSmith 02：Tracing 完整指南

> Tracing 是 LangSmith 里最常用、也最容易上手的功能。它会给每次请求画出从入口到模型、工具的完整调用树，每个节点都带着输入、输出、token、耗时和错误。这篇把自动追踪、手动追踪、脱敏、查询和反馈都过一遍。

版本：以 `langsmith 0.14.x` 为准。demo 不配 API Key 也能运行，只是不上传 trace。

---

## 1. Trace 与 Run 数据模型

```
Trace（一次完整请求，最外层 Run）
└─ Run（任何 traceable 的步骤）
   ├─ inputs (dict)
   ├─ outputs (dict)
   ├─ run_type ("chain" / "llm" / "tool" / "retriever" / ...)
   ├─ start_time / end_time
   ├─ status ("success" / "error")
   ├─ error (Exception)
   ├─ metadata (dict)
   ├─ tags (list[str])
   ├─ usage (token / cost，若是 llm)
   └─ child_runs[] （递归）
```

每个 Run 都有一个 UUID，可以用 Client API 查询或者附加反馈。

---

## 2. 自动追踪：LangChain 用户

只要设了 `LANGSMITH_TRACING=true` 并装了 `langsmith` 包，所有 LangChain / LangGraph 代码都会自动追踪，不用写任何 trace 代码。每个 LCEL 节点对应一个 Run，按调用关系自动嵌套。

---

## 3. 手动追踪：@traceable

非 LangChain 的代码用装饰器手动标：

```python
from langsmith import traceable

@traceable(run_type="tool", name="search_web")
def search_web(q: str) -> list[str]:
    ...
```

参数：

| 参数 | 含义 |
|------|------|
| `run_type` | `chain`/`llm`/`tool`/`retriever`/`embedding`/`prompt`/`parser` |
| `name` | UI 显示名 |
| `tags` | 静态 tag |
| `metadata` | 静态 metadata |
| `client` | 自定义 LangSmith Client |
| `process_inputs` / `process_outputs` | 输入/输出预处理 hook（脱敏） |

```python
@traceable(
    run_type="llm",
    name="chat",
    tags=["openai"],
    metadata={"version": "v3"},
    process_inputs=lambda x: {**x, "api_key": "<redacted>"},
)
def chat(messages, api_key): ...
```

---

## 4. trace 上下文管理器

不方便用装饰器的地方（比如动态构造的逻辑、循环体）可以用它：

```python
from langsmith import trace

for question in questions:
    with trace(name="qa-loop", inputs={"q": question}) as run:
        try:
            answer = my_app(question)
            run.end(outputs={"answer": answer})
        except Exception as e:
            run.end(error=str(e))
```

---

## 5. 嵌套：上下文自动传递

LangSmith 用 contextvars 记录当前 run，所以嵌套调用的 @traceable 函数会自动挂到外层 run 下面：

```python
@traceable
def child(x):
    return x * 2

@traceable
def parent(x):
    return child(x) + 1

parent(3)
```

UI 上 `parent` 是根，`child` 是子。

跨线程或异步时要确保 contextvar 能传过去。`asyncio` / `anyio` 默认会传；普通的 `ThreadPoolExecutor` 不会，换成 `langsmith.utils.ContextThreadPoolExecutor` 就行；多进程就得手动传 `run_tree`。

---

## 6. 把已有 OpenAI / Anthropic SDK 调用接入

`langsmith` 提供了现成的包装函数：

```python
from langsmith.wrappers import wrap_openai
import openai

client = wrap_openai(openai.OpenAI())   # 包裹后所有调用自动 trace（兼容 OPENAI_BASE_URL，千问等也适用）

r = client.chat.completions.create(
    model="gpt-6-luna",
    messages=[{"role": "user", "content": "hi"}],
)
```

类似的有 `wrap_anthropic`、`wrap_gemini` 等（见 `langsmith.wrappers`）。

---

## 7. Threads（多轮对话归集）

把多条 trace 关联成同一段对话：

```python
chain.invoke(
    x,
    config={
        "metadata": {"session_id": "thread-001"},
    },
)
```

LangSmith 的 Threads 视图按 metadata 里的 `session_id`、`thread_id` 或 `conversation_id`（三个键任选一个）把 trace 聚到一起，可以从头到尾看完整对话。LangGraph 会把 `configurable.thread_id` 自动写进 metadata，所以配了 checkpointer 和 `thread_id` 的图不用手动设置。

---

## 8. 敏感数据脱敏

### 8.1 整体关闭

```bash
LANGSMITH_HIDE_INPUTS=true
LANGSMITH_HIDE_OUTPUTS=true
```

### 8.2 按字段

`@traceable(process_inputs=..., process_outputs=...)`：

```python
def redact(d):
    return {k: ("<redacted>" if "secret" in k else v) for k, v in d.items()}

@traceable(process_inputs=redact, process_outputs=redact)
def login(username, password): ...
```

### 8.3 全局

```python
from langsmith import Client
Client(hide_inputs=True, hide_outputs=True)
```

---

## 9. 多模态 / Attachments

大文件（图片、音频、PDF）不要塞进 inputs。`@traceable` 函数的参数如果标注为 `langsmith.schemas.Attachment`，SDK 会把它当附件上传，而不是序列化进 JSON：

```python
from langsmith import traceable
from langsmith.schemas import Attachment

@traceable
def vision(prompt: str, image: Attachment):
    mime_type, data = image          # Attachment 本质是 (mime_type, bytes) 元组
    ...

vision("这是什么？", Attachment(mime_type="image/png", data=open("a.png", "rb").read()))
```

run 详情页里会显示图片预览。数据集样本（Example）同样支持附件，评估处理图片、文档的应用时会用到。

---

## 10. 程序化查询 Trace

```python
from langsmith import Client
from datetime import datetime, timezone
client = Client()

# 拿一段时间内的 run
runs = client.list_runs(
    project_name="my-project",
    start_time=datetime(2026, 11, 1, tzinfo=timezone.utc),   # 带时区
    is_root=True,
    run_type="chain",
    error=False,
    limit=100,
)
for r in runs:
    print(r.id, r.name, r.total_tokens)
```

拿到这些数据后，可以针对出错的 trace 做告警，也可以按 token 排序，找出最费钱的请求。

---

## 11. Trace 转 Dataset

在 LangSmith 界面里，点 trace 右上角的 Add to Dataset 就行。用代码的话：

```python
client.create_examples(
    inputs=[{"q": "..."}, ...],
    outputs=[{"a": "..."}, ...],
    dataset_id=dataset_id,
)

# 或从一组 run id 复制
example = client.create_example_from_run(run_id="...", dataset_id=dataset_id)
```

把线上的 bad case 持续加进回归测试集，应该成为日常运维的一部分。

---

## 12. 自动添加 Feedback

最直接的办法是在业务代码里拿到 `run_id`，再调用 `client.create_feedback`。用户点赞、规则打分都走这个接口：

```python
run_id = uuid.uuid4()
answer = chain.invoke(x, config={"run_id": run_id})
client.create_feedback(run_id=run_id, key="auto_quality", score=quality_check(answer))
```

如果想让生产 trace 自动被打分，用 LangSmith 控制台里的 Online Evaluators / Automation Rules（见 [LangSmith 05：Monitoring 监控 / Dashboards / Alerts / Online Eval](./05-monitoring.md)），不需要在代码里写 callback。

---

## 13. 综合 demo

完整代码：[`demos/langsmith/02_tracing.py`](../../demos/langsmith/02_tracing.py)

```python
# demos/langsmith/02_tracing.py（节选）
import os, openai
from langsmith import trace, traceable
from langsmith.wrappers import wrap_openai

MODEL = os.environ.get("QWEN_MODEL", "gpt-6-luna")
client = wrap_openai(openai.OpenAI())

@traceable(run_type="retriever", name="kb_search")
def kb_search(q: str) -> list[str]:
    return [f"doc[{q}][{i}]" for i in range(2)]

@traceable(run_type="tool", name="format")
def format_docs(docs: list[str]) -> str:
    return "\n".join(docs)

@traceable(name="rag_app", tags=["demo"], metadata={"version": "v1"})
def rag(q: str) -> str:
    ctx = format_docs(kb_search(q))
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": f"基于：{ctx}"}, {"role": "user", "content": q}],
    )
    return r.choices[0].message.content

with trace(name="batch-eval", inputs={"size": 2}) as run:
    answers = [rag(q) for q in ["LCEL 是什么", "如何流式输出"]]
    run.end(outputs={"count": len(answers)})
```

配了 key 就能在 LangSmith 里看到 trace 树，没配也能正常跑。

---

## 14. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 多个 @traceable 函数看不到父子关系 | 跨进程 / 跨线程 contextvar 丢失 | 线程池换 `ContextThreadPoolExecutor`；跨进程调用时用 `langsmith_extra={"parent": ...}` 显式传父 run |
| 输入太大，trace 上传慢 | 大对象（图片/embedding） | 用 `process_inputs` 截断或 `LANGSMITH_HIDE_INPUTS` |
| trace 没有 token 信息 | 用了非 LangChain 模型 SDK | 用 `wrap_openai` / 在 outputs 里手动写 usage（`usage_metadata`） |
| 只想上传一部分 trace | 流量太大 | `LANGSMITH_TRACING_SAMPLING_RATE=0.1` 或 `Client(tracing_sampling_rate=0.1)` |
| 异步代码看不到所有子 run | 没 await 子任务就退出 | `asyncio.gather` 等齐 |
| project 不存在 | 第一次上传时会自动创建 | 确认 key 有创建 project 的权限 |

---

下一篇：[LangSmith 03：Datasets 与 Evaluation](03-evaluation.md)
