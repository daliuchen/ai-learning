# LangGraph 01：介绍、定位与与其他框架的对比

> **一句话**：LangGraph 是一个用"显式状态机 + 图"来表达 LLM 应用的低层框架，专门为复杂 Agent / 多 Agent / Human-in-the-loop / 长时间运行任务设计。它把传统 Agent 的"隐式循环"变成"可控的、可观察的、可断点续跑的图"。

---

## 1. 为什么需要 LangGraph

LangChain 的 `AgentExecutor` 与 LCEL 解决了"线性 chain"，但对**有循环、有分支、有状态、要中断和恢复**的复杂场景力不从心：

| 场景 | LCEL 能否做 | LangGraph 友好程度 |
|------|-------------|---------------------|
| Prompt → Model → Parser | ✅ 简单 | 杀鸡用牛刀 |
| RAG（一次检索 + 一次回答） | ✅ | 可以更优雅 |
| Agent 调工具循环到结束 | ⚠️ AgentExecutor 黑盒 | **天然支持** |
| 多步骤"反思 → 重新检索 → 重写答案" | ❌ 难写 | **天然支持** |
| 多 Agent 互相调用 | ❌ | **天然支持** |
| 工具调用前要人审 | ❌ | **天然支持** |
| 进程挂了，从第 5 步继续 | ❌ | **天然支持** |
| 状态分支、并行 fan-out / fan-in | ❌ | **天然支持** |

**结论**：超过 3 步的 Agent / Workflow，都建议用 LangGraph。

---

## 2. LangGraph 的核心思想

把应用建模成一个**有向图**：

```
节点（Node）：一个函数，输入 State，返回 State 的更新
边（Edge）：节点之间的连接，可以是无条件 or 条件
状态（State）：跨节点流动的 dict
```

```
            ┌────────┐
   START ──▶│ agent  │──┐
            └────────┘  │
                ▲       │ 条件边
                │       ▼
            ┌────────┐  ┌────────┐
            │ tool   │◀─│decide? │
            └────────┘  └────────┘
                              │ 退出
                              ▼
                            END
```

一次 invoke 就是从 START 走到 END 的一次"图遍历"，每个 node 依次更新 State。**LangGraph 自动持久化每一步的 State**，所以可以暂停、回放、修改、续跑。

---

## 3. 与 LangChain Agent 对比

| 维度 | LangChain `AgentExecutor` | LangGraph |
|------|--------------------------|-----------|
| 编程模型 | 黑盒循环 | 显式图 |
| 可见性 | 只能看 callback | 每步 state 都可观察 |
| 中间状态 | 不可访问 | 自由读写 |
| 持久化 | ❌ | ✅ Checkpointer |
| Human-in-loop | ❌ | ✅ `interrupt()` |
| Time-travel | ❌ | ✅ checkpoints |
| 多 Agent | 困难 | ✅ 一等公民 |
| 部署 | 自己包 | LangGraph Platform |
| 学习曲线 | 一节课 | 半天 |

---

## 4. 与其他框架对比

### 4.1 LangGraph vs CrewAI

**CrewAI** 是高层多 Agent 框架，强调"角色 + 任务 + Crew"。

| 维度 | CrewAI | LangGraph |
|------|--------|-----------|
| 抽象层级 | 高（Role/Task/Crew） | 低（State/Node/Edge） |
| 控制流 | 隐式（顺序/层级） | 显式（任意图） |
| 灵活度 | 中（按模板） | 高（任意拓扑） |
| 学习成本 | 低 | 中 |
| 调试 | 较弱 | LangSmith 完整 trace |
| 状态自定义 | 弱 | 任意 schema |
| HITL / 中断 | 不支持 | 一等公民 |
| 适合 | 经典"PM/Coder/QA"模式 | 任何 workflow |

**CrewAI 适合**：模板化 Agent 团队（写文案、做研究），半天上手。

**LangGraph 适合**：复杂业务流程、需要严控状态和中断的生产应用。

### 4.2 LangGraph vs AutoGen (Microsoft)

**AutoGen** 是微软的多 Agent 框架，强调"对话式协作"，Agent 之间像聊天一样轮流发言。

| 维度 | AutoGen | LangGraph |
|------|---------|-----------|
| 范式 | 多 Agent 群聊 | 状态机图 |
| 默认通信 | 消息传递 | 共享 State |
| 路由 | speaker_selection 函数 | 任意条件边 |
| 工具调用 | 较复杂 | LangChain 工具体系 |
| 生态 | 微软系工具 | LangChain 全家桶 |
| 调试可观测 | 一般 | LangSmith 一流 |
| 部署 | DIY | LangGraph Platform |

**AutoGen 适合**：典型对话式 Agent 协作（Code Agent + Critic）。

**LangGraph 适合**：非对话式工作流（pipeline / 分支 / 长流程）。

### 4.3 LangGraph vs LlamaIndex Workflows

LlamaIndex 推出的 **Workflows** 模块也是状态机风格。

| 维度 | LlamaIndex Workflows | LangGraph |
|------|---------------------|-----------|
| 触发模型 | 事件驱动（emit/listen） | State 转换 |
| 起点 | RAG 出身 | Agent 出身 |
| 多 Agent | 中等支持 | 一等公民 |
| 持久化 | 较弱 | 强 |
| 生态 | LlamaIndex 检索器丰富 | LangChain 工具体系 |

如果你 RAG 重度依赖 LlamaIndex，可以用 Workflows；否则 LangGraph 在 Agent / 多 Agent 上更成熟。

### 4.4 LangGraph vs Haystack / Semantic Kernel

Haystack（deepset）和 Semantic Kernel（微软）都是综合性框架，但都不是"图为一等公民"。LangGraph 在"显式状态机"这条路上走得最远。

### 4.5 选型决策树

```
是否多 Agent / 复杂分支？
├─ 否
│  └─ LCEL（LangChain）即可
├─ 是
│  ├─ 模板化角色（PM/Coder/QA）？
│  │  └─ CrewAI 快速搭起来
│  ├─ 多 Agent 群聊？
│  │  └─ AutoGen
│  ├─ 任意定制 + 严肃生产？
│  │  └─ ✅ LangGraph
│  └─ 重度依赖 LlamaIndex 检索？
│     └─ LlamaIndex Workflows
```

---

## 5. LangGraph 的核心组件

```
StateGraph(schema)         ← 图构造器
├── add_node(name, fn)     ← 添加节点
├── add_edge(a, b)         ← 添加边
├── add_conditional_edges  ← 条件分支
├── set_entry_point        ← 入口
└── compile(checkpointer)  ← 编译成可执行 graph

CompiledGraph
├── invoke(state, config)        ← 同步执行
├── ainvoke / astream / astream_events
├── get_state(config)            ← 拿当前状态
├── update_state(config, values) ← 修改状态
└── get_state_history(config)    ← Time-travel
```

加上：

- `MessagesState` / 自定义 TypedDict：State schema
- `MemorySaver` / `SqliteSaver` / `PostgresSaver`：持久化
- `interrupt()`：人工中断
- `Send`：动态 fan-out
- `Subgraph`：子图复用

---

## 6. Hello LangGraph

最简单的"hello"：

```python
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

class State(TypedDict):
    text: str

def upper(s: State) -> State:
    return {"text": s["text"].upper()}

def shout(s: State) -> State:
    return {"text": s["text"] + "!!!"}

g = StateGraph(State)
g.add_node("upper", upper)
g.add_node("shout", shout)
g.add_edge(START, "upper")
g.add_edge("upper", "shout")
g.add_edge("shout", END)

app = g.compile()
print(app.invoke({"text": "hi"}))
# {'text': 'HI!!!'}
```

虽然简单但已经具备：trace、可视化、可持久化的全部基础设施。

---

## 7. 用 LangGraph 写 ReAct Agent（5 行）

```python
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city} 晴 25℃"

agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), [get_weather])

print(agent.invoke({"messages": [("human", "北京天气")]})["messages"][-1].content)
```

5 行得到一个标准 ReAct Agent。`create_react_agent` 内部就是一个预编译的 StateGraph：

```
START → agent (LLM) →条件→ tools → agent → ... → END
```

---

## 8. 路线图：本系列怎么学 LangGraph

1. **基础（02-04）**：StateGraph、Node、Edge、ReAct
2. **状态进阶（03）**：TypedDict + Reducer + Annotated
3. **持久化与会话（05）**：Checkpointer，Thread，跨会话
4. **HITL（06）**：interrupt、update_state、Time-travel
5. **流式（07）**：stream_mode 全模式
6. **多 Agent（08）**：Supervisor、Network、Swarm、Hierarchical
7. **进阶模式（09-10）**：Subgraph、Map-Reduce
8. **API 风格（11）**：Functional API
9. **部署（12）**：LangGraph Platform / Studio

---

## 9. 安装

```bash
pip install \
  "langgraph>=0.2.0" \
  "langgraph-checkpoint>=2.0.0" \
  "langgraph-checkpoint-sqlite>=2.0.0" \
  "langgraph-prebuilt>=0.1.0"   # 部分版本叫这个
```

---

## 10. 本章 demo

```python
# demos/langgraph/01_hello.py
from langgraph.graph import StateGraph, START, END
from typing_extensions import TypedDict

class State(TypedDict):
    text: str

def upper(s): return {"text": s["text"].upper()}
def shout(s): return {"text": s["text"] + "!!!"}

g = StateGraph(State)
g.add_node("upper", upper); g.add_node("shout", shout)
g.add_edge(START, "upper"); g.add_edge("upper", "shout"); g.add_edge("shout", END)
app = g.compile()

print(app.invoke({"text": "hi langgraph"}))
print(app.get_graph().draw_ascii())
```

---

下一篇：[02-stategraph.md](02-stategraph.md) — StateGraph 完整教学。
