# LangGraph 09：子图 Subgraph 与团队隔离

> 子图就是把一整张 graph 当成一个 node 来用。在多 Agent 项目里，可以让每个团队拥有独立的内部 state，外部只看到团队的输入和输出，token 消耗和状态复杂度都会降下来。这篇用一个研究团队的例子说明子图怎么接进父图，以及它和并行、Checkpointer、`Send` 的关系。

版本：以 `langgraph 1.2.19` 为准，示例里的子 agent 用 `langchain.agents.create_agent` 创建。

---

## 1. 为什么需要子图

上一篇的多 Agent 例子里，所有 Agent 共用一个 `messages` 列表。这样做有两个麻烦：每个子 Agent 看到的 messages 越来越长，token 跟着暴涨；子 Agent 的内部细节混进主 state，调试很费劲。

子图的思路是让团队有自己的 State schema（包括独立的 messages），主图只负责把任务传进去、把结果拿出来，子图内部想多复杂都行，甚至可以再嵌套子图。

---

## 2. 两种使用方式

### 2.1 子图和父图共享 state 字段：直接当 node 加进去

```python
sub_graph = sub_builder.compile()
parent.add_node("step", sub_graph)
```

子图通过同名字段读写父图的 state（schema 不必完全相同，但要有共享的键），返回的更新按父图的 reducer 合并。

### 2.2 子图 schema 与父图不同（更常见）：包一层函数

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
from langchain.agents import create_agent

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
web_agent = create_agent(ChatOpenAI(model="qwen-flash"), [web_search])
paper_agent = create_agent(ChatOpenAI(model="qwen-flash"), [arxiv_search])

def web_node(state):
    out = web_agent.invoke({"messages": state["messages"]})
    return {"messages": [HumanMessage(content=f"[web] {out['messages'][-1].content}", name="web")]}

def paper_node(state):
    out = paper_agent.invoke({"messages": state["messages"]})
    return {"messages": [HumanMessage(content=f"[paper] {out['messages'][-1].content}", name="paper")]}

def summarize(state):
    text = "\n".join(m.content for m in state["messages"] if hasattr(m, "name") and m.name in ("web","paper"))
    s = ChatOpenAI(model="qwen-flash").invoke(f"用一段话总结：{text}").content
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
    ans = ChatOpenAI(model="qwen-flash").invoke(
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

跑完可以留意三点：主 state 只有 3 个字段，里面没有 messages；团队 state 有 messages，但只存在于子图内部；在父图的 trace 上能看到子图，点开是嵌套结构。

---

## 4. 子图与并行

子图也可以放进父图的并行 fan-out 里：

```python
parent.add_edge("split", "team_a")   # 子图 A
parent.add_edge("split", "team_b")   # 子图 B
parent.add_edge("team_a", "join")
parent.add_edge("team_b", "join")
```

这样两个团队会同时跑。

---

## 5. 子图与 Checkpointer

子图共用父图的 Checkpointer。在包装函数里 invoke 子图时，建议把 `config` 一起传下去（Python 3.11 起 config 会通过 contextvar 自动传递，3.10 必须手动传）：

```python
def call_team(state, config):
    return research_team.invoke({"...": "..."}, config)
```

如果 `add_node` 时直接传的是子图对象，LangGraph 会自动透传 config。

子图里的 interrupt 同样有效，父图会一起停下等待 resume。从父图用 `Command(resume=...)` invoke，子图里被中断的节点会从头重跑。

想看子图内部的状态和事件：

```python
app.get_state(cfg, subgraphs=True)                     # 中断时 .tasks[0].state 是子图的快照
for ns, ev in app.stream(x, cfg, subgraphs=True, stream_mode="updates"):
    print(ns, ev)                                      # ns 形如 ('sub:<task_id>',)；顶层是 ()
```

子图默认继承父图的 checkpointer。如果要让子图自己保留跨调用的内部记忆，可以 `sub.compile(checkpointer=True)`；要完全无状态就用 `checkpointer=False`，细节见官方 subgraphs 文档。

---

## 6. 子图 + Send：动态 fan-out

如果调用子图的次数要根据 state 动态决定，而不是固定一次，就用 `Send`（下一篇细讲）：

```python
from langgraph.types import Send

def fan_out(state):
    return [Send("worker", {"item": x}) for x in state["items"]]
```

每个 Send 启动一个子图 worker，并发执行。

---

## 7. 本章 demo

完整代码：[`demos/langgraph/09_subgraph.py`](../../demos/langgraph/09_subgraph.py)，结构和第 3 节的研究团队例子相同。

---

## 8. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 子图 schema 不一致报错 | 直接当 node 加了进去 | 用包装函数转换 schema |
| 子图 invoke 没带 config，trace 断了 | 没透传 config | 写成 `def n(state, config): ...` 并传下去 |
| 子图改父图字段没生效 | 包装函数返回的字段名不对 | 严格按主 state 的 schema 写返回 dict |
| 用了子图 token 却没省下来 | 还是把全量 messages 塞给了子图 | 包装函数只传必要字段 |
| 子图里 HITL 卡住 | 没有 checkpointer | 编译主图时加上 checkpointer |

---

下一篇：[LangGraph 10：Map-Reduce 与 Send API](10-map-reduce.md)
