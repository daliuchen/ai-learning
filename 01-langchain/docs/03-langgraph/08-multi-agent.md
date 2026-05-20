# LangGraph 08：多 Agent 编排（Supervisor / Network / Swarm / Hierarchical）

> **一句话**：把单 Agent 拆成"专精 Agent + 调度器"是处理复杂任务的标准做法。LangGraph 提供 4 种官方模式：**Supervisor**（中心调度）、**Network**（互相调用）、**Swarm**（动态 handoff）、**Hierarchical**（多层级）。

---

## 1. 为什么要多 Agent

单 Agent 的问题：

- **工具太多** → LLM 选错工具率上升
- **角色冲突** → 一个 prompt 既要会写文案又要会查数据库
- **上下文爆炸** → 所有信息塞一个 messages
- **职责不清** → 难维护、难评估

多 Agent 解：

- **每个 Agent 工具/prompt 专精**：研究员只搜资料，作家只写
- **职责清晰**：每个 Agent 单独评估、单独优化
- **更容易拓展**：加一个新 Agent 不影响别人

---

## 2. 四种典型架构

### 2.1 Supervisor（中心调度，最常用）

```
              ┌───────────────┐
              │   Supervisor  │  ← LLM，决定让谁干
              └───────────────┘
              ↓     ↓     ↓
        ┌────────┐ ┌────────┐ ┌────────┐
        │ Agent1 │ │ Agent2 │ │ Agent3 │
        └────────┘ └────────┘ └────────┘
```

每个 Agent 干完回到 Supervisor，Supervisor 看结果决定下一步。

### 2.2 Network（互相调用）

```
        Agent A ⇄ Agent B
            ↘   ↗
            Agent C
```

任意 Agent 之间都可以转交。灵活但容易乱。

### 2.3 Swarm（动态 handoff）

```
当前活跃 Agent 可以"交棒"给任意其他 Agent，被交棒的成为新活跃 Agent。
```

类似 OpenAI Swarm 概念，State 里加一个 `active_agent` 字段。

### 2.4 Hierarchical（多层）

```
              Top Supervisor
              ↓        ↓
          Team A Sup   Team B Sup
          ↓     ↓      ↓     ↓
        a1     a2     b1     b2
```

Supervisor 也可以是另一个图，套娃。

---

## 3. Supervisor：手写实现

```python
from typing_extensions import Annotated, Literal, TypedDict
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent

# ----- 子 Agent 1: researcher -----
@tool
def search(q: str) -> str:
    """搜索"""
    return f"假装从网络搜到关于 {q} 的资料"

researcher = create_react_agent(
    ChatOpenAI(model="gpt-4o-mini"),
    [search],
    state_modifier="你是研究员，专门搜资料。",
)

# ----- 子 Agent 2: writer -----
writer = create_react_agent(
    ChatOpenAI(model="gpt-4o-mini"),
    [],
    state_modifier="你是文案专家，根据资料写成 200 字短文。",
)

# ----- Supervisor -----
class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next: str   # 下一个要跑的 agent

def supervisor(state: State):
    sys = """你是调度器。根据进度选择下一步：
- 'researcher'：还需要查资料
- 'writer'：资料够了，让作家写
- 'FINISH'：作家已经写完
仅回复一个词。"""
    decision_model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    resp = decision_model.invoke([("system", sys), *state["messages"]])
    return {"next": resp.content.strip()}

def call_researcher(state: State):
    result = researcher.invoke({"messages": state["messages"]})
    return {"messages": [HumanMessage(content=f"[researcher 输出]\n{result['messages'][-1].content}", name="researcher")]}

def call_writer(state: State):
    result = writer.invoke({"messages": state["messages"]})
    return {"messages": [HumanMessage(content=f"[writer 输出]\n{result['messages'][-1].content}", name="writer")]}

def route(state: State) -> Literal["researcher", "writer", "__end__"]:
    n = state["next"]
    if n == "researcher": return "researcher"
    if n == "writer": return "writer"
    return END

g = StateGraph(State)
g.add_node("supervisor", supervisor)
g.add_node("researcher", call_researcher)
g.add_node("writer", call_writer)
g.add_edge(START, "supervisor")
g.add_conditional_edges("supervisor", route)
g.add_edge("researcher", "supervisor")
g.add_edge("writer", "supervisor")
app = g.compile()

out = app.invoke({
    "messages": [("human", "写一篇 200 字 LangGraph 介绍")],
    "next": "",
})
for m in out["messages"]:
    print(m)
```

---

## 4. Supervisor：用 langgraph-supervisor 简化

LangGraph 官方 prebuilt 包 `langgraph-supervisor` 把上面这套封装：

```python
from langgraph_supervisor import create_supervisor

agents = [researcher, writer]   # 每个 agent 必须有 name 属性
app = create_supervisor(
    agents=agents,
    model=ChatOpenAI(model="gpt-4o-mini"),
    prompt="你是项目经理...按顺序协调 researcher / writer 完成任务。",
).compile()
```

---

## 5. Network：互相调用

每个 Agent 都可以选择"自己继续"或"交棒"：

```python
def agent_a(state) -> Command:
    # 决定输出 + 下一步走谁
    ...
    return Command(
        update={"messages": [response]},
        goto="agent_b",   # 把控制权交给 agent_b
    )
```

`Command` 是 LangGraph 0.2+ 的统一方式，可以在 node 里同时改 state 和 决定下一节点。

完整网络示例：

```python
from langgraph.types import Command
from typing import Literal

def agent_a(state) -> Command[Literal["agent_b", "agent_c", END]]:
    next_step = decide_who_should_handle(...)
    return Command(update={"messages": [...]}, goto=next_step)
```

`Command[Literal[...]]` 注解让 LangGraph 知道哪些 node 可能被跳到，自动画图。

---

## 6. Swarm（OpenAI Swarm 风格）

`langgraph-swarm` prebuilt：

```python
from langgraph_swarm import create_handoff_tool, create_swarm

handoff_to_billing = create_handoff_tool("billing", "转给账单 agent")
handoff_to_tech = create_handoff_tool("tech", "转给技术 agent")

billing = create_react_agent(model, [handoff_to_tech], name="billing", ...)
tech = create_react_agent(model, [handoff_to_billing], name="tech", ...)

app = create_swarm([billing, tech], default_active_agent="billing").compile()
```

handoff tool 在工具调用时不返回字符串，而是把"active_agent"切到另一个 Agent。前端用户看不到这个切换。

---

## 7. Hierarchical（多层 Supervisor）

把每个"team"做成子图（看下一篇），顶层 Supervisor 决定 dispatch 到哪个 team：

```python
research_team = build_research_team()   # 子图：有自己的 supervisor + agents
writing_team = build_writing_team()

def top_supervisor(state): ...

g = StateGraph(State)
g.add_node("supervisor", top_supervisor)
g.add_node("research", research_team)
g.add_node("writing", writing_team)
...
```

每层 Supervisor 只看顶层 messages，团队内部 messages 隔离，**Token 消耗显著降低**。

---

## 8. State 设计：messages 字段共享与隔离

多 Agent 项目 State 最难的是"消息怎么传"：

| 模式 | 主 state | 子 agent 看到 |
|------|----------|---------------|
| Supervisor + 直接传 messages | `messages` 一个字段 | 全量历史（容易撑爆） |
| Supervisor + 摘要传递 | `messages` + `summary` | 只看 summary + 最新消息 |
| 子图 + 独立 schema | 上层 messages 隔离下层 | 子图自己 messages |

实际生产中第三种最常见，下一篇 Subgraph 展开。

---

## 9. 完整 demo

```python
# demos/langgraph/08_multi_agent.py
from typing_extensions import Annotated, Literal, TypedDict
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent

load_dotenv()

@tool
def web_search(q: str) -> str:
    """搜索"""
    return f"[假设搜到] 关于 {q} 的资料：{q} 是一个深度框架，包含 graph 抽象..."

researcher = create_react_agent(
    ChatOpenAI(model="gpt-4o-mini", temperature=0),
    [web_search],
    state_modifier="你是研究员，只查资料并复述，不要写正文。",
)

writer = create_react_agent(
    ChatOpenAI(model="gpt-4o-mini", temperature=0.5),
    [],
    state_modifier="你是文案专家，根据 messages 中的资料写 200 字短文。",
)

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    next: str

def supervisor(state):
    sys = (
        "你是调度器。看历史决定下一个执行者，回复 researcher / writer / FINISH。"
        "如果 messages 里还没有 [researcher 输出]，下一步 researcher；"
        "如果已经有研究资料但没有 [writer 输出]，下一步 writer；"
        "如果已经有 writer 输出，FINISH。仅回复一个词。"
    )
    decision = ChatOpenAI(model="gpt-4o-mini", temperature=0).invoke([("system", sys), *state["messages"]])
    return {"next": decision.content.strip().upper().replace("FINISH", "FINISH")}

def call(agent, label):
    def _node(state):
        result = agent.invoke({"messages": state["messages"]})
        return {"messages": [HumanMessage(content=f"[{label} 输出]\n{result['messages'][-1].content}", name=label)]}
    return _node

def route(state) -> Literal["researcher", "writer", "__end__"]:
    s = state["next"].lower()
    if "researcher" in s: return "researcher"
    if "writer" in s: return "writer"
    return END

g = StateGraph(State)
g.add_node("supervisor", supervisor)
g.add_node("researcher", call(researcher, "researcher"))
g.add_node("writer", call(writer, "writer"))
g.add_edge(START, "supervisor")
g.add_conditional_edges("supervisor", route)
g.add_edge("researcher", "supervisor")
g.add_edge("writer", "supervisor")

app = g.compile()
out = app.invoke({"messages": [("human", "写一篇 200 字 LangGraph 介绍")], "next": ""})
print("\n=== 最终 messages ===")
for m in out["messages"]:
    print(getattr(m, "name", type(m).__name__), ":", m.content[:200])
```

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| Supervisor 死循环 | 退出条件不清 | 在 prompt 里明确 FINISH 条件 + 兜底 step 计数 |
| Agent 互踢皮球 | 都觉得"该别人干" | 加一个"如果两次没决策就 FINISH"兜底 |
| 子 Agent 看到全部历史，token 暴涨 | 直接传完整 messages | 用 summary / 子图隔离 |
| handoff 工具不工作 | swarm 版本不对 | 升级 langgraph-swarm |
| 多 Agent trace 看不出哪个 agent 说的 | 没 set name | 给 message 加 `name="researcher"` |

---

## 11. 选型建议

| 业务特点 | 推荐架构 |
|----------|----------|
| 流程固定（先研究后写） | Supervisor + 状态机 prompt |
| 不同领域专家 | Supervisor 路由 |
| 客服多技能转接 | Swarm |
| 复杂多团队（公司项目） | Hierarchical |
| 探索性任务 | Network + 退出阈值 |

---

## 12. 本章 demo

[`demos/langgraph/08_multi_agent.py`](../../demos/langgraph/08_multi_agent.py)

下一篇：[09-subgraph.md](09-subgraph.md)
