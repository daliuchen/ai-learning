# LangGraph 08：多 Agent 编排（Supervisor / Network / Swarm / Hierarchical）

> 任务复杂到一个 Agent 撑不住时，通常的做法是拆成几个各有专长的 Agent，再配一个调度者。常见的组织方式有四种：Supervisor（中心调度）、Network（互相调用）、Swarm（动态交棒）、Hierarchical（多层级）。这篇先讲为什么要拆，再逐个给出写法，最后说怎么选。

版本：以 `langchain 1.4.8`、`langgraph 1.2.19` 为准。官方推荐把子 agent 包成工具交给主 agent 调用（第 4 节）；`langgraph-supervisor`（已归档）和 `langgraph-swarm` 两个包官方已不再主推。

---

## 1. 为什么要多 Agent

单 Agent 做到一定规模，会遇到几类问题。工具一多，LLM 选错工具的概率就上去了；一个 prompt 既要会写文案又要会查数据库，角色互相打架；所有信息都塞进同一份 messages，上下文越来越长；职责混在一起，维护和评估都很难下手。

拆成多个 Agent 之后，每个 Agent 只配自己需要的工具和 prompt，研究员只管搜资料，作家只管写；每个 Agent 可以单独评估、单独优化；以后加一个新 Agent 也不会影响已有的。

---

## 2. 四种常见架构

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

每个 Agent 干完都回到 Supervisor，由 Supervisor 看结果决定下一步。

### 2.2 Network（互相调用）

```
        Agent A ⇄ Agent B
            ↘   ↗
            Agent C
```

任意两个 Agent 之间都能转交，灵活，但也容易乱。

### 2.3 Swarm（动态 handoff）

```
当前活跃 Agent 可以"交棒"给任意其他 Agent，被交棒的成为新活跃 Agent。
```

思路和 OpenAI Swarm 一样，State 里多一个 `active_agent` 字段。

### 2.4 Hierarchical（多层）

```
              Top Supervisor
              ↓        ↓
          Team A Sup   Team B Sup
          ↓     ↓      ↓     ↓
        a1     a2     b1     b2
```

下层的 Supervisor 本身也可以是一张图，层层嵌套。

---

## 3. Supervisor：手写实现

```python
from typing_extensions import Annotated, Literal, TypedDict
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain.agents import create_agent

# ----- 子 Agent 1: researcher -----
@tool
def search(q: str) -> str:
    """搜索"""
    return f"假装从网络搜到关于 {q} 的资料"

researcher = create_agent(
    ChatOpenAI(model="qwen-flash"),
    [search],
    system_prompt="你是研究员，专门搜资料。",
)

# ----- 子 Agent 2: writer -----
writer = create_agent(
    ChatOpenAI(model="qwen-flash"),
    [],
    system_prompt="你是文案专家，根据资料写成 200 字短文。",
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
    decision_model = ChatOpenAI(model="qwen-flash", temperature=0)
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

## 4. Supervisor：子 agent 当工具（官方推荐）

这种写法不需要手写路由节点。每个子 agent 包成一个工具，主 agent（supervisor）通过普通的工具调用来派活。子 agent 默认无状态，每次调用都从干净的上下文开始，子任务的中间过程不会把主 agent 的 messages 撑大：

```python
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI

model = ChatOpenAI(model="qwen-flash", temperature=0)

@tool
def web_search(q: str) -> str:
    """搜索网络资料"""
    return f"[搜到] {q} 是一个状态机型 Agent 编排框架"

researcher = create_agent(model, [web_search], system_prompt="你是研究员，只搜资料并复述，不要写作。")
writer = create_agent(model, [], system_prompt="你是文案专家，根据给你的资料写 100 字介绍。")

@tool("research", description="把研究任务交给研究员，返回资料摘要")
def research(task: str) -> str:
    return researcher.invoke({"messages": [("human", task)]})["messages"][-1].content

@tool("write", description="把资料交给文案，返回成稿")
def write(material: str) -> str:
    return writer.invoke({"messages": [("human", material)]})["messages"][-1].content

supervisor = create_agent(
    model, [research, write],
    system_prompt="你是调度员：先 research 再 write，把成稿原样返回。",
)
out = supervisor.invoke({"messages": [("human", "写一篇 100 字 LangGraph 介绍")]})
print(out["messages"][-1].content)
```

跑一遍的消息序列是 human → ai(调 research) → tool → ai(调 write) → tool → ai。代价是子 agent 藏在工具函数里，LangGraph 静态发现不了它们，`get_state(..., subgraphs=True)` 也看不到子 agent 的内部状态。如果中断时需要读嵌套状态，就改回自己画图（第 3 节，或下一篇的子图）。

### 4.1 预置库 langgraph-supervisor（仍可用）

```bash
pip install langgraph-supervisor      # 0.0.31，依赖 langgraph>=1
```

```python
from langgraph_supervisor import create_supervisor

# 每个 agent 必须设置 name
researcher = create_agent(model, [web_search], system_prompt="...", name="researcher")
writer = create_agent(model, [], system_prompt="...", name="writer")

app = create_supervisor(
    [researcher, writer],
    model=model,
    prompt="先 researcher 后 writer，最后给出短文。",
).compile()
out = app.invoke({"messages": [("human", "写 50 字 LangGraph 介绍")]})
```

它能和 `create_agent` 创建的子 agent 配合，消息序列里会出现 `transfer_to_researcher` / `transfer_back_to_supervisor` 两种 handoff 工具消息。不过它内部仍在调用已弃用的 `create_react_agent`，会报 `LangGraphDeprecatedSinceV10` 警告。新项目还是优先用上面的子 agent 当工具。

---

## 5. Network：互相调用

每个 Agent 都可以选择自己继续，或者把控制权交出去：

```python
def agent_a(state) -> Command:
    # 决定输出 + 下一步走谁
    ...
    return Command(
        update={"messages": [response]},
        goto="agent_b",   # 把控制权交给 agent_b
    )
```

`Command` 让 node 同时更新 state（`update=`）并决定下一个节点（`goto=`）。节点用 `-> Command[Literal["b"]]` 注解时，`get_graph()` 能把这条动态边画出来。

完整一点的网络示例：

```python
from langgraph.types import Command
from typing import Literal

def agent_a(state) -> Command[Literal["agent_b", "agent_c", END]]:
    next_step = decide_who_should_handle(...)
    return Command(update={"messages": [...]}, goto=next_step)
```

---

## 6. Swarm（OpenAI Swarm 风格）

用 `langgraph-swarm`（`pip install langgraph-swarm`，0.1.0，依赖 `langchain>=1`）：

```python
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph_swarm import create_handoff_tool, create_swarm

handoff_to_billing = create_handoff_tool(agent_name="billing", description="转给账单 agent")
handoff_to_tech = create_handoff_tool(agent_name="tech", description="转给技术 agent")

billing = create_agent(model, [handoff_to_tech], system_prompt="你是账单客服；技术问题转给 tech。", name="billing")
tech = create_agent(model, [handoff_to_billing], system_prompt="你是技术客服。", name="tech")

app = create_swarm([billing, tech], default_active_agent="billing").compile(checkpointer=InMemorySaver())
out = app.invoke({"messages": [("human", "我的程序报错了")]}, {"configurable": {"thread_id": "1"}})
print(out["active_agent"])   # 输出：tech
```

handoff tool 被调用时不返回字符串，而是把 `active_agent` 切换到另一个 Agent。这个切换对前端用户是无感的。

---

## 7. Hierarchical（多层 Supervisor）

把每个团队做成一张子图（下一篇细讲），顶层 Supervisor 决定把任务派给哪个团队：

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

每层 Supervisor 只看自己那一层的 messages。团队内部来回的中间消息留在子图里，不会进入顶层 Supervisor 的上下文，顶层每次调用模型时要带的历史就短得多。

---

## 8. State 设计：messages 字段共享与隔离

多 Agent 项目里，State 最难设计的部分是消息怎么传：

| 模式 | 主 state | 子 agent 看到 |
|------|----------|---------------|
| Supervisor + 直接传 messages | `messages` 一个字段 | 全量历史（容易撑爆） |
| Supervisor + 摘要传递 | `messages` + `summary` | 只看 summary + 最新消息 |
| 子图 + 独立 schema | 上层 messages 隔离下层 | 子图自己 messages |

生产里第三种最常见，下一篇的 Subgraph 会展开讲。

---

## 9. 完整 demo

完整代码：[`demos/langgraph/08_multi_agent.py`](../../demos/langgraph/08_multi_agent.py)

```python
# demos/langgraph/08_multi_agent.py
from typing_extensions import Annotated, Literal, TypedDict
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain.agents import create_agent

load_dotenv()

@tool
def web_search(q: str) -> str:
    """搜索"""
    return f"[假设搜到] 关于 {q} 的资料：{q} 是一个深度框架，包含 graph 抽象..."

researcher = create_agent(
    ChatOpenAI(model="qwen-flash", temperature=0),
    [web_search],
    system_prompt="你是研究员，只查资料并复述，不要写正文。",
)

writer = create_agent(
    ChatOpenAI(model="qwen-flash", temperature=0.5),
    [],
    system_prompt="你是文案专家，根据 messages 中的资料写 200 字短文。",
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
    decision = ChatOpenAI(model="qwen-flash", temperature=0).invoke([("system", sys), *state["messages"]])
    return {"next": decision.content.strip()}

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
| Supervisor 一直循环不结束 | 退出条件不清楚 | 在 prompt 里写明 FINISH 条件，再加一个步数兜底 |
| Agent 之间互相推活 | 都觉得该别人干 | 加兜底：连续两次没做出决策就 FINISH |
| 子 Agent 看到全部历史，token 暴涨 | 直接传了完整 messages | 用 summary 或子图隔离 |
| handoff 工具不工作 / 找不到 agent | 子 agent 没设 `name=`（supervisor 和 swarm 都按 name 路由） | `create_agent(..., name="xxx")` |
| 多 Agent trace 里分不清是谁说的 | 没设 name | 给 message 加 `name="researcher"` |

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

下一篇：[LangGraph 09：子图 Subgraph 与团队隔离](09-subgraph.md)
