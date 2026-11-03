# 横向对比：LangGraph vs CrewAI / Microsoft Agent Framework / LlamaIndex Workflows / OpenAI Agents SDK / Pydantic AI

> 做多 Agent 或复杂工作流的项目，2026 年常见的选择有六个：LangGraph（以及建在它上面的 `langchain.agents.create_agent`）、CrewAI、Microsoft Agent Framework、LlamaIndex Workflows、OpenAI Agents SDK 和 Pydantic AI。这篇把同一个任务用几个框架分别写一遍，再给出选型建议，方便你快速做决定。

版本：以 2026 年 11 月各项目 GitHub 主页和官方文档为准。AutoGen 已归档，微软建议新用户改用 Microsoft Agent Framework（下称 MAF）；LangGraph 一节的代码按 `langchain 1.4.x / langgraph 1.2.x` 写，其余框架的代码块是示意写法。

---

## 1. 框架定位一览

| 框架 | 出品方 | 定位 | 抽象层级 |
|------|--------|------------|----------|
| LangGraph | LangChain | 低层编排框架：显式状态图 + 持久化 + 人在回路 | 低 |
| LangChain `create_agent` | LangChain | 建在 LangGraph 之上的高层 agent 工厂 + 中间件，开箱即用 | 中高 |
| CrewAI | crewAI Inc. | Crews（角色团队）+ Flows（事件驱动流程） | 高 |
| Microsoft Agent Framework (MAF) | Microsoft | AutoGen + Semantic Kernel 的继任者；Python / .NET；图式 workflow、检查点、HITL | 中 |
| LlamaIndex Workflows | LlamaIndex | 事件驱动步骤；公司重心已转向 LlamaParse（文档解析平台） | 中 |
| OpenAI Agents SDK | OpenAI | Agent + handoff + guardrail + session + tracing，另有 sandbox / realtime / voice agent | 低 |
| Pydantic AI | Pydantic | 类型安全的 agent 循环，模型无关，强调结构化输出与可持久化执行 | 中 |

> 已经退场或正在迁移的：AutoGen（已归档，迁到 MAF）；Semantic Kernel（官方提供了到 MAF 的迁移指南，新项目直接看 MAF）。

---

## 2. 多维度打分（主观，仅作参考）

下表按 1～5 分打分，5 分最好。

| 维度 | LangGraph | CrewAI | MAF | LlamaIndex WF | OpenAI Agents | Pydantic AI |
|------|-----------|--------|-----|---------------|----------------|-------------|
| 学习曲线 | 中 | 低 | 中 | 中 | 低 | 低～中 |
| 控制流灵活度 | 5 | 3 | 4 | 4 | 3 | 3 |
| 多 Agent | 5 | 5 | 4 | 3 | 4 | 3 |
| 持久化 / HITL | 5 | 3 | 4 | 3 | 3 | 4 |
| 可观测 / 调试 | 5(LangSmith) | 3(AMP) | 3 | 2 | 4(自带 tracing) | 4(OTel / Logfire) |
| 工具生态 | 5 | 4 | 3 | 4 | 3 | 4 |
| 托管部署 | 5(LangSmith Deployment) | 3(AMP) | 3 | 2 | 3 | 2 |

这些分数只代表我写作时的整体印象，每一格都会随版本变化。选型前一定要自己用一个最小任务试一遍。

---

## 3. 同一任务用几个框架各写一遍

任务是：给定一个主题，研究员先搜资料，作家再据此写一段 200 字介绍。

### 3.1 LangGraph（`create_agent` + StateGraph）

2026 年的写法：单个 agent 用 `langchain.agents.create_agent`（它取代了旧的 `langgraph.prebuilt.create_react_agent`），多个 agent 之间的编排仍然用 LangGraph 的 `StateGraph`。

```python
import os
from typing_extensions import Annotated, TypedDict

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

# 千问等 OpenAI 兼容服务：设 OPENAI_API_KEY + OPENAI_BASE_URL 即可
model = ChatOpenAI(model=os.environ.get("QWEN_MODEL", "gpt-6-luna"))


@tool
def search(q: str) -> str:
    """搜索资料。"""
    return f"关于 {q} 的资料：LangGraph 是低层编排框架，支持持久化与人在回路。"


researcher = create_agent(model, tools=[search], system_prompt="你是研究员，先用 search 查资料再整理要点。")
writer = create_agent(model, tools=[], system_prompt="你是作家，根据已有资料写 200 字介绍。")


class S(TypedDict):
    messages: Annotated[list, add_messages]


def call_r(s: S):
    return {"messages": researcher.invoke({"messages": s["messages"]})["messages"]}


def call_w(s: S):
    return {"messages": writer.invoke({"messages": s["messages"]})["messages"]}


g = StateGraph(S)
g.add_node("research", call_r)
g.add_node("write", call_w)
g.add_edge(START, "research")
g.add_edge("research", "write")
g.add_edge("write", END)
app = g.compile()

out = app.invoke({"messages": [("human", "主题：LangGraph")]})
print(out["messages"][-1].content)
```

线性流程不需要 supervisor 节点，直接连边就行。如果需要由模型决定下一步派给谁，用 LangGraph 08 篇里"子 agent 当工具"的模式，不要再用已归档的 `langgraph-supervisor` 库。

### 3.2 CrewAI

```python
from crewai import Agent, Task, Crew
from crewai_tools import SerperDevTool

researcher = Agent(
    role="资深研究员",
    goal="收集主题的关键信息",
    backstory="擅长快速检索并提炼要点",
    tools=[SerperDevTool()],
    llm="gpt-6-luna",
)
writer = Agent(
    role="科技作家",
    goal="把资料写成 200 字介绍",
    backstory="擅长把技术写得易懂",
    llm="gpt-6-luna",
)
task1 = Task(description="研究主题 {topic}", expected_output="要点 bullets", agent=researcher)
task2 = Task(description="根据要点写 200 字介绍", expected_output="200 字文本", agent=writer, context=[task1])

crew = Crew(agents=[researcher, writer], tasks=[task1, task2], verbose=True)
print(crew.kickoff(inputs={"topic": "LangGraph"}))
```

二十来行，角色、任务、上下文依赖都是声明式写法，读起来最直观。新版本还提供了 Flows（用 `@start` / `@listen` / `@router` 组织的事件驱动流程，Crew 可以作为 Flow 里的一步）来补足控制流，另有商业化的 AMP 控制面负责追踪和可观测。它的字段和项目脚手架变化比较快，以官方文档为准。

### 3.3 Microsoft Agent Framework（AutoGen 的继任者）

AutoGen 仓库现在标注了 "Project archived and no longer maintained"，并建议新用户使用 MAF。MAF 的 workflow 思路和 LangGraph 接近：显式的图、检查点、流式、HITL 和 time-travel，内置 sequential / concurrent / handoff / group collaboration 几种多 agent 模式，官方也提供了从 AutoGen、Semantic Kernel 迁移的指南。具体的 Python API 请看 `microsoft/agent-framework` 仓库的 quickstart，这里不贴代码。

### 3.4 LlamaIndex Workflows

```python
from llama_index.core.workflow import Workflow, step, Event, StartEvent, StopEvent
from llama_index.llms.openai import OpenAI

class ResearchDone(Event):
    research: str

class MyFlow(Workflow):
    @step
    async def research(self, ev: StartEvent) -> ResearchDone:
        r = await OpenAI(model="gpt-6-luna").acomplete(f"研究 {ev.topic}")
        return ResearchDone(research=str(r))

    @step
    async def write(self, ev: ResearchDone) -> StopEvent:
        r = await OpenAI(model="gpt-6-luna").acomplete(f"基于 {ev.research} 写 200 字")
        return StopEvent(result=str(r))

print(await MyFlow().run(topic="LangGraph"))
```

它是事件驱动的，RAG 比重大的项目用起来顺手。不过 LlamaIndex 公司目前主推的产品是 LlamaParse（文档解析和抽取），开源框架仍在维护；如果重点是通用的 agent 编排，其他几个选项用的人更多。

### 3.5 OpenAI Agents SDK

```python
from agents import Agent, Runner, function_tool

@function_tool
def search(q: str) -> str:
    """搜索资料。"""
    return f"关于 {q} 的资料"

writer = Agent(name="writer", instructions="根据资料写 200 字介绍")
researcher = Agent(name="researcher", instructions="先搜索，再把资料交给 writer",
                   tools=[search], handoffs=[writer])
print(Runner.run_sync(researcher, "主题：LangGraph").final_output)
```

它的基本原语是 Agent、handoff（把对话交给另一个 agent）、agents-as-tools、guardrails、sessions 和内置 tracing，另外还有 sandbox agent 和 realtime / voice agent。控制流主要靠模型决定什么时候 handoff，流程的确定性比不上显式的图。

### 3.6 Pydantic AI（简述）

Pydantic AI 的 agent 是类型化的：`Agent(model, output_type=Report, tools=[...])`。多 agent 一般是在一个 agent 的工具里调用另一个 agent。它的长处在结构化输出校验、依赖注入，以及基于 OTel / Logfire 的观测。细节可以看同系列的 [Pydantic AI 学习手册](../../../02-pydantic-ai/README.md)。

> 还有一个 eve（eve.dev）：TypeScript 的 agent 框架，围绕 workflow 工具、沙箱和审批来设计，适合由代码决定流程、模型只负责推理的场景。本手册不展开。

---

## 4. 选型决策树

```
你的项目类型？

├── 简单 LCEL 能搞定（线性 chain / 单次调用）
│   → 直接用 LangChain LCEL，不用任何 Agent 框架
│
├── 单 agent + 工具（含审批 / 摘要 / 护栏）
│   → langchain.agents.create_agent + 中间件
│
├── 想最快搭多 Agent 模板（写文章 / 做研究 / 流程化任务）
│   → CrewAI（Crews / Flows）
│
├── 已重度依赖 LlamaIndex 检索 / 文档解析
│   → LlamaIndex Workflows
│
├── 要严肃生产级 + 完整可观测 + 持久化 + HITL
│   → LangGraph（+ LangSmith）
│
├── 在 .NET / 微软生态，或原来用 AutoGen / Semantic Kernel
│   → Microsoft Agent Framework
│
├── 强依赖 OpenAI 模型 + 想要最小抽象 + 自带 tracing / voice
│   → OpenAI Agents SDK
│
└── 重视类型安全 / 结构化输出 / Python 栈统一
    → Pydantic AI
```

---

## 5. 互操作

LangGraph 原生支持 LangChain 工具和 MCP 生态，所以你可以：

- 在 LangGraph 里调用任何 LangChain tool，或者通过 `langchain-mcp-adapters` / 1.4 起内置的 `langchain.mcp.MCPAdapter` 接入 MCP 服务
- 在 LangGraph 里嵌入 LCEL chain
- 用 LangSmith 观察任意 LangGraph trace（其他框架也能通过 `@traceable` 或 OTel 接进来）
- 把别的框架的 agent 包成一个函数，当作 LangGraph 的节点或 tool

反过来，其他框架大多也提供了 MCP 客户端、A2A（Agent2Agent）协议支持或 LangChain 工具的桥接。跨框架互通，现在主要靠 MCP 和 A2A 这一层协议。

---

## 6. 生态规模

GitHub 星标（2026-11 的约数，仅看量级）：

- LangChain：约 149k（官方定位已改为 "The agent engineering platform"）
- LangGraph：约 44k
- AutoGen：约 62k（但已归档）
- CrewAI：约 60k
- LlamaIndex：约 53k
- OpenAI Agents SDK：约 30k
- Pydantic AI：约 21k
- Microsoft Agent Framework：约 14.5k（较新）

AutoGen 星标不低，却已经归档，这说明维护状态和发布节奏比星标数更值得看。

---

## 7. 团队 / 项目实践建议

| 团队类型 | 推荐 |
|----------|------|
| 学习 / 个人项目 | 任选都行，建议先 `create_agent`，再学 LangGraph |
| 初创快速验证 | CrewAI 或 `create_agent`，之后需要精细控制再下沉到 LangGraph |
| 中型生产 | LangGraph + LangSmith |
| 大厂自托管 | LangGraph + 自部署 Postgres checkpointer + 自托管 LangSmith / 或 OTel 接自有观测 |
| 已有 LlamaIndex 检索体系 | LlamaIndex Workflows |
| .NET / 原 AutoGen、Semantic Kernel 用户 | Microsoft Agent Framework |

---

## 8. 长期趋势

几个框架在互相借鉴：CrewAI 加了 Flows，LlamaIndex 有 Workflows，MAF 用的是图式 workflow，LangChain 则把 agent 重建在了 LangGraph 之上。

维护状态也在洗牌。AutoGen 归档了，`langgraph-supervisor` 也归档了，官方改为推荐把子 agent 包装成工具的 supervisor 写法。引入任何库之前，先去仓库看看有没有归档公告。

多 agent 的写法正在往"子 agent 当工具 + 中间件"收敛，专用的 prebuilt 库越来越少。MCP、A2A 这类协议则让不同框架之间有了互通的可能。

---

下一篇：[实战项目 1：RAG 知识库问答 Agent（create_agent + LangGraph）](02-project-rag-agent.md)。
