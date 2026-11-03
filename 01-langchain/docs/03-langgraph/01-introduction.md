# LangGraph 01：介绍、定位与其他框架的对比

> LangGraph 是一个底层编排运行时，用显式的状态和图来描述 LLM 应用，面向的是复杂 Agent、多 Agent、需要人工介入和长时间运行的任务。传统 Agent 的循环藏在框架内部，LangGraph 把它摊开成一张图，每一步都能观察、打断点、从中途续跑。这篇先讲清楚它在 LangChain 生态里的位置，再和几个同类框架比一比。

版本：以 `langgraph 1.2.19`、`langchain 1.4.8` 为准。`create_react_agent` 已被 `langchain.agents.create_agent` 取代，本系列写 agent 用 `create_agent`，需要自己画图时才用 LangGraph。

---

## 1. 为什么需要 LangGraph

LangChain 的 `AgentExecutor` 和 LCEL 能很好地处理线性 chain，但遇到有循环、有分支、有状态、需要中断和恢复的场景就吃力了：

| 场景 | LCEL 能否做 | LangGraph 适配度 |
|------|-------------|---------------------|
| Prompt → Model → Parser | 能，很简单 | 杀鸡用牛刀 |
| RAG（一次检索 + 一次回答） | 能 | 可以写得更清楚 |
| Agent 调工具循环到结束 | 勉强，老 `AgentExecutor` 是黑盒（已移入 `langchain-classic`） | 原生支持（`create_agent` 就是一张 LangGraph 图） |
| 多步骤"反思 → 重新检索 → 重写答案" | 很难写 | 原生支持 |
| 多 Agent 互相调用 | 做不了 | 原生支持 |
| 工具调用前要人审 | 做不了 | 原生支持 |
| 进程挂了，从第 5 步继续 | 做不了 | 原生支持 |
| 状态分支、并行 fan-out / fan-in | 做不了 | 原生支持 |

标准的"模型 + 工具"循环直接用 `create_agent`（它底下就是 LangGraph）；流程里有固定步骤、并行、回路、多处人工审核时，再自己用 LangGraph 画图。

官方现在把这几层分成三档：

| 层 | 是什么 | 什么时候用 |
|----|--------|-----------|
| Deep Agents | 建在 `create_agent` 之上的整套 harness（上下文压缩、虚拟文件系统、子 agent） | 想直接拿一个能干活的 agent |
| LangChain `create_agent` | 模型 + 工具循环，用 middleware 定制 | 大多数 agent |
| LangGraph | 底层编排运行时：持久执行、流式、人在回路、持久化 | 确定性步骤与模型步骤混合的工作流 |

---

## 2. LangGraph 的核心思想

LangGraph 把应用建模成一张有向图：

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

一次 invoke 就是从 START 走到 END，沿途每个 node 依次更新 State。挂上 checkpointer 之后，LangGraph 会自动保存每一步的 State，暂停、回放、修改、续跑都建立在这个基础上。

---

## 3. 与 LangChain Agent 对比

| 维度 | LangChain `create_agent` | 自己画 LangGraph |
|------|--------------------------|-----------------|
| 编程模型 | 固定的"模型 ↔ 工具"循环，靠 middleware 插钩子 | 显式图，循环形状自己定 |
| 可见性 | 底层同样是图，`stream` / `get_state` 都能用 | 每步 state 都可观察 |
| 持久化 | 支持，传 `checkpointer=` | 支持，`compile(checkpointer=)` |
| Human-in-loop | 支持，`interrupt()` / HITL middleware | 支持，`interrupt()` |
| 多 Agent | 子 agent 当工具用 | 子图、`Send`、`Command` 随意编排 |
| 部署 | LangSmith Deployment / 自己包 | 同左 |
| 上手成本 | 低：一个函数调用，传模型、工具和提示词就能跑 | 中：要先理解 State、Reducer、条件边和 checkpointer |

（老的 `AgentExecutor` 是黑盒循环，不支持持久化和中断，已移入 `langchain-classic`，新项目不要再用。）

---

## 4. 与其他框架对比

### 4.1 LangGraph vs CrewAI

CrewAI 是高层的多 Agent 框架，围绕"角色 + 任务 + Crew"来组织代码。

| 维度 | CrewAI | LangGraph |
|------|--------|-----------|
| 抽象层级 | 高（Role/Task/Crew） | 低（State/Node/Edge） |
| 控制流 | 隐式（顺序/层级） | 显式（任意图） |
| 灵活度 | 中（按模板） | 高（任意拓扑） |
| 学习成本 | 低 | 中 |
| 调试 | 较弱 | LangSmith 完整 trace |
| 状态自定义 | 弱 | 任意 schema |
| HITL / 中断 | 有，但粒度粗：Task 设 `human_input=True` 会在任务给出结果前向人要反馈 | 任意节点 `interrupt()` 暂停，状态持久化后可隔天续跑 |
| 适合 | 经典"PM/Coder/QA"模式 | 任何 workflow |

模板化的 Agent 团队（写文案、做研究）用 CrewAI，半天就能搭起来。复杂业务流程、需要严格控制状态和中断的生产应用，我会选 LangGraph。

### 4.2 LangGraph vs Microsoft Agent Framework（原 AutoGen）

AutoGen 曾是微软主打对话式多 Agent 协作的框架，现在仓库已归档，微软建议新用户改用 Microsoft Agent Framework（MAF）。MAF 合并了 AutoGen 和 Semantic Kernel 的思路，支持 Python 和 .NET。

| 维度 | MAF | LangGraph |
|------|-----|-----------|
| 范式 | 图式 workflow + 内置多 Agent 模式（sequential / concurrent / handoff / group collaboration） | 状态机图 |
| 状态与持久化 | 检查点、time-travel | checkpointer、time-travel |
| 人在回路 | 支持 | 任意节点 `interrupt()` |
| 语言 | Python / .NET | Python / JS |
| 生态 | 微软系（Azure、Semantic Kernel 迁移用户） | LangChain 全家桶 + LangSmith |

两者的 workflow 思路很接近。已经在微软生态里、或者原来用 AutoGen / Semantic Kernel 的团队选 MAF 迁移成本更低；已经用 LangChain 工具链、想要 LangSmith 观测和评估的选 LangGraph。更完整的对比见 [横向对比：LangGraph vs CrewAI / Microsoft Agent Framework / LlamaIndex Workflows / OpenAI Agents SDK / Pydantic AI](../04-comparison/01-frameworks.md)。

### 4.3 LangGraph vs LlamaIndex Workflows

LlamaIndex 的 Workflows 模块也是状态机风格。

| 维度 | LlamaIndex Workflows | LangGraph |
|------|---------------------|-----------|
| 触发模型 | 事件驱动（emit/listen） | State 转换 |
| 起点 | RAG 出身 | Agent 出身 |
| 多 Agent | 中等支持 | 一等公民 |
| 持久化 | 较弱 | 强 |
| 生态 | LlamaIndex 检索器丰富 | LangChain 工具体系 |

如果你的 RAG 重度依赖 LlamaIndex，用 Workflows 顺手；否则在 Agent 和多 Agent 上 LangGraph 更成熟。

### 4.4 LangGraph vs Haystack / Semantic Kernel

Haystack（deepset）和 Semantic Kernel（微软）都是综合性框架，图在它们那里不是核心抽象。如果你需要的是显式状态机（自定义 State、条件边、中断续跑），LangGraph 的支持最完整。

### 4.5 选型决策树

```
是否多 Agent / 复杂分支？
├─ 否
│  └─ LCEL（LangChain）即可
├─ 是
│  ├─ 模板化角色（PM/Coder/QA）？
│  │  └─ CrewAI 快速搭起来
│  ├─ 微软生态 / 从 AutoGen 迁移？
│  │  └─ Microsoft Agent Framework
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
└── compile(checkpointer)  ← 编译成可执行 graph

CompiledStateGraph（Pregel）
├── invoke(state, config)        ← 同步执行
├── ainvoke / astream / astream_events
├── get_state(config)            ← 拿当前状态
├── update_state(config, values) ← 修改状态
└── get_state_history(config)    ← Time-travel
```

另外几样会反复用到：

- `MessagesState` / 自定义 TypedDict：State schema
- `InMemorySaver`（旧名 `MemorySaver` 仍可用）/ `SqliteSaver` / `PostgresSaver`：持久化
- `interrupt()`：人工中断
- `Send`：动态 fan-out
- `Subgraph`：子图复用

---

## 6. Hello LangGraph

先写一个最小的例子：

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

两个节点、三条边，trace、可视化、持久化这些基础设施却已经全部可用。

---

## 7. ReAct Agent：create_agent（5 行）

```python
import os
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

@tool
def get_weather(city: str) -> str:
    """查天气"""
    return f"{city} 晴 25℃"

model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "qwen-flash"))
agent = create_agent(model, [get_weather])

print(agent.invoke({"messages": [("human", "北京天气")]})["messages"][-1].content)
```

`create_agent` 返回的就是一张编译好的 `CompiledStateGraph`：

```
START → model → 条件 → tools → model → ... → END
```

> 旧写法 `from langgraph.prebuilt import create_react_agent`（以及它的 `state_modifier=` / `prompt=` 参数）在 langgraph 1.x 里仍能 import，但已标记弃用，文档建议迁到 `create_agent`（用 `system_prompt=` 和 middleware）。另外 `from langchain import create_agent` 这种写法不存在，要写 `from langchain.agents import create_agent`。

---

## 8. 路线图：本系列怎么学 LangGraph

1. 基础（02）：StateGraph、Node、Edge、条件边
2. 状态进阶（03）：TypedDict + Reducer + Annotated
3. ReAct Agent（04）：`create_agent` 与手写版
4. 持久化与会话（05）：Checkpointer，Thread，跨会话
5. HITL（06）：interrupt、update_state、Time-travel
6. 流式（07）：stream_mode 全模式
7. 多 Agent（08）：Supervisor、Network、Swarm、Hierarchical
8. 进阶模式（09-10）：Subgraph、Map-Reduce
9. API 风格（11）：Functional API
10. 部署（12）：LangSmith Deployment（Agent Server）/ Studio

---

## 9. 安装

```bash
pip install -U "langgraph>=1.2" "langchain>=1.4" "langchain-openai" \
  "langgraph-checkpoint-sqlite" grandalf   # grandalf 仅 draw_ascii 画图需要
```

`langgraph` 会自带 `langgraph-checkpoint`（含 `InMemorySaver`）和 `langgraph-prebuilt`（`ToolNode` / `tools_condition` 在这里）。

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
print(app.get_graph().draw_ascii())   # 需要 pip install grandalf
```

---

下一篇：[LangGraph 02：StateGraph 基础（Node / Edge / 条件边 / 编译运行）](02-stategraph.md)。
