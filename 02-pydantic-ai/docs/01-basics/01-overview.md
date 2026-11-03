# Pydantic AI 01：整体定位与生态全景

> Pydantic AI 是 Pydantic 团队继 FastAPI 之后做的 Python Agent 框架，卖点是类型安全、不绑定模型、能直接上生产。它想把 FastAPI 那种"代码少、IDE 提示全、跑起来心里有底"的体验搬到 LLM 应用上。2026 年 6 月发布了稳定版 V2，capability（能力）成了核心扩展原语。这套手册按 2026 年 11 月的 `pydantic-ai 2.64.0` 校对。

V1 时代的代码升级到 V2，有几处会直接报错或出警告：`agent.tool` 以外的扩展点基本都收进了 `capabilities=[...]`；`result.usage()` 改成属性 `result.usage`；`RetryPromptPart` 被废弃（重试反馈改为 `ToolReturnPart(outcome="retried")` / `RetryFeedbackPart`）；`pydantic_graph` 改用 `GraphBuilder`；MCP 客户端改成基于 FastMCP 的 `MCPToolset`；`mcp.server.fastmcp` 在 `mcp` 2.x 里已更名。后面各篇碰到这类变化会在原地标出。

---

## 1. Pydantic AI 是什么

先看它从哪来：

- Pydantic 是 Python 数据校验的事实标准，OpenAI、Anthropic、Google、LangChain 的 SDK 内部都依赖它
- FastAPI 把 Pydantic 用到 Web 后端，声明了类型就自动得到校验和文档
- Pydantic AI 是同一个团队的新项目，把这套做法搬到 LLM Agent：你声明 `output_type=Invoice`，Agent 就保证返回一个 `Invoice` 对象

官方的说法是 "that FastAPI feeling to GenAI app and agent development"。

所以它和 LangChain 走的路线不一样，有很明确的取舍：

1. Python 原生：用类型注解、dataclass、Pydantic Model，不另造 DSL
2. 不绑定模型：OpenAI / Anthropic / Gemini / Bedrock / Groq / Mistral / Cohere / Hugging Face / Ollama / OpenRouter / xAI / Cerebras 等 30 多家 provider，切换只改一个字符串
3. 面向生产：原生 OpenTelemetry（Logfire 一行接入）、多层重试、超时与取消、依赖注入、评测框架（Evals）、类型化状态机（Graph）、持久执行（Temporal / DBOS / Prefect 等）
4. 可组合：V2 起 capability 是统一的扩展单元，工具、hooks、指令、模型设置打包在一起，用 `capabilities=[...]` 挂到任意 Agent 上；官方 harness 包里有 80 多个现成的

---

## 2. 为什么不直接用 OpenAI SDK

裸调 SDK 的话，下面五件事你迟早都要自己写一遍：

1. 结构化输出：把字符串解析成业务对象，校验失败时重试
2. 工具调用循环：模型回 tool_call → 你执行 → 拼 tool_result → 再请求模型 → 直到没有 tool_call
3. 多 Provider 适配：OpenAI 用 `tools`，Anthropic 用 `tool_use`，Gemini 又有自己的 JSON Schema 方言
4. 对话历史持久化：每家消息格式都不同，存数据库还得手写序列化
5. 可观测：tokens、时延、重试、失败原因，都得自己埋点

Pydantic AI 把这些都收进了 `Agent` 这一个类：

```python
from pydantic_ai import Agent
from pydantic import BaseModel

class Invoice(BaseModel):
    amount: float
    vendor: str
    date: str

agent = Agent("openai-chat:qwen-flash", output_type=Invoice)  # 本手册 demo 用千问的 OpenAI 兼容端点；换 "openai:gpt-6.1-sol" 之类即可
result = agent.run_sync("发票：阿里云 2024-01-15 ¥1280")
print(result.output)  # Invoice(amount=1280.0, vendor='阿里云', date='2024-01-15')
```

不算空行 9 行代码，Schema 生成、function calling、结果校验、失败重试、token 计数都有了。

---

## 3. 特性一览

| 特性 | Pydantic AI 实现 | 说明 |
|------|------------------|------|
| 类型安全 | `Agent[DepsType, OutputType]` 泛型 | 静态类型检查，IDE 跳转 / 补全 |
| 模型无关 | 统一 `Model` 抽象 + 30 多家 Provider | 字符串简写：`"provider:model-name"` |
| 结构化输出 | `output_type=PydanticModel` | 自动 Schema、自动校验，校验失败把错误回传给模型重试 |
| 工具调用 | `@agent.tool` 装饰器 | 从函数签名自动生成 JSON Schema |
| 依赖注入 | `deps_type` + `RunContext` | 把 DB、HTTP 客户端、用户上下文注入工具 |
| 流式响应 | `agent.run_stream()` | 文本流 + 结构化流式校验 |
| 可扩展 | `capabilities=[...]`（V2 核心原语） | 工具 + hooks + 指令 + 模型设置一起打包，可按需加载 |
| 可观测性 | OpenTelemetry；Logfire 一行接入 | Span / Token / Cost / 重试链路 |
| 评测 | `pydantic-evals` 独立包 | Dataset + Evaluator + Judge，离线与线上评估 |
| 多步编排 | `pydantic-graph` 独立包 | 类型化 State + `GraphBuilder`（节点 / 步骤 / 并行 / join） |
| 协议互通 | MCP（`MCPToolset` / `MCP` capability）、AG-UI、Vercel AI | 接入外部工具与前端 |
| 持久执行 | Temporal / DBOS / Prefect 等 | Agent 能扛过重启，跑几天 |

---

## 4. 与同类框架对比

| 维度 | Pydantic AI | LangChain | LangGraph | CrewAI | LlamaIndex |
|------|----------------|-----------|-----------|--------|-----------|
| 核心定位 | Agent 框架 | LLM 应用框架 | 状态机编排 | 多 Agent 协作 | RAG / 数据框架 |
| 类型安全 | 一等公民 | 部分支持（with_structured_output） | 有（TypedDict State） | 弱（Task 可设 `output_pydantic`） | 部分支持 |
| 学习曲线 | 平缓 | 陡（积木多） | 中（要会画图） | 平缓 | 中 |
| 结构化输出 | `output_type=Model` | `create_agent(response_format=Model)` / `.with_structured_output` | 同 LangChain | `output_pydantic` | `as_structured_llm` / `structured_predict` |
| 工具系统 | `@agent.tool` 函数签名自动转 | `@tool` + Pydantic args | 同 LangChain | `Tool` 类 | `FunctionTool` |
| 模型抽象 | 30 多家厂商统一接口 | 数十个 partner 包 | 复用 LangChain | 通过 LiteLLM | 通过 LiteLLM |
| 可观测性 | Logfire（同厂出品） | LangSmith | LangSmith | 第三方 | 第三方 |
| 多 Agent | 工具调用其他 Agent | 用 LangGraph | 一等公民 | 一等公民 | `AgentWorkflow` |
| 适合场景 | 想要 IDE 友好 / 类型安全 / 生产可用 | 生态广、组件多 | 复杂状态机、流程审批 | 角色化协作（CEO/CTO 等） | 文档问答、检索 |

怎么选，我的看法是：

- 想要类型安全的 SDK 体验、不绑死模型、生产级可观测：Pydantic AI
- 已经在 LangChain 生态里，需要大量现成的 chain / retriever：LangChain
- 长流程，有审批、中断、重启：LangGraph
- 角色扮演式的多 Agent 协作：CrewAI
- 主要做 RAG：LlamaIndex（或 Pydantic AI 加自己拼的 Retriever）

---

## 5. 生态全景

Pydantic AI 是一组独立项目，不止一个包：

```
pydantic-ai            ← 主包：pydantic-ai-slim + OpenAI / Anthropic / Google provider + CLI / MCP / Logfire 等依赖
├── pydantic-ai-slim   ← 精简包，按需 extras
│   ├── [openai] [anthropic] [google] [bedrock] [groq] [mistral] [cohere] ...   ← 各家 Provider
│   ├── [mcp]          ← MCP 客户端（基于 FastMCP）
│   ├── [evals]        ← pydantic-evals
│   ├── [logfire]      ← 可观测性
│   └── [retries] [duckduckgo] [web] [web-fetch] ...                            ← 可选集成
│
pydantic-ai-harness    ← 官方 capability / harness 库：Coder、Researcher、Skills、Memory、SubAgents、StepPersistence …（0.x，独立发版）
pydantic-evals         ← 评测框架（Dataset / Evaluator / LLM Judge / 线上评估）
pydantic-graph         ← 类型化状态机（GraphBuilder）
logfire                ← 可观测性平台（Pydantic 团队同款，OpenTelemetry 底子）
clai                   ← 命令行（独立包）
```

各包的分工：

| 包 | 定位 | 何时用 |
|----|------|--------|
| pydantic-ai | Agent 框架（V2 以 capability 为核心） | 主线 |
| pydantic-ai-harness | 官方 capability 库 + 完整 agent（Coder / Researcher） | 要工作区、技能、记忆、子 agent、持久执行时 |
| pydantic-evals | Eval 框架 | 上线前回归、Prompt A/B、线上抽样评估 |
| pydantic-graph | 状态机 | 控制流本身就是难点：多步骤 / 含人工节点 / 需要中断恢复（配合持久执行引擎） |
| logfire | 观测平台 | 生产部署建议都接上 |
| Pydantic AI Gateway | 一个 key 访问所有模型 + 成本监控 | 想统一管模型与预算时 |

可以只用 pydantic-ai 不碰其他包，也可以只用 pydantic-graph 不用 pydantic-ai（它不依赖 pydantic-ai）。

---

## 6. Hello World

最小可运行例子：

```python
# demos/basics/01_overview.py
from dotenv import load_dotenv
from pydantic_ai import Agent

load_dotenv()

agent = Agent(
    "openai-chat:qwen-flash",   # 千问（OpenAI 兼容端点，靠 OPENAI_API_KEY + OPENAI_BASE_URL）
    instructions="你是一位简洁的助手，一句话回答。",
)

result = agent.run_sync("Python 的 GIL 是什么？")
print(result.output)
```

这几行里有几处值得说明：

1. `"openai-chat:qwen-flash"` 是字符串简写，等价于 `OpenAIChatModel("qwen-flash")`。`openai:` 前缀在 V2 默认走 OpenAI 的 Responses API，而第三方 OpenAI 兼容端点（千问、DeepSeek……）一般只实现了 Chat Completions，所以要用 `openai-chat:`
2. `instructions` 直接传字符串，不需要 `ChatPromptTemplate`；官方推荐用它代替 `system_prompt`（区别见[第一个 Agent](03-first-agent.md)）
3. `run_sync` 返回 `AgentRunResult`，`.output` 是模型最终回复（这里是 `str`）；用量是属性 `result.usage`（V2 起不再是方法 `usage()`）
4. API Key 从 `OPENAI_API_KEY` 环境变量读，`.env` 由 `python-dotenv` 加载

### vs LangChain

```python
# LangChain 1.x 等价
from langchain.agents import create_agent

agent = create_agent("openai:gpt-6.1-sol", tools=[], system_prompt="你是一位简洁的助手，一句话回答。")
result = agent.invoke({"messages": [{"role": "user", "content": "Python 的 GIL 是什么？"}]})
print(result["messages"][-1].content)
```

LangChain 1.x 之后主推 `create_agent`（旧的 `prompt | model | parser` LCEL 链还能用，但已经不是写 agent 的主线）。两边在最小例子上已经很像了，差别要到后面才显出来：Pydantic AI 的类型（`deps_type` / `output_type`）是运行时约束，扩展靠 capability；LangChain 的扩展靠 middleware 和 LangGraph 生态。

---

## 7. 再进一步：带工具的 Agent

```python
from pydantic_ai import Agent, RunContext

agent = Agent("openai-chat:qwen-flash", instructions="你是一位天气助手。")

@agent.tool_plain
def get_weather(city: str) -> str:
    """查询城市当前天气"""
    fake_db = {"北京": "晴 26°C", "上海": "多云 24°C"}
    return fake_db.get(city, "未知")

result = agent.run_sync("北京和上海的天气分别怎么样？")
print(result.output)
# 模型自己决定调两次 get_weather，再合成自然语言回复
```

- `@agent.tool_plain` 表示不需要 RunContext，函数签名直接给 LLM 看
- `@agent.tool` 表示需要 RunContext，第一个参数必须是 `ctx: RunContext[...]`
- docstring 会作为工具描述交给模型，类型注解自动转成 JSON Schema

---

## 8. 谁在用 Pydantic AI

Pydantic AI 2024 年 12 月首次发布，2026 年 6 月 23 日发布稳定版 V2（官方的说法是「V2 转向 harness 优先的设计，capability 成为核心原语」）。官方文档有一页「Built with Pydantic AI」，列了开源应用、开发者工具、领域 agent 和企业案例，想看实际用法可以去那里翻。

背后是 Pydantic 团队。Pydantic 本身是 OpenAI SDK、Anthropic SDK、Google ADK、LangChain 等大半个 AI 生态的校验层，所以类型与校验这块是它的老本行。

---

## 9. 学习路径建议

| 阶段 | 章节 | 重点 |
|------|------|------|
| 入门 | [整体定位与生态全景](01-overview.md) → [第一个 Agent](03-first-agent.md) → [模型与 Provider 全览](04-models-providers.md) | 跑通 Hello World |
| 核心 | [依赖注入（Dependencies）](05-dependencies.md) → [结构化输出（Output Types）](06-output-types.md) → [消息与对话历史（Messages & History）](07-messages-history.md) | 写一个有状态的 Agent |
| 工具 | 工具部分全部 5 篇（从[Function Tools 函数工具](../02-tools/01-function-tools.md)开始） | 让 Agent 能干活 |
| 进阶 | [流式](../03-advanced/01-streaming.md) / [多模态](../03-advanced/02-multimodal.md) / [Hooks](../03-advanced/04-hooks.md) / [Capability](../03-advanced/06-capabilities.md) | 性能与体验、可扩展 |
| 生产 | [Logfire](../04-modules/04-logfire.md) / [Evals](../04-modules/02-evals.md) / [Graph](../04-modules/03-graph.md) | 上线前要会的 |
| 模式 | [多 Agent](../05-patterns/01-multi-agent.md) / [Web](../05-patterns/02-web-chat-ui.md) / [测试](../05-patterns/03-testing.md) | 架构层 |

---

## 10. 常见误区

| 误区 | 实际情况 |
|------|------|
| Pydantic AI 是 Pydantic 的子模块 | 它是独立项目，需要单独 `pip install pydantic-ai` |
| 只能用 Pydantic Model 当输出 | 也能用 `str` / `list` / `Union` / `TypedDict` / dataclass |
| 必须用 OpenAI | 内置 30 多家 Provider，OpenAI 兼容端点（千问、DeepSeek 等）、OpenRouter 也都能接 |
| Agent = LLM 一次调用 | Agent 内部是工具调用循环，可能调几十次模型 |
| Pydantic AI 锁定 Logfire | Logfire 是推荐但非必需，底层是 OpenTelemetry，可接任意 OTel 后端 |
| 没有 LCEL 那么灵活 | 抽象层次不同：LangChain 是积木加胶水（现在主推 create_agent + middleware），Pydantic AI 是声明式 Agent 加 capability |
| TestModel 只能测连通性 | `TestModel` 可以模拟任意输出，`FunctionModel` 可以模拟工具调用序列 |

---

## 11. 本章 demo

完整可运行代码：[`demos/basics/01_overview.py`](../../demos/basics/01_overview.py)

下一章：[Pydantic AI 02：安装、依赖与环境](02-installation.md)。
