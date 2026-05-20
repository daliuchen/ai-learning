# Pydantic AI 实战 01：横向对比 — Pydantic AI vs LangChain / LangGraph / CrewAI / AutoGen / LlamaIndex / OpenAI Agents SDK / Smolagents

> **一句话**：选型不要"信仰"。先看你的场景是简单 Chat / 单 Agent / RAG / 多 Agent / 生产可观测，再决定。本章给一张大表 + 三种典型场景的选择 + 同一个"查天气"Agent 用 Pydantic AI 和 LangChain 各写一遍的对照代码。

---

## 1. 为什么要做横向对比

2025-2026 年 Python 侧能写 LLM Agent 的框架已经至少 8 个：

```
LangChain        ← LCEL，最早、生态最广
LangGraph        ← LangChain 出品，复杂状态机
CrewAI           ← 角色化多 Agent
AutoGen          ← 微软，群聊范式
LlamaIndex       ← RAG 起家，现在也有 Workflows
Pydantic AI      ← Pydantic 团队，类型安全 / 生产可用
OpenAI Agents SDK← OpenAI 官方简化版
Smolagents       ← Hugging Face，"Code as Action"
```

每家都说"我能写 Agent"，但抽象不一样、长项不一样。看完本篇你应该能：

1. 5 分钟决策当前项目用哪个
2. 知道 Pydantic AI 适合 / 不适合 的场景
3. 看懂别的框架的代码、需要时混用

---

## 2. 主流 Agent 框架全景

| 框架 | 出品方 | 一句话定位 | 抽象层级 | 首发时间 |
|------|--------|------------|----------|----------|
| **Pydantic AI** | Pydantic 团队 | 类型安全的"FastAPI for AI" | 中 | 2024-12 |
| **LangChain** | LangChain Inc. | 标准抽象 + 上百适配器 + LCEL 编排 | 中 | 2022-10 |
| **LangGraph** | LangChain Inc. | 显式状态机 + 图，可中断可恢复 | 低 | 2024-01 |
| **CrewAI** | crewAI Inc. | Role / Task / Crew 模板化 | 高 | 2023-12 |
| **AutoGen** | Microsoft | 多 Agent 群聊范式 | 中 | 2023-08 |
| **LlamaIndex** | LlamaIndex Inc. | 文档 / 检索 → Workflows 多 Agent | 中 | 2022-11 |
| **OpenAI Agents SDK** | OpenAI | 官方简化版 + Handoff | 低 | 2025-03 |
| **Smolagents** | Hugging Face | "Code-as-Action"，让模型写 Python | 中 | 2024-12 |

抽象层级解释：

- **低**：你必须自己定义状态、控制流，框架给的是"原子"
- **中**：框架给了"Agent / Tool / Memory" 的概念，但具体流程你拼
- **高**：你只描述角色和任务，框架自动决定流程

---

## 3. 大表：十个维度横向对比

| 维度 | Pydantic AI | LangChain | LangGraph | CrewAI | AutoGen | LlamaIndex WF | OpenAI Agents | Smolagents |
|------|-------------|-----------|-----------|--------|---------|---------------|----------------|-----------|
| 核心抽象 | `Agent` + tool 函数 | `Runnable` (LCEL) | `StateGraph` + node | `Agent` + `Task` + `Crew` | `Agent` + GroupChat | `Workflow` + `step` | `Agent` + `handoff` | `CodeAgent` 写 Python |
| 类型安全 | ★★★★★ 一等公民 | ★★★ structured_output | ★★★★ TypedDict state | ★★ | ★★ | ★★★ | ★★★ | ★★ |
| 模型抽象 | 15+ Provider 字符串简写 | 数十个 partner 包 | 复用 LangChain | LiteLLM | 复用 OpenAI Client + 适配 | LiteLLM | 仅 OpenAI 一等 | LiteLLM |
| 工具系统 | `@agent.tool` 自动 schema | `@tool` + Pydantic args | 复用 LangChain | `Tool` 类 | `FunctionTool` | `FunctionTool` | `function_tool` | 任意 Python 函数 |
| 流式 | `agent.run_stream()` + 结构化流 | `chain.stream()` + LCEL | `app.stream()` | 一般 | 一般 | 一般 | 支持 | 一般 |
| 多 Agent | tool 调 Agent / Graph | 用 LangGraph | 一等公民 | 一等公民 | 一等公民（群聊） | step 间传递 | `handoff` | code agent 互调 |
| 可观测性 | Logfire 一行接入 | LangSmith | LangSmith | 第三方 | 第三方 | 第三方 | 内置 trace | 第三方 |
| 评测 | `pydantic-evals` 同厂 | LangSmith Eval | LangSmith Eval | 第三方 | 第三方 | 第三方 | 内置 eval | 第三方 |
| 生态 | 新，集成少 | 最广，1000+ 集成 | 复用 LangChain | 中等，模板多 | 中等 | 文档/检索最强 | 新 | 新 |
| 学习曲线 | 平缓 | 陡（积木多） | 中（要画图） | 平缓 | 中 | 中 | 平缓 | 平缓 |
| GitHub Stars (2026-05) | 8k+ | 95k+ | 9k+ | 25k+ | 35k+ | 38k+ | 5k+ | 4k+ |
| 社区活跃 | 高速增长 | 稳居第一 | 高速增长 | 稳定 | 学界关注 | RAG 主战场 | OpenAI 加持 | 增长中 |

注：星数随时间变化，仅供横向相对参考。

---

## 4. Pydantic AI 的强项与弱项

### 4.1 强项

| 项 | 说明 |
|----|------|
| **类型安全** | `Agent[DepsType, OutputType]` 是真泛型，IDE 跳转 / 补全 / mypy 全通 |
| **声明式输出** | `output_type=Invoice` 一行搞定 schema + 校验 + 重试，比 LangChain `with_structured_output` 更彻底 |
| **依赖注入** | `deps_type` + `RunContext` 像 FastAPI Depends 一样，把 DB / HTTP Client / 用户上下文注入工具 |
| **Logfire** | 同团队出品，一行 `logfire.configure()` 即看到完整调用链路、token、cost、retry 树 |
| **生产可用** | retry、timeout、超时、并发都做好了，不像有些框架还要自己包 |
| **API 稳定** | Pydantic 团队 = 严谨派，不会三个月 break 一次 |
| **模型无关** | 切换 model 改一行字符串 `"openai:gpt-5"` → `"anthropic:claude-haiku-4-5"` |

### 4.2 弱项

| 项 | 说明 |
|----|------|
| **生态相对新** | 2024-12 才发布，第三方集成少（向量库 / 工具 / loader 都要自己拼） |
| **多 Agent 抽象不像 CrewAI 那么开箱** | 需要用 Graph 或工具调工具的方式编排 |
| **没有"chain"那种轻量级线性管道** | 简单的 prompt → model → parser 三行能搞定，但要写很多 LangChain LCEL 那种"链式组合"会显得啰嗦 |
| **社区案例还在积累** | 想抄 demo 时不如 LangChain 多 |
| **本地模型支持依赖第三方** | Ollama / vLLM 要通过 OpenAI 兼容接口接入 |

---

## 5. 三种典型场景的选型推荐

### 场景 1：简单 Chat / 单 Agent 工具调用

需求：客服 / 内部小助手 / Slack bot，单 Agent 调 3-5 个工具就够。

| 选项 | 评分 | 理由 |
|------|------|------|
| **Pydantic AI** | ★★★★★ | 7 行代码搞定，类型安全，Logfire 直出 |
| LangChain | ★★★★ | LCEL 三件套也很简洁，但 structured output 不如 Pydantic AI 干净 |
| LangGraph | ★★ | 杀鸡用牛刀 |
| CrewAI | ★ | 单 Agent 用 Crew 抽象浪费 |
| OpenAI Agents SDK | ★★★★ | 简洁，但锁 OpenAI |

**推荐**：Pydantic AI。

### 场景 2：复杂多 Agent 工作流（研究 → 起草 → 审核 → 发布）

需求：流程有分支、并行、人审、可中断恢复。

| 选项 | 评分 | 理由 |
|------|------|------|
| **Pydantic AI + Graph** | ★★★★★ | 状态用 `dataclass`，节点用 `BaseNode`，类型化 + 可恢复 |
| **LangGraph** | ★★★★★ | 显式状态机，HITL / Checkpointer 成熟，Studio 调试 |
| LangChain | ★★ | 必须升级到 LangGraph |
| CrewAI | ★★★ | 简单流程很快，复杂分支吃力 |
| AutoGen | ★★★ | 群聊范式不适合"流水线" |
| LlamaIndex Workflows | ★★★★ | 事件驱动很优雅 |

**推荐**：

- 全新项目 → **Pydantic AI + pydantic-graph**（如果你重视类型安全）
- 已经在 LangChain 体系 → **LangGraph**（生态成熟、Studio 好用）

### 场景 3：已有 LangChain 大量代码，团队想试试 Pydantic AI

**不要重写**。LangChain 和 Pydantic AI 完全可以共存：

```python
# 在 LangChain 项目里新加的模块用 Pydantic AI
from pydantic_ai import Agent
new_agent = Agent("openai:gpt-5-mini", output_type=Invoice)

# 旧 LangChain Chain 保持原样
from langchain_openai import ChatOpenAI
old_chain = prompt | ChatOpenAI(model="gpt-4o-mini") | StrOutputParser()
```

迁移路径建议：

1. 新模块先用 Pydantic AI，老模块保持
2. 当老 chain 改动要碰 `with_structured_output` / Agent 时，顺手切到 Pydantic AI
3. 全量替换 = 最后再做，先把团队培养起来

---

## 6. 选型决策树

```
你的需求是什么？

├── 单次结构化抽取（不要 Agent 循环）
│   ├─ Pydantic AI ★ (output_type 即可)
│   └─ LangChain  ★ (with_structured_output)
│
├── 单 Agent + 工具调用（< 10 工具）
│   ├─ Pydantic AI ★★★★★
│   ├─ OpenAI Agents SDK（锁 OpenAI 可接受）★★★★
│   └─ LangChain agent ★★★
│
├── 多 Agent / Workflow
│   ├─ 流水线 / 状态机
│   │   ├─ Pydantic AI + Graph ★★★★★
│   │   └─ LangGraph ★★★★★
│   ├─ 角色化协作（CEO/Manager/Worker）
│   │   └─ CrewAI ★★★★
│   └─ 群聊式头脑风暴
│       └─ AutoGen ★★★★
│
├── RAG 重型项目
│   ├─ Pydantic AI + 自拼 retriever ★★★★
│   └─ LlamaIndex ★★★★★
│
└── 已有 LangChain 体系
    └─ 保留 LangChain + 新模块用 Pydantic AI ★★★★★
```

---

## 7. 同一个 Agent 用两个框架各写一遍

任务：**查天气**。Agent 收到城市名后调 `get_weather` 工具，返回结构化的 `WeatherReport(city, temp_c, condition)`。

### 7.1 Pydantic AI 版

```python
from dataclasses import dataclass
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

@dataclass
class Deps:
    """模拟一个天气 API 客户端"""
    api_key: str

class WeatherReport(BaseModel):
    city: str
    temp_c: float
    condition: str

agent = Agent(
    "openai:gpt-5-mini",
    deps_type=Deps,
    output_type=WeatherReport,
    system_prompt="你是天气助手，必须调用 get_weather 工具拿数据，不要自己编。",
)

@agent.tool
async def get_weather(ctx: RunContext[Deps], city: str) -> dict:
    """查询城市当前天气（模拟）。"""
    # 真实场景：用 ctx.deps.api_key 调外部 API
    fake = {"北京": (26.0, "晴"), "上海": (24.0, "多云"), "广州": (30.0, "雷阵雨")}
    temp, cond = fake.get(city, (20.0, "未知"))
    return {"city": city, "temp_c": temp, "condition": cond}

result = agent.run_sync("北京天气怎么样？", deps=Deps(api_key="demo"))
print(result.output)
# WeatherReport(city='北京', temp_c=26.0, condition='晴')
```

总共 **22 行**。包含：

- 依赖注入（`Deps`）
- 工具自动 schema 化（从函数签名 + docstring）
- 结构化输出（`output_type=WeatherReport`）
- 自动校验（如果模型乱填会触发 `ModelRetry`）

### 7.2 LangChain 版

```python
from pydantic import BaseModel
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

class WeatherReport(BaseModel):
    city: str
    temp_c: float
    condition: str

@tool
def get_weather(city: str) -> dict:
    """查询城市当前天气（模拟）。"""
    fake = {"北京": (26.0, "晴"), "上海": (24.0, "多云"), "广州": (30.0, "雷阵雨")}
    temp, cond = fake.get(city, (20.0, "未知"))
    return {"city": city, "temp_c": temp, "condition": cond}

model = ChatOpenAI(model="gpt-4o-mini")
# 必须分两步：第一步带 tools 调用，第二步带 structured_output
model_with_tools = model.bind_tools([get_weather])

system = SystemMessage("你是天气助手，必须调用 get_weather 工具拿数据，不要自己编。")
messages = [system, HumanMessage("北京天气怎么样？")]

# 手动跑工具循环
while True:
    resp = model_with_tools.invoke(messages)
    messages.append(resp)
    if not resp.tool_calls:
        break
    for call in resp.tool_calls:
        result = get_weather.invoke(call["args"])
        messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

# 最后一步：结构化输出
structured = model.with_structured_output(WeatherReport)
final = structured.invoke(messages + [HumanMessage("把上面的结果以 WeatherReport 返回")])
print(final)
```

约 **30 行**。可以看到：

1. LangChain 的工具循环要**手写 while** （或者用 `langgraph.prebuilt.create_react_agent` 黑盒掉）
2. 结构化输出和工具调用是**两套机制**（`with_structured_output` 和 `bind_tools` 不能同时叠加）
3. 消息管理要自己拼 `ToolMessage`

如果用 `create_react_agent`：

```python
from langgraph.prebuilt import create_react_agent
agent = create_react_agent(model, [get_weather])
result = agent.invoke({"messages": [("human", "北京天气怎么样？")]})
# 但 result["messages"][-1].content 是字符串，不是 WeatherReport
# 想结构化还得再过一次 with_structured_output
```

依旧要"两步走"。

### 7.3 对比小结

| 维度 | Pydantic AI | LangChain |
|------|-------------|-----------|
| 代码行数 | 22 | 30+ |
| 工具循环 | 内置 | 手写 / 用 langgraph prebuilt |
| 结构化输出 | 与工具调用同一机制 | `with_structured_output` 单独一步 |
| 类型提示 | `Agent[Deps, WeatherReport]`，IDE 全识别 | 各组件 `Any`，结构化输出要单独类型 |
| 依赖注入 | `deps_type` 一等公民 | 没有，要自己用闭包或全局变量 |

---

## 8. 从 LangChain 迁移到 Pydantic AI 的对照表

| LangChain | Pydantic AI |
|-----------|-------------|
| `ChatOpenAI(model="gpt-4o-mini")` | `Agent("openai:gpt-4o-mini")` |
| `prompt = ChatPromptTemplate.from_messages([...])` | `Agent(system_prompt="...")` 或 `@agent.system_prompt` |
| `chain = prompt \| model \| StrOutputParser()` | `agent` 本身就是 chain |
| `chain.invoke({"q": "..."})` | `agent.run_sync("...")` |
| `model.with_structured_output(Schema)` | `Agent(output_type=Schema)` |
| `@tool def foo(...)` | `@agent.tool_plain def foo(...)` |
| `model.bind_tools([tool1, tool2])` | tool 用装饰器加到 agent 上 |
| `create_react_agent(model, tools)` | `Agent(model, tools=[...])` 默认就是 react 循环 |
| `chain.stream({...})` | `agent.run_stream("...")` |
| `RunnableLambda` 自定义节点 | `@agent.tool_plain` 包装函数 |
| LangSmith 跟踪 | `logfire.configure()` + `logfire.instrument_pydantic_ai()` |
| `MemorySaver()` checkpointer | `message_history=[...]` 手动传 / 自己存 |
| `LangGraph.StateGraph` 状态机 | `pydantic_graph.Graph` + `BaseNode` |

### 8.1 迁移建议

**不要一次性大重构**。按以下顺序：

1. **第一步：新功能用 Pydantic AI**。老代码保持。
2. **第二步：替换"结构化抽取"**。这是收益最大的——`with_structured_output` → `Agent(output_type=...)`。
3. **第三步：替换 Agent 循环**。`AgentExecutor` / `create_react_agent` → `Agent + @agent.tool`。
4. **第四步：替换状态机**。`LangGraph` → `pydantic_graph`。
5. **第五步：替换可观测**。`LangSmith` → `Logfire`。
6. **检索器保留**。LangChain 的 Retriever / VectorStore 抽象很成熟，Pydantic AI 自己没有同等抽象，可以**继续用 LangChain 检索器，把它包成一个 Pydantic AI tool**：

```python
from pydantic_ai import Agent, RunContext
from dataclasses import dataclass
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

@dataclass
class Deps:
    retriever: Chroma

agent = Agent("openai:gpt-5-mini", deps_type=Deps)

@agent.tool
async def retrieve(ctx: RunContext[Deps], query: str) -> str:
    """从知识库检索相关文档。"""
    docs = ctx.deps.retriever.similarity_search(query, k=4)
    return "\n\n".join(f"[{i+1}] {d.page_content}" for i, d in enumerate(docs))
```

这种"Pydantic AI 在前 + LangChain 检索器在后"是 2026 年最流行的混合架构。

---

## 9. 与 OpenAI Agents SDK 的关系

OpenAI 在 2025-03 也出了官方 Agents SDK：

```python
from agents import Agent, Runner, function_tool

@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 26 degrees"

agent = Agent(name="WeatherAgent", instructions="You help with weather.", tools=[get_weather])
result = Runner.run_sync(agent, "What is the weather in Beijing?")
print(result.final_output)
```

看起来和 Pydantic AI 很像，区别：

| 维度 | Pydantic AI | OpenAI Agents SDK |
|------|-------------|-------------------|
| 模型支持 | 15+ Provider | 主要 OpenAI（其他要 LiteLLM 桥接） |
| 类型安全 | 一等公民 | 一般 |
| 结构化输出 | `output_type=Schema` 完整生态 | `output_type` 也有，但和 Pydantic AI 不同名 |
| Handoff | 通过 tool 调 Agent | 一等公民 `handoff` |
| Tracing | Logfire | 内置 trace UI |
| 适用 | 想跨厂商、想类型安全 | 重度 OpenAI 用户 |

如果你只用 OpenAI，**两者都可以**，差异在生态偏好。

---

## 10. 与 Smolagents 的差异

Smolagents（Hugging Face）是另一种风格——**让模型直接写 Python 代码当工具调用**：

```python
from smolagents import CodeAgent, HfApiModel
agent = CodeAgent(tools=[], model=HfApiModel())
agent.run("计算 (3 + 5) * 7 的结果")
# 模型实际生成 Python 代码运行，而不是 tool_call JSON
```

- **优点**：表达力强，能自己拼复杂逻辑
- **缺点**：执行 LLM 生成的代码 = 沙箱难做、安全性差

Pydantic AI 走的是"严格 schema 化 tool call"路线，**更适合生产**。Smolagents 适合实验 / Agent Arena 场景。

---

## 11. 与 CrewAI / AutoGen 的差异

### 11.1 CrewAI

```python
from crewai import Agent, Task, Crew
researcher = Agent(role="资深研究员", goal="收集信息", llm="gpt-4o-mini")
writer = Agent(role="科技作家", goal="写 300 字介绍", llm="gpt-4o-mini")
task1 = Task(description="研究 {topic}", agent=researcher)
task2 = Task(description="基于上一任务写介绍", agent=writer, context=[task1])
crew = Crew(agents=[researcher, writer], tasks=[task1, task2])
crew.kickoff(inputs={"topic": "Pydantic AI"})
```

- CrewAI 的隐含哲学：**Agent 是有"角色"的**，流程靠 Task 依赖图自动调度
- Pydantic AI 的哲学：**Agent 是个函数（输入 → 工具循环 → 输出）**，流程要显式（用 Graph 或 tool 调 tool）

什么时候选 CrewAI：

- 你要"虚拟员工模拟"（市场分析师 + 产品经理 + 工程师对话）
- 不关心代码结构、只想快速跑通流程

什么时候选 Pydantic AI：

- 你要严肃工程，要 IDE 跳转，要单元测试
- 你已经在用 Pydantic 全家桶（FastAPI / pydantic-settings）

### 11.2 AutoGen

```python
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat

researcher = AssistantAgent("researcher", model_client=model, system_message="搜资料")
writer = AssistantAgent("writer", model_client=model, system_message="根据资料写 200 字")
team = RoundRobinGroupChat([researcher, writer], max_turns=4)
result = await team.run(task="主题：Pydantic AI")
```

- AutoGen 的核心是"**多 Agent 群聊**"——所有 Agent 在一个会话里轮流发言
- 适合"反思 / 批评 / 头脑风暴"场景
- 不适合"线性流水线"

---

## 12. 团队选型建议表

| 团队场景 | 推荐 |
|----------|------|
| 学习 / 个人项目 | Pydantic AI（学到 Pydantic + Agent 双重红利） |
| 初创快速验证 MVP | CrewAI 或 Pydantic AI |
| 中型生产 + 严肃工程 | **Pydantic AI + Logfire** |
| 大厂自托管 + 完整 HITL / 持久化 | LangGraph + Self-host LangSmith / Pydantic AI + Graph |
| 已重度依赖 LangChain | 保留 + 新模块 Pydantic AI |
| 主要做 RAG | LlamaIndex（重检索）或 Pydantic AI + LangChain 检索器（混合） |
| 锁定 OpenAI 生态 | OpenAI Agents SDK |
| 多 Agent 群聊讨论 | AutoGen |

---

## 13. 常见误区

| 误区 | 真相 |
|------|------|
| Pydantic AI 是 LangChain 的"轻量替代" | 错，**抽象不同**。LangChain 是积木，Pydantic AI 是声明式 Agent |
| 选 Pydantic AI 就不能用 LangChain 工具了 | 可以混用。Pydantic AI Agent 里完全可以包 LangChain Retriever |
| 类型安全等于编译期保证 | Python 是动态类型，所谓"类型安全"是 IDE 提示 + Pydantic 运行时校验 + mypy |
| Logfire 必须付费 | 有免费额度，自托管也开源 |
| Pydantic AI 没有 LCEL 就缺一块 | LCEL 是 LangChain 把抽象碎片化的产物，Pydantic AI 没碎片所以不需要"胶水" |
| 多 Agent 必须用 CrewAI / AutoGen | Pydantic AI 用 Graph 也能做，类型还更安全 |
| LangChain 必须升级到 LangGraph | 简单线性 chain 不需要，复杂状态机才需要 |

---

## 14. 一图流总结

```
                  抽象层级
                     ▲
高（自动调度）       │  CrewAI
                     │
                     │  AutoGen
                     │  LlamaIndex Workflows
                     │
中（声明式 Agent）   │  Pydantic AI ★
                     │  LangChain  ★
                     │  OpenAI Agents SDK
                     │
低（显式状态机）     │  LangGraph
                     │  Pydantic Graph
                     │
                     └────────────────────────►
                       类型安全 / 工程严谨度
```

Pydantic AI 占据"中等抽象 + 高度类型安全"的甜蜜点。

---

## 15. 本章 demo

[`demos/practice/01_vs_langchain.py`](../../demos/practice/01_vs_langchain.py) —— 同一个"查天气"Agent 用 Pydantic AI 和 LangChain 各写一遍，跑起来直接对比。

```bash
python demos/practice/01_vs_langchain.py
```

---

下一篇：[02-project-rag.md](02-project-rag.md) —— 实战 1：RAG Agent，跑通一个能引用文档作答的问答 Agent。
