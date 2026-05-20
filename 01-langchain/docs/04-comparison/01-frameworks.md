# 横向对比：LangGraph vs CrewAI / AutoGen / LlamaIndex Workflows / Semantic Kernel

> **一句话**：要做"多 Agent / 复杂工作流"的项目，目前主流选项是 LangGraph、CrewAI、AutoGen、LlamaIndex Workflows、Semantic Kernel。本章给一份"对照实现 + 选型指南"，帮你 5 分钟决策。

---

## 1. 框架定位一览

| 框架 | 出品方 | 一句话定位 | 抽象层级 |
|------|--------|------------|----------|
| **LangGraph** | LangChain | 显式状态机 + 图，最灵活 | 低 |
| **CrewAI** | crewAI Inc. | Role/Task/Crew 模板化 Agent 团队 | 高 |
| **AutoGen** | Microsoft | 多 Agent 群聊范式 | 中 |
| **LlamaIndex Workflows** | LlamaIndex | 事件驱动状态机 | 中 |
| **Semantic Kernel** | Microsoft | Plugin-based，主打 .NET 生态 | 中高 |
| **OpenAI Agents SDK** | OpenAI | 官方简化版 Agent | 低 |

---

## 2. 五维度打分

| 维度 | LangGraph | CrewAI | AutoGen | LlamaIndex WF | OpenAI Agents |
|------|-----------|--------|---------|---------------|----------------|
| 学习曲线 | 中 | 低 | 中 | 中 | 低 |
| 控制流灵活度 | ★★★★★ | ★★★ | ★★★★ | ★★★★ | ★★★ |
| 多 Agent | ★★★★★ | ★★★★★ | ★★★★★ | ★★★ | ★★★ |
| 持久化 / HITL | ★★★★★ | ★★ | ★★★ | ★★★ | ★★ |
| 可观测 / 调试 | ★★★★★(LangSmith) | ★★★ | ★★ | ★★ | ★★★ |
| 工具生态 | ★★★★★ (LangChain) | ★★★★ | ★★★ | ★★★★ | ★★★ |
| 部署能力 | ★★★★★(Platform) | ★★ | ★ | ★ | ★★★ |

---

## 3. 同一任务用四个框架各写一遍

任务：**给定主题 → 研究员搜资料 → 作家写 200 字介绍**。

### 3.1 LangGraph

```python
from langgraph.graph import StateGraph, START, END
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from typing_extensions import Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages

@tool
def search(q): 
    """搜索"""
    return f"关于 {q} 的资料"

researcher = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), [search])
writer = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), [], state_modifier="你是作家，根据资料写 200 字")

class S(TypedDict):
    messages: Annotated[list, add_messages]
    stage: str

def supervisor(s):
    if not s.get("stage"): return {"stage": "research"}
    if s["stage"] == "research": return {"stage": "write"}
    return {"stage": "done"}

def call_r(s): return {"messages": researcher.invoke({"messages": s["messages"]})["messages"]}
def call_w(s): return {"messages": writer.invoke({"messages": s["messages"]})["messages"]}

def route(s) -> Literal["research", "write", "__end__"]:
    return {"research":"r", "write":"w", "done": END}[s["stage"]]   # type: ignore

g = StateGraph(S)
g.add_node("supervisor", supervisor)
g.add_node("r", call_r)
g.add_node("w", call_w)
g.add_edge(START, "supervisor")
g.add_conditional_edges("supervisor", lambda s: {"research":"r","write":"w","done":END}[s["stage"]])
g.add_edge("r", "supervisor"); g.add_edge("w", "supervisor")
app = g.compile()
print(app.invoke({"messages": [("human", "LangGraph")], "stage": ""}))
```

约 30 行，清晰可控。

### 3.2 CrewAI

```python
from crewai import Agent, Task, Crew
from crewai_tools import SerperDevTool

researcher = Agent(
    role="资深研究员",
    goal="收集主题的关键信息",
    tools=[SerperDevTool()],
    llm="gpt-4o-mini",
)
writer = Agent(
    role="科技作家",
    goal="把资料写成 200 字介绍",
    llm="gpt-4o-mini",
)
task1 = Task(description="研究主题 {topic}", expected_output="要点 bullets", agent=researcher)
task2 = Task(description="根据要点写 200 字介绍", expected_output="200 字文本", agent=writer, context=[task1])

crew = Crew(agents=[researcher, writer], tasks=[task1, task2], verbose=True)
print(crew.kickoff(inputs={"topic": "LangGraph"}))
```

约 15 行，最简洁，但**控制流隐式**（按 task 顺序），改逻辑难。

### 3.3 AutoGen

```python
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_ext.models.openai import OpenAIChatCompletionClient

model = OpenAIChatCompletionClient(model="gpt-4o-mini")

researcher = AssistantAgent("researcher", model_client=model, system_message="搜资料")
writer = AssistantAgent("writer", model_client=model, system_message="根据资料写 200 字")

team = RoundRobinGroupChat([researcher, writer], max_turns=4)
result = await team.run(task="主题：LangGraph")
print(result)
```

约 10 行，群聊范式，**适合对话场景**，非对话流程要费心。

### 3.4 LlamaIndex Workflows

```python
from llama_index.core.workflow import Workflow, step, Event, StartEvent, StopEvent
from llama_index.llms.openai import OpenAI

class ResearchDone(Event):
    research: str

class MyFlow(Workflow):
    @step
    async def research(self, ev: StartEvent) -> ResearchDone:
        llm = OpenAI(model="gpt-4o-mini")
        r = await llm.acomplete(f"研究 {ev.topic}")
        return ResearchDone(research=str(r))

    @step
    async def write(self, ev: ResearchDone) -> StopEvent:
        llm = OpenAI(model="gpt-4o-mini")
        r = await llm.acomplete(f"基于 {ev.research} 写 200 字")
        return StopEvent(result=str(r))

flow = MyFlow()
print(await flow.run(topic="LangGraph"))
```

约 18 行，事件驱动，**写 RAG 重的项目顺手**。

---

## 4. 选型决策树

```
你的项目类型？

├── 简单 LCEL 能搞定（线性 chain / 单 Agent）
│   → 直接用 LangChain LCEL，不用任何 Agent 框架
│
├── 想最快搭多 Agent 模板（写文章 / 做研究 / 流程化任务）
│   → CrewAI
│
├── 想要"Agent 群聊"协作（Code+Critic / Devil+Optimist）
│   → AutoGen
│
├── 已重度依赖 LlamaIndex 检索
│   → LlamaIndex Workflows
│
├── 想要严肃生产级 + 完整可观测 + 持久化 + HITL
│   → LangGraph ★★★★★
│
└── 在 .NET / 微软生态
    → Semantic Kernel
```

---

## 5. 互操作

LangGraph 的好处：**LangChain 工具体系是它原生支持的**。所以你可以：

- 在 LangGraph 里调 LangChain tool
- 在 LangGraph 里嵌入 LCEL chain
- 用 LangSmith 观察任意 LangGraph trace
- 通过 wrap，能调 CrewAI agent / AutoGen agent

反过来，CrewAI / AutoGen 也提供 LangChain 工具的桥接。

---

## 6. 生态规模

社区活跃度（截至 2025）：

- LangChain：47k+ stars，仓库最活跃
- LangGraph：8k+ stars，增长最快
- CrewAI：21k+ stars，社区案例多
- AutoGen：30k+ stars，研究界关注
- LlamaIndex：35k+ stars，文档检索主战场

---

## 7. 团队 / 项目实践建议

| 团队类型 | 推荐 |
|----------|------|
| 学习 / 个人项目 | 任选都行，建议 LangGraph |
| 初创快速验证 | CrewAI（先跑通），后续可改 LangGraph |
| 中型生产 | LangGraph + LangSmith |
| 大厂自托管 | LangGraph + 自部署 Postgres + Self-host LangSmith |
| 已有 LlamaIndex 检索体系 | LlamaIndex Workflows |
| 与 Code Agent / Critic 对话 | AutoGen |

---

## 8. 长期趋势

- 各框架在**互相借鉴**：CrewAI 加入 flow，LlamaIndex 加入 Workflows
- LangGraph 在**通用性与生态**上领先一个身位
- OpenAI Agents SDK 把"Agent 抽象"做进了 SDK 层，对其他框架是减法
- 多 Agent 协议化（Anthropic MCP / Google A2A）正在浮现，让框架间互通成为可能

---

下一篇：[02-project-rag-agent.md](02-project-rag-agent.md) — 实战项目 1。
