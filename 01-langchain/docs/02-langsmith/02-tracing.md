# LangSmith 02：Tracing 完整指南

> **一句话**：Tracing 是 LangSmith 最核心、上手最快的能力——给每次请求自动画出从入口到模型/工具的完整调用树，附带输入、输出、token、耗时、错误。

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

每个 Run 有 UUID，可通过 Client API 查询/反馈。

---

## 2. 自动追踪：LangChain 用户

只要 `LANGSMITH_TRACING=true` 且装了 `langsmith` 包，**所有 LangChain/LangGraph 代码自动追踪**，无需写一行 trace 代码。

每个 LCEL 节点对应一个 Run，自动嵌套。

---

## 3. 手动追踪：@traceable

非 LangChain 代码：

```python
from langsmith import traceable

@traceable(run_type="tool", name="search_web")
def search_web(q: str) -> list[str]:
    ...
```

参数：

| 参数 | 含义 |
|------|------|
| `run_type` | `chain`/`llm`/`tool`/`retriever`/`embedding`/`prompt`/`parser`/`prompt` |
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

适合不能用装饰器的场景（动态构造、循环里）：

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

LangSmith 用 contextvars 跟踪当前 run，**子 @traceable 自动认父**：

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

跨线程/异步要保证 contextvar 传播，`asyncio` / `anyio` 默认就传播；多进程要手动传 `run_tree`。

---

## 6. 把已有 OpenAI / Anthropic SDK 调用接入

`langsmith` 提供 wrap：

```python
from langsmith.wrappers import wrap_openai
import openai

client = wrap_openai(openai.Client())   # 包裹后所有调用自动 trace

r = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "hi"}],
)
```

类似的 `wrap_anthropic`。

---

## 7. Threads（多轮对话归集）

把一组 trace 关联成"一次对话"：

```python
chain.invoke(
    x,
    config={
        "metadata": {"session_id": "thread-001"},
    },
)
```

LangSmith UI 中 "Threads" 视图按 `session_id` 聚合 trace，方便看完整对话。

LangGraph 用 `thread_id` 配置时自动写入 metadata.session_id，无需手动设置。

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

可以把图片/音频等附件挂在 run 上：

```python
from langsmith import attachment

@traceable
def vision(prompt: str, image_bytes: bytes):
    attachment("image", image_bytes, mime_type="image/png")
    ...
```

UI 上 run 详情会展示图片预览。

---

## 10. 程序化查询 Trace

```python
from langsmith import Client
client = Client()

# 拿一段时间内的 run
runs = client.list_runs(
    project_name="my-project",
    start_time=datetime(2025, 5, 1),
    is_root=True,
    run_type="chain",
    error=False,
    limit=100,
)
for r in runs:
    print(r.id, r.name, r.total_tokens)
```

可以根据错误 trace 做自动告警、按 token 排序找贵 trace 等。

---

## 11. Trace 转 Dataset

LangSmith UI 右上角 → "Add to Dataset"。代码：

```python
client.create_examples(
    inputs=[{"q": "..."}, ...],
    outputs=[{"a": "..."}, ...],
    dataset_id=dataset_id,
)

# 或从一组 run id 复制
example = client.create_example_from_run(run_id="...", dataset_id=dataset_id)
```

把 bad case 加入回归测试集是日常运维。

---

## 12. 自动添加 Feedback（评估器）

`as_runnable` 把 evaluator 当 callback：

```python
from langsmith.run_helpers import as_runnable
# 后续 Evaluation 章会详细演示
```

简单点：手动写 callback 直接 `client.create_feedback`：

```python
class FeedbackHandler(BaseCallbackHandler):
    def on_chain_end(self, outputs, run_id, **kw):
        score = quality_check(outputs)
        client.create_feedback(run_id=run_id, key="auto_quality", score=score)
```

---

## 13. 综合 demo

```python
# demos/langsmith/02_tracing.py
import os
from dotenv import load_dotenv
from langsmith import traceable, trace
from langsmith.wrappers import wrap_openai
import openai

load_dotenv()
assert os.getenv("LANGSMITH_API_KEY")

client = wrap_openai(openai.Client())

@traceable(run_type="retriever", name="kb_search")
def kb_search(q: str) -> list[str]:
    return [f"doc[{q}][{i}]" for i in range(2)]

@traceable(run_type="tool", name="format")
def format_docs(docs: list[str]) -> str:
    return "\n".join(docs)

@traceable(name="rag_app", tags=["demo"], metadata={"version": "v1"})
def rag(q: str) -> str:
    docs = kb_search(q)
    ctx = format_docs(docs)
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": f"基于：{ctx}"},
            {"role": "user", "content": q},
        ],
    )
    return r.choices[0].message.content

with trace(name="batch-eval", inputs={"size": 3}) as run:
    answers = [rag(q) for q in ["LCEL 是什么", "如何流式输出", "怎么做 RAG"]]
    run.end(outputs={"count": len(answers)})

print("done. 去 LangSmith 看 trace 树。")
```

---

## 14. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 多个 @traceable 函数看不到父子关系 | 跨进程 / 跨线程 contextvar 丢失 | 用 `with trace(...)` 显式传 |
| 输入太大，trace 上传慢 | 大对象（图片/embedding） | 用 `process_inputs` 截断或 `LANGSMITH_HIDE_INPUTS` |
| trace 没有 token 信息 | 用了非 LangChain 模型 SDK | 用 `wrap_openai` / 在 outputs 里手动写 usage |
| 异步代码看不到所有子 run | 没 await 子任务就退出 | `asyncio.gather` 等齐 |
| project 不存在 | 第一次会自动建 | 确认 key 有 project 创建权限 |

---

## 15. 本章 demo

[`demos/langsmith/02_tracing.py`](../../demos/langsmith/02_tracing.py)

下一篇：[03-evaluation.md](03-evaluation.md)
