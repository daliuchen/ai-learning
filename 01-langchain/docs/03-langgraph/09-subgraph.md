# LangGraph 09：子图 Subgraph 与团队隔离

> **一句话**：子图就是"把一个 graph 当成一个 node 用"。它能让多 Agent 项目里的"团队"拥有独立的内部 state，外部只看到团队的输入/输出，**显著减少 token 消耗和状态复杂度**。

---

## 1. 为什么需要子图

回顾上一篇的多 Agent：所有 Agent 共享一个 `messages` 列表，问题是：

- 每个子 Agent 看到的 messages 越来越长 → token 暴涨
- 子 Agent 内部细节污染主 state → 难调试

子图的解法：

- 子图有**自己的 State schema**（独立 messages 等）
- 主图只把"任务"传进去、把"结果"拿出来
- 子图内部可以是任意复杂的图（包括嵌套子图）

---

## 2. 两种使用方式

### 2.1 子图 schema 与父图相同：直接当 node 加进去

```python
sub_graph = sub_builder.compile()
parent.add_node("step", sub_graph)
```

子图直接收父图 state，返回新 partial state。

### 2.2 子图 schema 与父图不同（典型）：包一层函数

```python
def call_sub(state) -> dict:
    sub_input = {"sub_q": state["question"]}
    sub_out = sub_graph.invoke(sub_input)
    return {"answer": sub_out["sub_answer"]}

parent.add_node("sub", call_sub)
```

---

## 3. 完整例子：研究团队子图

```python
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent

# ===== 团队内 State =====
class TeamState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    summary: str

@tool
def web_search(q: str) -> str:
    """搜索"""
    return f"搜到 {q} 相关资料"

@tool
def arxiv_search(q: str) -> str:
    """论文搜索"""
    return f"找到 {q} 论文"

# 团队内两个 agent
web_agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), [web_search])
paper_agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), [arxiv_search])

def web_node(state):
    out = web_agent.invoke({"messages": state["messages"]})
    return {"messages": [HumanMessage(content=f"[web] {out['messages'][-1].content}", name="web")]}

def paper_node(state):
    out = paper_agent.invoke({"messages": state["messages"]})
    return {"messages": [HumanMessage(content=f"[paper] {out['messages'][-1].content}", name="paper")]}

def summarize(state):
    text = "\n".join(m.content for m in state["messages"] if hasattr(m, "name") and m.name in ("web","paper"))
    s = ChatOpenAI(model="gpt-4o-mini").invoke(f"用一段话总结：{text}").content
    return {"summary": s}

team = StateGraph(TeamState)
team.add_node("web", web_node)
team.add_node("paper", paper_node)
team.add_node("summarize", summarize)
team.add_edge(START, "web")
team.add_edge(START, "paper")     # web 与 paper 并行
team.add_edge("web", "summarize")
team.add_edge("paper", "summarize")
team.add_edge("summarize", END)
research_team = team.compile()
```

主图调用团队：

```python
class MainState(TypedDict):
    question: str
    research_summary: str
    answer: str

def call_team(state):
    out = research_team.invoke({"messages": [("human", state["question"])], "summary": ""})
    return {"research_summary": out["summary"]}

def write_answer(state):
    ans = ChatOpenAI(model="gpt-4o-mini").invoke(
        f"基于研究：{state['research_summary']}\n回答问题：{state['question']}"
    ).content
    return {"answer": ans}

main = StateGraph(MainState)
main.add_node("research", call_team)
main.add_node("write", write_answer)
main.add_edge(START, "research")
main.add_edge("research", "write")
main.add_edge("write", END)
app = main.compile()

print(app.invoke({"question": "LangGraph 的优势？", "research_summary": "", "answer": ""}))
```

观察重点：

- 主 state 只有 3 个字段，**没有** messages
- 团队 state 有 messages，但只活在子图内
- 父图 trace 上能看到子图（点开是嵌套结构）

---

## 4. 子图与并行

子图本身可以加进父图的并行 fan-out：

```python
parent.add_edge("split", "team_a")   # 子图 A
parent.add_edge("split", "team_b")   # 子图 B
parent.add_edge("team_a", "join")
parent.add_edge("team_b", "join")
```

两个团队同时跑。

---

## 5. 子图与 Checkpointer

**子图共享父图 Checkpointer**。invoke 子图时把 `config` 一起传：

```python
def call_team(state, config):
    return research_team.invoke({"...": "..."}, config)
```

如果 `add_node` 直接传子图对象，LangGraph 自动透传 config。

interrupt 在子图里也工作，父图会同样停下来等待 resume。

---

## 6. 子图 + Send：动态 fan-out

如果想根据 state 动态决定调用多少次子图（不是固定一次），用 `Send`（下一篇详细讲）：

```python
from langgraph.types import Send

def fan_out(state):
    return [Send("worker", {"item": x}) for x in state["items"]]
```

每个 Send 启一个子图 worker，并发跑。

---

## 7. demo

```python
# demos/langgraph/09_subgraph.py
# 见对应文件，结构同上面例子
```

完整代码在 `demos/langgraph/09_subgraph.py`。

---

## 8. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 子图 schema 不一致报错 | 直接当 node | 用 wrapper 函数转 schema |
| 子图 invoke 不带 config 没 trace | 没透传 | `def n(state, config): ...` 传过去 |
| 子图修改了父图字段没生效 | wrapper 返回字段名不对 | 严格按主 state schema 写返回 dict |
| 子图 token 没省下来 | 仍然把全量 messages 塞给子图 | 让 wrapper 只传必要字段 |
| HITL 在子图里 stuck | 没 checkpointer | compile 主图时加 checkpointer 即可 |

---

## 9. 本章 demo

[`demos/langgraph/09_subgraph.py`](../../demos/langgraph/09_subgraph.py)

下一篇：[10-map-reduce.md](10-map-reduce.md)
