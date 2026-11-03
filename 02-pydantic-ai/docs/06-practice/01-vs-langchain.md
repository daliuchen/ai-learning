# Pydantic AI 实战 01：与 LangChain 等主流 Agent 框架横向对比

> 选框架别凭偏好，先看场景：简单 Chat、单 Agent、RAG、多 Agent，还是对生产可观测性有要求。这一篇有一张横向对比大表、三种常见场景下的选择，以及同一个"查天气"Agent 用 Pydantic AI 和 LangChain 各写一遍的对照代码。

以 `pydantic-ai 2.64.0`、`langchain 1.4.7` 为准；其他框架没有逐个按最新版核对，相关代码和评分只用来说明思路。

---

## 1. 为什么要做横向对比

到 2026 年，Python 里能写 LLM Agent 的框架至少有 8 个：

```
LangChain        ← LCEL，最早、生态最广
LangGraph        ← LangChain 出品，复杂状态机
CrewAI           ← 角色化多 Agent
MS Agent Framework ← 微软，AutoGen + Semantic Kernel 的继任者
LlamaIndex       ← RAG 起家，现在也有 Workflows
Pydantic AI      ← Pydantic 团队，类型安全 / 生产可用
OpenAI Agents SDK← OpenAI 官方简化版
Smolagents       ← Hugging Face，"Code as Action"
```

每家都能写 Agent，但抽象方式和擅长的方向各不相同。这篇的目的是帮你很快判断当前项目该用哪个，弄清 Pydantic AI 适合和不适合的场景，并且在需要时能看懂、混用别的框架的代码。

Pydantic AI 官方文档也有一组对比页（`pydantic.dev/docs/ai/comparisons/overview`，覆盖 LangChain/LangGraph、OpenAI Agents SDK、Google ADK、CrewAI 等），那是 Pydantic 团队自己的视角，可以对照着看。

---

## 2. 主流 Agent 框架全景

| 框架 | 出品方 | 定位 | 抽象层级 | 首发时间 |
|------|--------|------------|----------|----------|
| Pydantic AI | Pydantic 团队 | 类型安全的"FastAPI for AI" | 中 | 2024-12 |
| LangChain | LangChain Inc. | 标准抽象 + 上百适配器 + LCEL 编排 | 中 | 2022-10 |
| LangGraph | LangChain Inc. | 显式状态机 + 图，可中断可恢复 | 低 | 2024-01 |
| CrewAI | crewAI Inc. | Role / Task / Crew 模板化 | 高 | 2023-12 |
| Microsoft Agent Framework (MAF) | Microsoft | AutoGen + Semantic Kernel 的继任者：图式 workflow、检查点、HITL，内置多种多 Agent 模式 | 中 | 2025-10（公开预览） |
| LlamaIndex | LlamaIndex Inc. | 文档 / 检索 → Workflows 多 Agent | 中 | 2022-11 |
| OpenAI Agents SDK | OpenAI | 官方简化版 + Handoff | 中 | 2025-03 |
| Smolagents | Hugging Face | "Code-as-Action"，让模型写 Python | 中 | 2024-12 |

AutoGen（2023 年起的多 Agent 群聊框架）仓库已经归档、不再维护，微软建议新项目改用 MAF，所以下面的表格都用 MAF 代替 AutoGen。

抽象层级的含义：低，表示状态和控制流要自己定义，框架只提供基础构件；中，表示框架提供 Agent、Tool、Memory 这些概念，具体流程由你组合；高，表示你只描述角色和任务，流程由框架决定。

---

## 3. 大表：十个维度横向对比

| 维度 | Pydantic AI | LangChain | LangGraph | CrewAI | MAF | LlamaIndex WF | OpenAI Agents | Smolagents |
|------|-------------|-----------|-----------|--------|---------|---------------|----------------|-----------|
| 核心抽象 | `Agent` + tool 函数 | `create_agent`（底层组合仍是 `Runnable` / LCEL） | `StateGraph` + node | `Agent` + `Task` + `Crew` | `Agent` + 图式 Workflow | `Workflow` + `step` | `Agent` + `handoff` | `CodeAgent` 写 Python |
| 类型安全 | 5/5，一等公民 | 3/5，structured_output | 4/5，TypedDict state | 2/5 | 未评 | 3/5 | 3/5 | 2/5 |
| 模型抽象 | 30 多家 Provider，字符串简写 | 数十个 partner 包 | 复用 LangChain | LiteLLM | 多家 chat client（OpenAI / Azure 等） | LiteLLM | 仅 OpenAI 一等 | LiteLLM |
| 工具系统 | `@agent.tool` 自动 schema | `@tool` + Pydantic args | 复用 LangChain | `Tool` 类 | 函数工具 | `FunctionTool` | `function_tool` | 任意 Python 函数 |
| 流式 | `agent.run_stream()` + 结构化流 | `chain.stream()` + LCEL | `app.stream()` | 一般 | 支持 | 一般 | 支持 | 一般 |
| 多 Agent | tool 调 Agent / `SubAgents` / Graph | `create_agent` 子 agent / LangGraph | 一等公民 | 一等公民 | 一等公民（sequential / concurrent / handoff / group collaboration） | step 间传递 | `handoff` | code agent 互调 |
| 可观测性 | Logfire / 任意 OTel 一行接入 | LangSmith | LangSmith | 第三方 | OpenTelemetry | 第三方 | 内置 tracing（默认上报 OpenAI 平台） | 第三方 |
| 评测 | `pydantic-evals` 同厂 | LangSmith Eval | LangSmith Eval | 第三方 | 第三方 | 第三方 | SDK 不带评测框架，OpenAI 平台有 Evals | 第三方 |
| 生态 | 新，集成少 | 最广，1000+ 集成 | 复用 LangChain | 中等，模板多 | 新，偏微软 / Azure 生态 | 文档/检索最强 | 新 | 新 |
| 学习曲线 | 平缓 | 陡（积木多） | 中（要画图） | 平缓 | 中 | 中 | 平缓 | 平缓 |
| 社区活跃 | 高速增长 | 稳居第一 | 高速增长 | 稳定 | 微软主推 | RAG 主战场 | OpenAI 加持 | 增长中 |

---

## 4. Pydantic AI 的强项与弱项

### 4.1 强项

| 项 | 说明 |
|----|------|
| 类型安全 | `Agent[DepsType, OutputType]` 是真泛型，IDE 跳转 / 补全 / mypy 全通 |
| 声明式输出 | `output_type=Invoice` 一行就包含了 schema、校验和重试，比 LangChain 的 `with_structured_output` 做得更完整 |
| 依赖注入 | `deps_type` + `RunContext` 像 FastAPI Depends 一样，把 DB / HTTP Client / 用户上下文注入工具 |
| Logfire | 同一团队出品，`logfire.configure()` 之后就能看到完整的调用链路、token、cost、retry 树 |
| 生产可用 | retry、timeout、并发控制都已经内置，不用自己再包一层 |
| API 稳定 | 有明确的版本策略。但从 0.0.x 到 1.x 再到 2.0 经历了两次大版本，不少 API 改了名字或位置，稳定要从 2.x 算起 |
| capability 扩展原语 | V2 起工具 / hooks / 指令 / 设置打包成一个单元，可按需加载、可 YAML 声明；官方 harness 包 80+ 现成能力 |
| 持久执行 | Temporal / DBOS / Prefect / Restate 等集成，agent 能扛过重启 |
| 模型无关 | 切换 model 改一行字符串 `"openai:gpt-6.1-sol"` → `"anthropic:claude-haiku-5-5"` |

### 4.2 弱项

| 项 | 说明 |
|----|------|
| 生态相对新 | 2024-12 才发布，第三方集成不如 LangChain 多（向量库 / loader 要自己拼；V2 有 `Embedder` 和 MCP 缓解） |
| 多 Agent 不像 CrewAI 那样开箱即用 | 需要用 `SubAgents` / 工具调 Agent / Graph 编排（harness 里已有现成的，但不是「角色 + 任务」的模板化体验） |
| 没有 chain 那种轻量的线性管道 | 简单的 prompt → model → parser 三行就能写完，但如果要大量写 LangChain LCEL 那样的链式组合，会显得啰嗦 |
| 社区案例还在积累 | 能直接参考的 demo 不如 LangChain 多 |
| 本地模型支持 | Ollama / vLLM 有专门前缀，但小模型工具调用质量参差 |
| harness 仍是 0.x | `pydantic-ai-harness` 小版本之间可能有 API 变化 |

---

## 5. 三种常见场景的选型推荐

### 场景 1：简单 Chat / 单 Agent 工具调用

需求：客服、内部小助手、Slack bot 这类，单个 Agent 调 3-5 个工具就够了。

| 选项 | 评分 | 理由 |
|------|------|------|
| Pydantic AI | 5/5 | 不带依赖注入和结构化输出的最小版本不到 10 行（带上这两样的完整版见第 7 节，26 行），类型安全，直接接 Logfire |
| LangChain | 4/5 | LCEL 三件套也很简洁，但 structured output 不如 Pydantic AI 干净 |
| LangGraph | 2/5 | 对这种需求太重了 |
| CrewAI | 1/5 | 单 Agent 用不上 Crew 这层抽象 |
| OpenAI Agents SDK | 4/5 | 简洁，但绑定 OpenAI |

推荐 Pydantic AI。

### 场景 2：复杂多 Agent 工作流（研究 → 起草 → 审核 → 发布）

需求：流程有分支、并行、人审、可中断恢复。

| 选项 | 评分 | 理由 |
|------|------|------|
| Pydantic AI + Graph | 4/5 | 状态用 `dataclass`，节点用 `BaseNode`，类型化；要中断恢复得接持久执行引擎（Temporal / DBOS 等） |
| LangGraph | 5/5 | 显式状态机，HITL / Checkpointer 成熟，Studio 调试 |
| LangChain | 2/5 | 这种复杂度要上 LangGraph |
| CrewAI | 3/5 | 简单流程很快，复杂分支吃力 |
| Microsoft Agent Framework | 未评 | 有图式 workflow、检查点和 HITL，适合这类流程；Python API 还比较新。它的前身 AutoGen 以群聊为主，不适合流水线 |
| LlamaIndex Workflows | 4/5 | 事件驱动很优雅 |

全新项目、看重类型安全的，推荐 Pydantic AI + pydantic-graph；已经在 LangChain 体系里的，用 LangGraph，生态成熟，Studio 也好用。

### 场景 3：已有 LangChain 大量代码，团队想试试 Pydantic AI

不要重写，LangChain 和 Pydantic AI 完全可以共存：

```python
# 在 LangChain 项目里新加的模块用 Pydantic AI
from pydantic_ai import Agent
new_agent = Agent("openai-chat:qwen-flash", output_type=Invoice)

# 旧 LangChain Chain 保持原样
from langchain_openai import ChatOpenAI
old_chain = prompt | ChatOpenAI(model="gpt-6-luna") | StrOutputParser()
```

迁移的节奏：新模块先用 Pydantic AI，老模块不动；老 chain 的改动正好要碰 `with_structured_output` 或 Agent 时，顺手切过去；全量替换放到最后，先让团队熟悉起来。

---

## 6. 选型决策树

```
你的需求是什么？

├── 单次结构化抽取（不要 Agent 循环）
│   ├─ Pydantic AI：output_type 即可
│   └─ LangChain：with_structured_output
│      （两者都够用，按团队现有技术栈选）
│
├── 单 Agent + 工具调用（< 10 工具）
│   ├─ 首选：Pydantic AI
│   ├─ 可选：OpenAI Agents SDK（锁定 OpenAI 可接受时）
│   └─ 可选：LangChain create_agent
│
├── 多 Agent / Workflow
│   ├─ 流水线 / 状态机
│   │   ├─ 首选：LangGraph（HITL、checkpointer 现成）
│   │   └─ 可选：Pydantic AI + Graph（类型化，中断恢复要接持久执行引擎）
│   ├─ 角色化协作（CEO/Manager/Worker）
│   │   └─ 首选：CrewAI
│   └─ 群聊式头脑风暴
│       └─ 首选：Microsoft Agent Framework（group collaboration；AutoGen 已归档）
│
├── RAG 重型项目
│   ├─ 首选：LlamaIndex
│   └─ 可选：Pydantic AI + 自拼 retriever
│
└── 已有 LangChain 体系
    └─ 保留 LangChain，新模块用 Pydantic AI
```

「首选 / 可选」只在同一分支内比较；各框架在具体场景下的打分和理由见第 5 节。

---

## 7. 同一个 Agent 用两个框架各写一遍

任务是查天气：Agent 收到城市名后调用 `get_weather` 工具，返回结构化的 `WeatherReport(city, temp_c, condition)`。

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
    "openai-chat:qwen-flash",
    deps_type=Deps,
    output_type=WeatherReport,
    instructions="你是天气助手，必须调用 get_weather 工具拿数据，不要自己编。",
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

不算空行一共 26 行，包含了依赖注入（`Deps`）、从函数签名和 docstring 自动生成的工具 schema、结构化输出（`output_type=WeatherReport`），以及自动校验（模型填的结构校验不通过时，错误信息会回传给模型让它重试）。

### 7.2 LangChain 1.x 版

```python
import os
from pydantic import BaseModel
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

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

agent = create_agent(
    ChatOpenAI(model="qwen-flash", temperature=0, base_url=os.getenv("OPENAI_BASE_URL")),
    tools=[get_weather],
    system_prompt="你是天气助手，必须调用 get_weather 工具拿数据，不要自己编。",
    response_format=WeatherReport,
)
result = agent.invoke({"messages": [{"role": "user", "content": "北京天气怎么样？"}]})
print(result["structured_response"])
# WeatherReport(city='北京', temp_c=26.0, condition='晴')
```

不算空行 23 行，输出和 Pydantic AI 版相同。早期的 LangChain 写法要手写 ReAct while 循环，再用 `with_structured_output` 做第二步；LangChain 1.x 把这两件事都收进了 `create_agent`：工具循环是内置的，`response_format` 在循环结束时产出结构化结果。所以在这个最小例子上，两边的代码量已经差不多。`Runnable` / LCEL 并没有消失：它仍是 LangChain 组件之间的底层组合方式，只是写 agent 时的入口换成了 `create_agent`。

差别现在在这些地方：

- Pydantic AI 的 `deps_type` / `RunContext` 让「工具要什么依赖」有类型检查；LangChain 的对应物是 `context_schema` + `ToolRuntime`（也是运行时注入一个带类型的上下文）；
- 扩展方式：Pydantic AI 是 capability；LangChain 是 middleware（摘要、HITL、护栏、重试、调用上限……）+ LangGraph 生态；
- 状态与持久化：LangChain 的 `create_agent` 底层是 LangGraph，checkpointer / interrupt / store 现成；Pydantic AI 状态是显式的消息历史，持久化靠你、`StepPersistence` 或持久执行引擎；
- 输出校验反馈：Pydantic AI 的 `ModelRetry` / 输出校验失败回喂默认就通；LangChain 里要看 `ToolStrategy` 的错误处理和 `ToolErrorMiddleware`。

### 7.3 对比小结

| 维度 | Pydantic AI | LangChain |
|------|-------------|-----------|
| 代码行数（不算空行） | 26（多出来的是 `Deps`） | 23 |
| 工具循环 | 内置 | 内置（`create_agent`） |
| 结构化输出 | `output_type`（默认走输出工具） | `response_format`（按模型能力选 provider / 工具策略） |
| 类型提示 | `Agent[Deps, WeatherReport]`，IDE 能完整识别 | `structured_response` 是 `Any` 级别，需要自己标注 |
| 依赖注入 | `deps_type` / `RunContext` | `context_schema` + `ToolRuntime` |
| 扩展 | capability | middleware |

---

## 8. 从 LangChain 迁移到 Pydantic AI 的对照表

| LangChain | Pydantic AI |
|-----------|-------------|
| `ChatOpenAI(model=...)` / `init_chat_model("openai:...")` | `Agent("openai:...")` |
| `create_agent(..., system_prompt="...")` | `Agent(instructions="...")` |
| `prompt \| model \| StrOutputParser()`（LCEL） | `agent` 本身 |
| `agent.invoke({"messages": [...]})` | `agent.run_sync("...")` |
| `create_agent(response_format=Schema)` / `model.with_structured_output(Schema)` | `Agent(output_type=Schema)` |
| `@tool def foo(...)` + `create_agent(tools=[foo])` | `@agent.tool_plain def foo(...)` / `Agent(tools=[foo])` |
| middleware（摘要 / 重试 / HITL / 护栏） | capability（`ProcessHistory` / `ModelFallback` / `HandleDeferredToolCalls` / harness `Guardrails`） |
| `agent.stream(...)` | `agent.run_stream(...)` / `run_stream_events(...)` |
| `context_schema` + `ToolRuntime` | `deps_type` + `RunContext` |
| LangSmith 跟踪 | `logfire.configure()` + `logfire.instrument_pydantic_ai()` |
| checkpointer（`InMemorySaver` / SQLite / Postgres）+ `thread_id` | `conversation=` / `message_history=` 自己存；或 `StepPersistence` |
| `interrupt` / `HumanInTheLoopMiddleware` | `requires_approval` + `DeferredToolRequests` / `HandleDeferredToolCalls` |
| `LangGraph.StateGraph` 状态机 | `pydantic_graph.GraphBuilder` |
| Deep Agents | `pydantic-ai-harness` 的 `Coder` / `SubAgents` / `Skills` / `Memory` |

### 8.1 迁移建议

不要一次性大重构，建议按这个顺序来：

1. 新功能用 Pydantic AI，老代码不动。
2. 替换结构化抽取，这一步收益最大：`with_structured_output` 换成 `Agent(output_type=...)`。
3. 替换 Agent 循环：`create_agent`（或旧的 `AgentExecutor` / `create_react_agent`）换成 `Agent + @agent.tool`。
4. 替换状态机：`LangGraph` 换成 `pydantic_graph`。pydantic-graph 2.x 没有内置持久化，需要断点续跑的流程，先评估一下持久执行引擎。
5. 替换可观测：`LangSmith` 换成 `Logfire`。
6. 检索器保留。LangChain 的 Retriever / VectorStore 抽象很成熟，Pydantic AI 没有对应的抽象，可以继续用 LangChain 检索器，把它包成一个 Pydantic AI tool：

```python
from pydantic_ai import Agent, RunContext
from dataclasses import dataclass
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

@dataclass
class Deps:
    retriever: Chroma

agent = Agent("openai-chat:qwen-flash", deps_type=Deps)

@agent.tool
async def retrieve(ctx: RunContext[Deps], query: str) -> str:
    """从知识库检索相关文档。"""
    docs = ctx.deps.retriever.similarity_search(query, k=4)
    return "\n\n".join(f"[{i+1}] {d.page_content}" for i, d in enumerate(docs))
```

Pydantic AI 负责 Agent、LangChain 负责检索，这种混合架构现在很常见。

---

## 9. 与 OpenAI Agents SDK 的关系

OpenAI 在 2025 年 3 月也推出了官方的 Agents SDK：

```python
from agents import Agent, Runner, function_tool

@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 26 degrees"

agent = Agent(name="WeatherAgent", instructions="You help with weather.", tools=[get_weather])
result = Runner.run_sync(agent, "What is the weather in Beijing?")
print(result.final_output)
```

看起来和 Pydantic AI 很像，区别在这些地方：

| 维度 | Pydantic AI | OpenAI Agents SDK |
|------|-------------|-------------------|
| 模型支持 | 30 多家 Provider | 原生只支持 OpenAI 协议；千问等兼容端点走 Chat Completions，Claude、Gemini 推荐用 Any-LLM 适配器（LiteLLM 和 openai v3 有依赖冲突） |
| 类型安全 | 一等公民 | 一般 |
| 结构化输出 | `output_type=Schema`，结果在 `result.output` | 同样叫 `output_type`，结果在 `result.final_output` |
| Handoff | 通过 tool 调 Agent | 一等公民 `handoff` |
| Tracing | Logfire / 任意 OTel 后端 | 内置 tracing，默认上报到 OpenAI 平台的 Traces 页面 |
| 评测 | `pydantic-evals` | SDK 本身没有评测框架，用 OpenAI 平台的 Evals |
| 适用 | 想跨厂商、想类型安全 | 重度 OpenAI 用户 |

如果只用 OpenAI，两者都可以，主要看你更喜欢哪边的生态。

---

## 10. 与 Smolagents 的差异

Smolagents（Hugging Face）走的是另一条路：让模型直接写 Python 代码来代替工具调用。

```python
from smolagents import CodeAgent, InferenceClientModel   # 旧名 HfApiModel 已改名
agent = CodeAgent(tools=[], model=InferenceClientModel())
agent.run("计算 (3 + 5) * 7 的结果")
# 模型实际生成 Python 代码运行，而不是 tool_call JSON
```

好处是表达力强，模型能自己组合复杂逻辑；问题是要执行 LLM 生成的代码，沙箱很难做，安全性差。

Pydantic AI 默认走严格 schema 化的 tool call 路线，但 V2 harness 里的 `CodeMode` 同样是让模型写 Python 来编排工具。不同之处在于代码跑在隔离的 Monty 沙箱里，工具调用仍然经过框架的审批、hooks 和用量计数（见[多 Agent 协作模式](../05-patterns/01-multi-agent.md)）。所以这两条路线已经谈不上对立，区别在于有没有沙箱和管控。

---

## 11. 与 CrewAI / Microsoft Agent Framework 的差异

### 11.1 CrewAI

```python
from crewai import Agent, Task, Crew
researcher = Agent(role="资深研究员", goal="收集信息", llm="gpt-6-luna")
writer = Agent(role="科技作家", goal="写 300 字介绍", llm="gpt-6-luna")
task1 = Task(description="研究 {topic}", agent=researcher)
task2 = Task(description="基于上一任务写介绍", agent=writer, context=[task1])
crew = Crew(agents=[researcher, writer], tasks=[task1, task2])
crew.kickoff(inputs={"topic": "Pydantic AI"})
```

CrewAI 把 Agent 看成有角色的成员，流程由 Task 的依赖关系自动调度；Pydantic AI 把 Agent 看成一个函数（输入 → 工具循环 → 输出），流程要显式写出来（用 Graph，或者在工具里调用别的 Agent）。

如果你要模拟一组虚拟员工（市场分析师、产品经理、工程师互相对话），或者不在意代码结构、只想快速跑通流程，CrewAI 更合适。如果是要长期维护的工程，需要 IDE 跳转和单元测试，或者你已经在用 FastAPI、pydantic-settings 这些 Pydantic 系的库，选 Pydantic AI。

### 11.2 AutoGen 与 Microsoft Agent Framework

AutoGen 仓库已经标注为归档、不再维护，微软建议新用户改用 Microsoft Agent Framework（MAF）。存量项目里常见的是 AutoGen 0.4 的群聊写法：

```python
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat

researcher = AssistantAgent("researcher", model_client=model, system_message="搜资料")
writer = AssistantAgent("writer", model_client=model, system_message="根据资料写 200 字")
team = RoundRobinGroupChat([researcher, writer], max_turns=4)
result = await team.run(task="主题：Pydantic AI")
```

AutoGen 的核心是多 Agent 群聊，所有 Agent 在同一个会话里轮流发言，适合反思、批评、头脑风暴，不适合线性流水线。MAF 保留了这种群聊式协作（group collaboration），另外加了图式 workflow、检查点、HITL，以及 sequential / concurrent / handoff 几种编排模式，官方提供从 AutoGen 迁移的指南。MAF 的 Python API 请看 `microsoft/agent-framework` 仓库的 quickstart，这里不贴代码。

和 Pydantic AI 比，MAF 把多 Agent 编排做成了框架内置的模式；Pydantic AI 用工具调 Agent、`SubAgents` 或 Graph 显式写出流程。

---

## 12. 团队选型建议表

| 团队场景 | 推荐 |
|----------|------|
| 学习 / 个人项目 | Pydantic AI（顺带把 Pydantic 也学了） |
| 初创快速验证 MVP | CrewAI 或 Pydantic AI |
| 中型生产项目、长期维护 | Pydantic AI + Logfire |
| 大厂自托管 + 完整 HITL / 持久化 | LangGraph + Self-host LangSmith；或 Pydantic AI + Graph + 持久执行引擎 |
| 已重度依赖 LangChain | 保留 + 新模块 Pydantic AI |
| 主要做 RAG | LlamaIndex（重检索）或 Pydantic AI + LangChain 检索器（混合） |
| 锁定 OpenAI 生态 | OpenAI Agents SDK |
| 多 Agent 群聊讨论、微软 / .NET 生态 | Microsoft Agent Framework（原来用 AutoGen 的也迁到这里） |

---

## 13. 常见误区

| 误区 | 实际情况 |
|------|------|
| Pydantic AI 是 LangChain 的轻量替代 | 两者抽象方式不同。LangChain 是一堆可组合的积木，Pydantic AI 是声明式的 Agent |
| 选 Pydantic AI 就不能用 LangChain 工具了 | 可以混用。Pydantic AI Agent 里完全可以包 LangChain Retriever |
| 类型安全等于编译期保证 | Python 是动态类型，这里说的类型安全是 IDE 提示加 Pydantic 运行时校验加 mypy |
| Logfire 必须付费 | SDK 开源，托管平台有免费额度；平台本身闭源，自托管属于企业版 |
| Pydantic AI 没有 LCEL，少了一块 | LCEL 是用来把 LangChain 的细粒度组件串起来的，Pydantic AI 没有拆得那么细，所以不需要这层胶水 |
| 多 Agent 必须用 CrewAI / MAF | Pydantic AI 用 Graph 也能做，类型还更安全 |
| LangChain 必须升级到 LangGraph | 简单线性 chain 不需要，复杂状态机才需要 |

---

## 14. 一张图看定位

```
                  抽象层级
                     ▲
高（自动调度）       │  CrewAI
                     │
                     │  MS Agent Framework
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

Pydantic AI 的位置是中等抽象层级，同时类型安全程度高。

---

## 15. 本章 demo

[`demos/practice/01_vs_langchain.py`](../../demos/practice/01_vs_langchain.py)：同一个"查天气"Agent 用 Pydantic AI 和 LangChain 各写一遍，可以直接运行对比。

```bash
python demos/practice/01_vs_langchain.py
```

---

下一篇：[RAG 知识库问答 Agent](02-project-rag.md)，做一个能引用文档作答的问答 Agent。
