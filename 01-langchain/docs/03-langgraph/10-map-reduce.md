# LangGraph 10：Map-Reduce 与 Send API

> **一句话**：`Send(node_name, state)` 让你在条件边里**动态决定要并行启动多少个 node 实例**，每个实例用不同的 state，完美对应"map（每个分别处理）→ reduce（合并）"模式。

---

## 1. 经典场景

- 给一段长文档**分块并行总结**，最后合并
- 给一组任务**并行执行**，最后汇总
- 用 LLM 对一组候选**并行打分**，最后选最优
- **群体智慧**：同一问题 5 个 Agent 各自回答，再投票

固定 fan-out（`add_edge(A, B)` + `add_edge(A, C)`）只能写死分支数。`Send` 允许"运行时根据 state 决定 fan-out 多少个"。

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

- `fan_out` 是条件边函数，但**返回一个 Send 列表**而不是字符串
- 每个 Send 启动一个 "worker" node 实例，input 是 Send 里的 dict
- 所有 worker 并行执行
- 它们的结果通过 reducer 合并回主 state

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

model = ChatOpenAI(model="gpt-4o-mini")

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

观察：

- 三个 paragraph 并行起三个 worker
- 每个 worker 写入 `summaries`，reducer `add` 把三个列表 append 起来
- `merge` 在所有 worker 都跑完后执行（自动等齐 fan-in）

---

## 4. Send + 子图

`Send` 的目标可以是子图：

```python
sub = build_subgraph().compile()
g.add_node("sub_worker", sub)
g.add_conditional_edges("split", lambda s: [Send("sub_worker", {...}) for ...], ["sub_worker"])
```

每个 Send 启一个独立 sub_worker 实例，子图内部完整跑一遍。

---

## 5. fan-in：reducer 是关键

如果 worker 输出字段没设 reducer，**并行写会报 InvalidUpdateError**。一定要：

```python
summaries: Annotated[list[str], add]
```

或：

```python
results: Annotated[dict, lambda l, r: {**(l or {}), **(r or {})}]
```

确保多个并行结果能聚合。

---

## 6. 并发控制

默认 LangGraph 会同时启所有 Send，可能瞬间打爆 LLM 服务：

```python
app.invoke(input, config={"max_concurrency": 5})
```

把同时跑的 task 数限制到 5。

---

## 7. 进阶：动态 fan-out + 评分 + 选最优

```python
def vote(state):
    # 5 个回答，1 个评分
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

这就是 **Self-consistency** 推理模式：5 个 LLM 并行回答，投票选最优。

---

## 8. demo

```python
# demos/langgraph/10_map_reduce.py
```

完整代码在文件里。

---

## 9. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `InvalidUpdateError` 并行写同字段 | 没 reducer | 加 `Annotated[..., add]` |
| Send 后 merge 没等齐 | 漏 `add_edge("worker", "merge")` | 必须显式加 |
| 并发太高 OOM / 限流 | 不限并发 | `max_concurrency=...` |
| `Send` 类型报错 | 第二个参数必须是 dict | 不能传 None |
| worker 内部又 fan-out | LangGraph 支持嵌套 Send | 注意总并发数 |

---

## 10. 本章 demo

[`demos/langgraph/10_map_reduce.py`](../../demos/langgraph/10_map_reduce.py)

下一篇：[11-functional-api.md](11-functional-api.md)
