# LangGraph 10：Map-Reduce 与 Send API

> `Send(node_name, state)` 用在条件边里，可以在运行时决定并行启动多少个 node 实例，每个实例拿到各自不同的 state。这正好对应 map-reduce：先分别处理每一份，再合并结果。这篇讲 `Send` 的基本用法、fan-in 时 reducer 的作用、并发控制，以及一个投票选优的变体。

版本：以 `langgraph 1.2.19` 为准，示例模型用千问 `qwen-flash`。

---

## 1. 适用场景

- 长文档分块并行总结，最后合并
- 一组任务并行执行，最后汇总
- 用 LLM 给一组候选并行打分，选出最好的
- 同一个问题让 5 个 Agent 各自回答，再投票

固定的 fan-out（`add_edge(A, B)` 加 `add_edge(A, C)`）只能把分支数写死，`Send` 能在运行时按 state 决定分出多少个。

---

## 2. Send 基本用法

```python
from langgraph.types import Send

def fan_out(state) -> list[Send]:
    return [
        Send("worker", {"task": t}) for t in state["tasks"]
    ]

g.add_node("worker", worker_node)
g.add_conditional_edges("split", fan_out, ["worker"])
```

`fan_out` 是一个条件边函数，只不过返回的是 Send 列表而不是字符串。每个 Send 启动一个 "worker" node 实例，输入就是 Send 里的那个 dict。所有 worker 并行执行，结果再通过 reducer 合并回主 state。

---

## 3. 完整例子：并行总结一组段落

```python
from operator import add
from typing_extensions import Annotated, TypedDict
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send

class State(TypedDict):
    paragraphs: list[str]
    summaries: Annotated[list[str], add]   # ← reducer 让所有 worker 输出汇总到这里
    final: str

# Worker 的 state 是 paragraph 一段
class WorkerState(TypedDict):
    paragraph: str

model = ChatOpenAI(model="qwen-flash")

def summarize_one(s: WorkerState):
    summary = model.invoke(f"用一句话概括：{s['paragraph']}").content
    return {"summaries": [summary]}    # 进入主 state 的 summaries

def fan_out(state: State) -> list[Send]:
    return [Send("worker", {"paragraph": p}) for p in state["paragraphs"]]

def merge(state: State):
    bullet = "\n".join(f"- {s}" for s in state["summaries"])
    final = model.invoke(f"把以下摘要整合成 100 字短文：\n{bullet}").content
    return {"final": final}

g = StateGraph(State)
g.add_node("worker", summarize_one)
g.add_node("merge", merge)
g.add_conditional_edges(START, fan_out, ["worker"])
g.add_edge("worker", "merge")
g.add_edge("merge", END)

app = g.compile()
out = app.invoke({
    "paragraphs": [
        "LangChain 是 LLM 应用框架，提供 chain、agent 等抽象...",
        "LangGraph 是基于图的复杂 agent 编排框架...",
        "LangSmith 是配套可观测平台...",
    ],
    "summaries": [],
    "final": "",
})
print(out["final"])
```

运行过程是这样的：三个 paragraph 起了三个并行的 worker；每个 worker 往 `summaries` 写一条，reducer `add` 把三个列表拼起来；`merge` 要等所有 worker 跑完才执行，fan-in 的等待是自动的。

---

## 4. Send + 子图

`Send` 的目标也可以是子图：

```python
sub = build_subgraph().compile()
g.add_node("sub_worker", sub)
g.add_conditional_edges("split", lambda s: [Send("sub_worker", {...}) for ...], ["sub_worker"])
```

每个 Send 启动一个独立的 sub_worker 实例，子图内部完整跑一遍。

---

## 5. fan-in 靠 reducer

worker 写入的字段如果没设 reducer，并行写入会报 `InvalidUpdateError`。所以要写成：

```python
summaries: Annotated[list[str], add]
```

或者：

```python
results: Annotated[dict, lambda l, r: {**(l or {}), **(r or {})}]
```

这样多个并行结果才能聚合到一起。

---

## 6. 并发控制

LangGraph 默认会把所有 Send 同时启动，任务一多可能瞬间把 LLM 服务打满：

```python
app.invoke(input, config={"max_concurrency": 5})
```

这样同时运行的 task 最多 5 个。

---

## 7. 进阶：动态 fan-out + 评分 + 选最优

```python
def vote(state):
    # 对 5 个候选逐个打分（judge 返回数值分数）
    scores = []
    for ans in state["candidates"]:
        scores.append(judge.invoke(f"评分: {ans}"))
    best = max(zip(state["candidates"], scores), key=lambda x: x[1])
    return {"final": best[0]}

def fan_out_answers(state) -> list[Send]:
    return [Send("answerer", {"q": state["q"]}) for _ in range(5)]

g.add_node("answerer", answer_one)
g.add_node("vote", vote)
g.add_conditional_edges(START, fan_out_answers, ["answerer"])
g.add_edge("answerer", "vote")
g.add_edge("vote", END)
```

这是 best-of-N：并行生成 5 个回答，再用评审模型打分，取分数最高的一个（`candidates` 字段要配 `add` reducer 才能收齐 5 个回答）。它和 Self-Consistency 不是一回事：Self-Consistency 是多次采样后按最终答案做多数投票，把 `vote` 改成统计每个答案的出现次数就是了。

---

## 8. 本章 demo

完整代码：[`demos/langgraph/10_map_reduce.py`](../../demos/langgraph/10_map_reduce.py)，内容是第 3 节并行总结的例子。

---

## 9. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 并行写同一字段报 `InvalidUpdateError` | 没有 reducer | 加 `Annotated[..., add]` |
| Send 之后 merge 没等齐 | 漏了 `add_edge("worker", "merge")` | 必须显式加上 |
| 并发太高导致 OOM 或被限流 | 没限制并发 | `max_concurrency=...` |
| worker 收到的输入不对 | `Send` 的第二个参数会原样作为目标节点的输入 | 按目标节点的输入 schema 构造，别传 None |
| worker 内部又 fan-out | LangGraph 支持嵌套 Send | 留意总并发数 |

---

下一篇：[LangGraph 11：Functional API（@entrypoint / @task）](11-functional-api.md)
