# Pydantic AI 01：整体定位与生态全景

> **一句话**：Pydantic AI 是 Pydantic 团队继 FastAPI 之后推出的"Python 原生 Agent 框架"，主打**类型安全、模型无关、生产可用**，目标是把 FastAPI 那种"少写代码、IDE 提示完善、跑起来就靠谱"的体验复刻到 GenAI 应用上。

---

## 1. Pydantic AI 是什么

要回答这个问题，先看背景：

- **Pydantic** 是 Python 数据校验事实标准，被 OpenAI / Anthropic / Google / LangChain 全部 SDK 内部依赖
- **FastAPI** 把 Pydantic 用在 Web 后端，让"声明类型 = 自动校验 + 自动文档"
- **Pydantic AI** 是同一个团队的新作，把这套哲学搬到 LLM Agent：你声明 `output_type=Invoice`，Agent 就保证返回一个 `Invoice` 对象

官方原话是 "**that FastAPI feeling to GenAI app and agent development**"。

所以它不是又一个 LangChain，而是带着明确审美主张的工具：

1. **Python 原生**：用类型注解、dataclass、Pydantic Model，不发明新 DSL
2. **模型无关**：OpenAI / Anthropic / Gemini / Groq / Mistral / Cohere / Bedrock / Hugging Face / Ollama / OpenRouter / xAI / Cerebras 等 15+ 厂商，切换只改一行
3. **生产可用**：自带 Logfire 可观测性、retries、超时、依赖注入、评测框架（Evals）、状态机（Graph）

---

## 2. 为什么不直接用 OpenAI SDK

裸调 SDK 你会反复造五个轮子：

1. **结构化输出**：把字符串解析成业务对象、校验失败时重试
2. **工具调用循环**：模型回 tool_call → 你执行 → 拼 tool_result → 再请求模型 → 直到没有 tool_call
3. **多 Provider 适配**：OpenAI 用 `tools`，Anthropic 用 `tool_use`，Gemini 又一套 JSON Schema 方言
4. **对话历史持久化**：每家消息格式都不一样，存数据库还要手写序列化
5. **可观测**：tokens / 时延 / 重试 / 失败原因，自己埋点

Pydantic AI 把这些抽成了 `Agent` 这一个核心类：

```python
from pydantic_ai import Agent
from pydantic import BaseModel

class Invoice(BaseModel):
    amount: float
    vendor: str
    date: str

agent = Agent("openai:gpt-4o", output_type=Invoice)
result = agent.run_sync("发票：阿里云 2024-01-15 ¥1280")
print(result.output)  # Invoice(amount=1280.0, vendor='阿里云', date='2024-01-15')
```

7 行代码，自动包含：Schema 生成、function calling、结果校验、失败重试、token 计费。

---

## 3. 核心特性矩阵

| 特性 | Pydantic AI 实现 | 说明 |
|------|------------------|------|
| **类型安全** | `Agent[DepsType, OutputType]` 泛型 | 静态类型检查，IDE 跳转 / 补全 |
| **模型无关** | 统一 `Model` 抽象 + 15+ Provider | 字符串简写：`"openai:gpt-4o"` |
| **结构化输出** | `output_type=PydanticModel` | 自动 Schema、自动校验、失败 ModelRetry |
| **工具调用** | `@agent.tool` 装饰器 | 自动从函数签名生成 JSON Schema |
| **依赖注入** | `deps_type` + `RunContext` | 把 DB、HTTP 客户端、用户上下文注入工具 |
| **流式响应** | `agent.run_stream()` | 文本流 + 结构化流式校验 |
| **可观测性** | Logfire 一行接入 | Span / Token / Cost / 重试链路 |
| **评测** | `pydantic-evals` 独立包 | Dataset + Evaluator + Judge |
| **多步编排** | `pydantic-graph` 独立包 | 类型化 State + Node 状态机 |
| **协议互通** | MCP / A2A 原生支持 | 接入外部工具/Agent |

---

## 4. 与同类框架对比

| 维度 | **Pydantic AI** | LangChain | LangGraph | CrewAI | LlamaIndex |
|------|----------------|-----------|-----------|--------|-----------|
| 核心定位 | Agent 框架 | LLM 应用框架 | 状态机编排 | 多 Agent 协作 | RAG / 数据框架 |
| 类型安全 | ✅ 一等公民 | ⚠️ 部分（with_structured_output） | ✅ TypedDict State | ❌ | ⚠️ |
| 学习曲线 | 平缓 | 陡（积木多） | 中（要会画图） | 平缓 | 中 |
| 结构化输出 | `output_type=Model` | `.with_structured_output(Model)` | 同 LangChain | 一般 | 通过 LLM Predictor |
| 工具系统 | `@agent.tool` 函数签名自动转 | `@tool` + Pydantic args | 同 LangChain | `Tool` 类 | `FunctionTool` |
| 模型抽象 | 15+ 厂商统一接口 | 数十个 partner 包 | 复用 LangChain | 通过 LiteLLM | 通过 LiteLLM |
| 可观测性 | Logfire（同厂出品） | LangSmith | LangSmith | 第三方 | 第三方 |
| 多 Agent | 工具调用其他 Agent | 用 LangGraph | 一等公民 | 一等公民 | 通过 Agent Runners |
| 适合场景 | 想要 IDE 友好 / 类型安全 / 生产可用 | 生态广、组件多 | 复杂状态机、流程审批 | 角色化协作（CEO/CTO 等） | 文档问答、检索 |

**简短结论**：

- 你想要"**类型安全的 SDK 体验 + 不绑死任何模型 + 生产级可观测**" → Pydantic AI
- 你已经在用 LangChain 生态、需要大量预制 chain / retriever → LangChain
- 你做长流程、有审批 / 中断 / 重启 → LangGraph
- 你做"角色扮演型多 Agent 协作" → CrewAI
- 你主要在做 RAG → LlamaIndex（或 Pydantic AI + 自己拼 Retriever）

---

## 5. 生态全景

Pydantic AI 不是一个包，而是一组独立项目：

```
pydantic-ai          ← 主包（包含 logfire）
├── pydantic-ai-slim ← 精简包，按需 extras
│   ├── [openai]     ← OpenAI Provider
│   ├── [anthropic]  ← Anthropic
│   ├── [google]     ← Gemini
│   ├── [groq]       ← Groq
│   ├── [mistral]    ← Mistral
│   ├── [bedrock]    ← AWS Bedrock
│   ├── [cohere]     ← Cohere
│   ├── [mcp]        ← Model Context Protocol
│   └── [logfire]    ← 可观测性
│
pydantic-evals       ← 评测框架（Dataset / Evaluator / LLM Judge）
pydantic-graph       ← 类型化状态机（多步骤工作流）
logfire              ← 可观测性平台（Pydantic 团队同款）
mcp                  ← MCP 客户端 / 服务端
```

三个姊妹包的关系：

| 包 | 定位 | 何时用 |
|----|------|--------|
| **pydantic-ai** | Agent 框架 | 主线 |
| **pydantic-evals** | Eval 框架 | 上线前回归、Prompt A/B |
| **pydantic-graph** | 状态机 | 多步骤 / 含人工节点 / 可恢复 |
| **logfire** | 观测平台 | 任何生产部署都该上 |

可以**只用 pydantic-ai 不碰其他**，也可以**只用 pydantic-graph 不碰 pydantic-ai**（虽然两者一起用最爽）。

---

## 6. Hello World

最小可运行例子（30 秒跑通）：

```python
# demos/basics/01_overview.py
from dotenv import load_dotenv
from pydantic_ai import Agent

load_dotenv()

agent = Agent(
    "openai:gpt-4o-mini",
    system_prompt="你是一位简洁的助手，一句话回答。",
)

result = agent.run_sync("Python 的 GIL 是什么？")
print(result.output)
```

**关键观察**：

1. `"openai:gpt-4o-mini"` 是字符串简写，等价于 `OpenAIChatModel("gpt-4o-mini")`
2. `system_prompt` 直接传字符串就行，不需要 `ChatPromptTemplate`
3. `run_sync` 返回 `AgentRunResult`，`.output` 是模型最终回复（这里是 `str`）
4. API Key 走 `OPENAI_API_KEY` 环境变量，`.env` + `python-dotenv` 自动加载

### vs LangChain

```python
# LangChain 等价
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

prompt = ChatPromptTemplate.from_messages([
    ("system", "你是一位简洁的助手，一句话回答。"),
    ("human", "{q}"),
])
model = ChatOpenAI(model="gpt-4o-mini")
chain = prompt | model | StrOutputParser()
print(chain.invoke({"q": "Python 的 GIL 是什么？"}))
```

LangChain 把"提示、模型、解析"拆三件套用 `|` 拼；Pydantic AI 把这三件事打包进 `Agent`。**前者更灵活，后者更省脑**。

---

## 7. 进一步看：带工具的 Agent

```python
from pydantic_ai import Agent, RunContext

agent = Agent("openai:gpt-4o-mini", system_prompt="你是一位天气助手。")

@agent.tool_plain
def get_weather(city: str) -> str:
    """查询城市当前天气"""
    fake_db = {"北京": "晴 26°C", "上海": "多云 24°C"}
    return fake_db.get(city, "未知")

result = agent.run_sync("北京和上海的天气分别怎么样？")
print(result.output)
# 模型自己决定调两次 get_weather，再合成自然语言回复
```

注意：

- `@agent.tool_plain` 表示"不要 RunContext"，函数签名直接被 LLM 看见
- `@agent.tool` 表示"需要 RunContext"，第一个参数必须是 `ctx: RunContext[...]`
- docstring 会作为工具描述喂给模型，类型注解自动转 JSON Schema

---

## 8. 谁在用 Pydantic AI

Pydantic AI 2024 年 12 月发布，到 2026 年已经被以下场景广泛采用：

- **Pydantic Validation** 本身的内部 AI 工具（Pydantic 团队 dogfood）
- **Logfire 平台**自己用来分析日志
- 已知用户：Anthropic 部分内部工具、Hex、Cleanlab 等
- 在 LangChain / LlamaIndex / CrewAI 用户的"我想换"列表里排第一

由于背后是 Pydantic 团队（**也就是 OpenAI / Google / Anthropic 都依赖的人**），可以预期它的稳定性和持续维护非常靠谱。

---

## 9. 学习路径建议

| 阶段 | 章节 | 重点 |
|------|------|------|
| 入门 | 01-overview → 03-first-agent → 04-models | 跑通 Hello World |
| 核心 | 05-dependencies → 06-output-types → 07-messages | 写一个有状态的 Agent |
| 工具 | 02-tools 全部 5 篇 | 让 Agent 能干活 |
| 进阶 | 03-advanced（流式 / 多模态 / Hooks） | 性能与体验 |
| 生产 | 04-modules（Logfire / Evals / Graph） | 上线必备 |
| 模式 | 05-patterns（多 Agent / Web / 测试） | 架构层 |

---

## 10. 常见误区

| 误区 | 真相 |
|------|------|
| Pydantic AI 是 Pydantic 的子模块 | 是**独立项目**，需要单独 `pip install pydantic-ai` |
| 只能用 Pydantic Model 当输出 | 也能用 `str` / `list` / `Union` / `TypedDict` / dataclass |
| 必须用 OpenAI | 内置 15+ Provider，OpenRouter 还能再桥接 100+ |
| Agent = LLM 一次调用 | Agent 内部是工具调用循环，可能调几十次模型 |
| Pydantic AI 锁定 Logfire | Logfire 是默认但非必需，可换 OpenTelemetry |
| 没有 LCEL 那么灵活 | 灵活度差不多但抽象层次不同：LangChain 是"积木 + 胶水"，Pydantic AI 是"声明式 Agent" |
| TestModel 只能测连通性 | `TestModel` 可以模拟任意输出，`FunctionModel` 可以模拟工具调用序列 |

---

## 11. 本章 demo

完整可运行代码：[`demos/basics/01_overview.py`](../../demos/basics/01_overview.py)

跑通后下一章：[02-installation.md](02-installation.md) —— 安装、依赖、版本对齐。
