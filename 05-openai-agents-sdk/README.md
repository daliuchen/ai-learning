# OpenAI Agents SDK 实战手册

> OpenAI Agents SDK 是 OpenAI 官方的轻量级 Agent 框架，核心是 Agents、Handoffs、Guardrails、Sessions、Tracing 几个概念，再加上 OpenAI 托管的一组工具。这本手册从第一个 hello world 讲到生产部署，最后和 Pydantic AI、LangGraph 做一次横向对比。

---

## 它和同类框架的区别

OpenAI Agents SDK 的前身是 Swarm 实验项目。它和 Pydantic AI、LangGraph 处在同一层，差别主要在下面几处：

| 维度 | OpenAI Agents SDK | Pydantic AI | LangGraph |
|------|---|---|---|
| 设计哲学 | 最小原语 + OpenAI 生态绑定 | 类型安全 + 框架无关 | 图引擎 + 状态机 |
| 多 Agent 协作 | Handoffs（框架内建） | Agent.run(子 Agent) | Graph nodes + edges |
| 托管工具 | web_search / file_search / code_interpreter / hosted MCP / image_generation 内置（仅 Responses 模型） | Native Tools（`NativeTool` 挂载 provider 原生工具，如 web search、code execution） | 无（需自接） |
| 观测 | OpenAI Platform Dashboard 默认开启，另有 30+ 第三方 tracing 处理器 | Logfire / 自接 | LangSmith |
| 人在回路 | `needs_approval` + `RunState` 暂停/恢复（原生） | Deferred Tools + `requires_approval`（原生） | `interrupt()` |
| 实时语音 | Realtime API + Voice Pipeline | 不擅长 | 不擅长 |
| 上手成本 | 低 | 低 | 中 |

项目本来就在用 GPT 系列、web_search 和 OpenAI 的 Tracing 后台，或者需要 handoffs 这种路由式的多 Agent 写法，又或者要做语音和 Realtime，选它很顺手。

如果经常要换模型，就要掂量一下：Chat Completions 兼容端点、LiteLLM、Any-LLM 都能接，但 hosted tools、Realtime 这些会用不了。需要严格类型约束时 Pydantic AI 更合适，流程是复杂的图状态机时用 LangGraph。

---

## 版本基线

内容以 2026 年 11 月的 `openai-agents 0.24.0` + `openai 3.42` 为准，参考官方文档 <https://openai.github.io/openai-agents-python/> 和 release notes。从 0.0.x 到 0.24，下面这些变化最容易踩到：

| 变化 | 说明 |
|------|------|
| Python ≥ 3.10 | 0.9 起不再支持 3.9 |
| `openai>=3,<4` | 0.21 起；HTTP 层换成 httpx2，自定义 `http_client` 要迁移 |
| 默认模型 | 不写 `model` 时 0.20 起用 `gpt-5.6-luna`（`reasoning.effort="none"`）；可用 `OPENAI_DEFAULT_MODEL` 或 `RunConfig(model=...)` 改 |
| 用量 | `result.usage` 没了，改为 `result.context_wrapper.usage` |
| 装饰器 | 新增 `agents.decorators`：`@tool`（`@function_tool` 的别名）、`@input_guardrail` 等 |
| 人在回路 | `needs_approval` + `result.interruptions` + `RunState`（可序列化、跨进程恢复） |
| 嵌套 handoff 历史 | 0.7 起默认关闭，要 `RunConfig(nest_handoff_history=True)` 才开 |
| Sessions | 内置十余种后端（SQLite / Redis / SQLAlchemy / MongoDB / Dapr / 加密 / 压缩…），`SessionSettings` 与 `session_input_callback` 控制历史 |
| 重试与超时 | `ModelSettings(timeout=..., retry=ModelRetrySettings(...))`、`@tool(timeout=...)`、`Runner.run(error_handlers=...)` |
| 新能力 | Tool guardrails、Tool search / `tool_namespace`、Programmatic Tool Calling、Sandbox Agents（beta）、Responses WebSocket、`agents.testing` 假模型 |
| Realtime | 默认模型 `gpt-realtime-2.1`，配置改为 `RealtimeRunner(config={"model_settings": ...})`，Python 端只有服务端 WebSocket / SIP，没有浏览器 WebRTC |
| computer use | `computer-use-preview` → GA 的 `gpt-5.6-*` + `{"type":"computer"}` |

> 示例统一用 GPT-6 系：`gpt-6-luna`（便宜）和 `gpt-6.1-sol`（能力强），换成你账号能用的模型即可。没写 `model` 时，SDK 默认是 `gpt-5.6-luna`。

### 没有 OpenAI key 怎么跑

SDK 默认走 Responses API，并把 trace 上传到 OpenAI。千问、DeepSeek、本地 vLLM 这类 OpenAI 兼容端点通常只支持 Chat Completions，所以要改两处配置：

```python
import os
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, set_tracing_disabled

set_tracing_disabled(True)  # 没有 OpenAI key，别上传 trace（否则 401）
client = AsyncOpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
MODEL = OpenAIChatCompletionsModel(model="qwen-flash", openai_client=client)
# Agent(name=..., instructions=..., model=MODEL)
```

或者全局切换：设好 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，再 `set_default_openai_api("chat_completions")`。详见 [Multi-provider：接千问 / Claude / Gemini / 本地模型](./docs/05-advanced/04-multi-provider.md)。hosted tools（web_search / file_search / code_interpreter）、Realtime 和 Voice 的 STT/TTS 依赖 OpenAI 自家服务，第三方端点跑不了。

## 章节结构（38 篇）

### [01-basics（基础入门）](./docs/01-basics)，6 篇
1. 概览：OpenAI Agents SDK 是什么、设计取向、和其他框架对比
2. 安装与第一个 Agent
3. Agent 配置详解：instructions / model / tools / output_type / model_settings
4. Runner 的三种运行方式：run / run_sync / run_streamed
5. RunResult 与 Usage
6. Sessions 会话状态

### [02-tools（工具系统）](./docs/02-tools)，5 篇
1. Function Tools：@function_tool / @tool 装饰器
2. Hosted Tools：web_search / file_search / code_interpreter / computer_use / tool search（OpenAI 特有）
3. Agent as Tool
4. Tool Choice / Parallel / 超时 / 错误处理 / 人工审批
5. 动态工具集与上下文感知工具

### [03-handoffs（Handoffs）](./docs/03-handoffs)，4 篇（OpenAI 特有）
1. Handoffs 概念，以及它和 Tool、Sub-Agent 调用的区别
2. Triage Pattern（路由模式）
3. Handoff Inputs & Filters
4. 复杂多 Agent 协作

### [04-guardrails（守卫）](./docs/04-guardrails)，3 篇（OpenAI 特有）
1. Input Guardrails
2. Output Guardrails
3. Tripwire 与异常处理（含 Tool Guardrails）

### [05-advanced（进阶能力）](./docs/05-advanced)，6 篇
1. 内置 Tracing 与 OpenAI Platform Dashboard
2. 自定义 Tracer：接 LangSmith / Langfuse / Logfire
3. Lifecycle Hooks
4. Multi-provider：千问 / OpenAI 兼容端点 / Any-LLM / LiteLLM 接 Claude / Gemini / 本地
5. Realtime Agent（实时语音 API，`gpt-realtime-2.1`）
6. Voice Pipeline（STT + LLM + TTS）

### [06-integration（集成与生态）](./docs/06-integration)，4 篇
1. MCP 集成：消费 MCP Server
2. 与 LangSmith / Langfuse / Logfire 集成
3. FastAPI / Lambda 部署
4. 与 Pydantic AI / LangChain 互操作

### [07-production（生产化）](./docs/07-production)，5 篇
1. 部署形态选型
2. Cost / Latency 优化
3. 错误处理与重试
4. 安全：prompt injection / 越狱防御
5. 评测：把 Prompt Engineering 手册的评测集用到 Agent 上

### [08-practice（实战项目）](./docs/08-practice)，5 篇
1. 客服 Triage Agent（OpenAI 官方的经典示例）
2. 研究助手（hosted web_search + file_search + critique）
3. 语音助手（Realtime API）
4. Computer Use Agent
5. 横向对比：vs Pydantic AI vs LangGraph

---

## 安装与跑起来

```bash
cd 05-openai-agents-sdk
python -m venv .venv && source .venv/bin/activate   # 需要 Python >= 3.10
pip install -r requirements.txt
cp .env.example .env  # 填入 OPENAI_API_KEY（或第三方的 key + base_url）
# 示例代码都在正文里，可直接复制运行
```

---

## 和其他手册的关联

- 03-mcp：[MCP 集成：让 Agent 消费 MCP Server](./docs/06-integration/01-mcp.md) 讲怎么消费 MCP Server
- 04-prompt-engineering：[评测：把 Prompt Engineering 手册的评测集用到 Agent 上](./docs/07-production/05-evals.md) 沿用那本手册的 evalset 做法
- 02-pydantic-ai 和 01-langchain（LangGraph）：[横向对比：OpenAI Agents SDK vs Pydantic AI vs LangGraph](./docs/08-practice/05-vs-others.md)

---

## 阅读顺序

只想尽快上手（大约半天）：
```
01-basics/01-overview
  → 01-basics/02-install-hello
  → 01-basics/03-agent-config
  → 02-tools/01-function-tools
  → 03-handoffs/01-handoffs-concept
```

想先看 OpenAI 特有的能力：
```
02-tools/02-hosted-tools  ← web_search / file_search
  → 03-handoffs/02-triage-pattern
  → 05-advanced/01-tracing
  → 08-practice/01-customer-triage
```

准备上生产：
```
05-advanced/02-custom-tracer
  → 06-integration/03-fastapi-deploy
  → 07-production/02-cost-latency
  → 07-production/05-evals
```

从第一篇开始：[OpenAI Agents SDK 概览](docs/01-basics/01-overview.md)

问题和勘误可以在 GitHub 仓库提 issue。
