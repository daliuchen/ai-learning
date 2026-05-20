# OpenAI Agents SDK 实战手册

> 一句话：OpenAI 官方出的轻量级 Agent 框架——把 **Agents / Handoffs / Guardrails / Sessions / Tracing** 几个最小原语 + Hosted Tools 生态做成一等公民。本手册带你从 hello world 跑到生产部署，并跟 Pydantic AI / LangGraph 做横向对比。

---

## 这本手册为啥要写

OpenAI Agents SDK（前身是 Swarm 实验项目）是 OpenAI 自己出的 Agent 框架。它跟 Pydantic AI、LangGraph 都属于"Agent 框架"这一层，但有自己的特色：

| 维度 | OpenAI Agents SDK | Pydantic AI | LangGraph |
|------|---|---|---|
| 设计哲学 | 最小原语 + OpenAI 生态绑定 | 类型安全 + 框架无关 | 图引擎 + 状态机 |
| 多 Agent 协作 | **Handoffs**（一等公民） | Agent.run(子 Agent) | Graph nodes + edges |
| 托管工具 | **web_search / file_search / code_interpreter / computer_use** 内置 | 无（需自接） | 无（需自接） |
| 观测 | **OpenAI Platform Dashboard** 默认开 | Logfire / 自接 | LangSmith |
| 实时语音 | Realtime API + Voice Pipeline | 不擅长 | 不擅长 |
| 上手成本 | 低 | 低 | 中 |

**啥时候选它**：项目跟 OpenAI 生态绑定（用 GPT 系列 + web_search + Tracing dashboard）、想要 handoffs 这种"路由"式多 Agent、想做语音 / Realtime。

**啥时候不选它**：要换模型（虽然 LiteLLM 能接但不丝滑）、要严格类型安全（用 Pydantic AI）、要复杂图状态机（用 LangGraph）。

---

## 章节结构（38 篇）

### [01-basics（基础入门）](./docs/01-basics) — 6 篇
1. 概览：OpenAI Agents SDK 是什么 / 设计哲学 / 跟其它框架对比
2. 安装与第一个 Agent
3. Agent 配置详解：instructions / model / tools / output_type / model_settings
4. Runner 三种姿势：run / run_sync / run_streamed
5. RunResult & Usage
6. Sessions 会话状态

### [02-tools（工具系统）](./docs/02-tools) — 5 篇
1. Function Tools：@function_tool 装饰器
2. Hosted Tools：web_search / file_search / code_interpreter / computer_use ★
3. Agent as Tool
4. Tool Choice / Parallel / 错误处理
5. 动态工具集与上下文感知工具

### [03-handoffs（Handoffs，OpenAI 独门）](./docs/03-handoffs) — 4 篇 ★
1. Handoffs 概念 / 跟 Tool / 跟 Sub-Agent 调用的区别
2. Triage Pattern（路由模式）
3. Handoff Inputs & Filters
4. 复杂多 Agent 协作

### [04-guardrails（守卫）](./docs/04-guardrails) — 3 篇 ★
1. Input Guardrails
2. Output Guardrails
3. Tripwire 与异常处理

### [05-advanced（进阶能力）](./docs/05-advanced) — 6 篇
1. Tracing 内置 & OpenAI Platform Dashboard
2. 自定义 Tracer：接 LangSmith / Langfuse / Logfire
3. Lifecycle Hooks
4. Multi-provider：LiteLLM 接 Claude / Gemini / 本地
5. Realtime Agent（实时语音 API）
6. Voice Pipeline（STT + LLM + TTS）

### [06-integration（集成与生态）](./docs/06-integration) — 4 篇
1. MCP 集成：消费 MCP Server
2. 跟 LangSmith / Langfuse / Logfire 集成
3. FastAPI / Lambda 部署
4. 跟 Pydantic AI / LangChain 互操作

### [07-production（生产化）](./docs/07-production) — 5 篇
1. 部署形态选型
2. Cost / Latency 优化
3. Error Handling & Retry
4. 安全：prompt injection / 越狱防御
5. 评测：把 04-prompt-engineering 的 evalset 套到 Agent 上

### [08-practice（实战项目）](./docs/08-practice) — 5 篇
1. 客服 Triage Agent（OpenAI 招牌示例）
2. 研究助手（hosted web_search + file_search + critique）
3. 语音助手（Realtime API）
4. Computer Use Agent
5. 横向对比：vs Pydantic AI vs LangGraph

★ = OpenAI Agents SDK 独门特性

---

## 安装与跑起来

```bash
cd 05-openai-agents-sdk
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 填入 OPENAI_API_KEY
python demos/basics/01_hello.py
```

---

## 跟其它手册的交叉

- 03-mcp：[06-integration/01-mcp.md](./docs/06-integration/01-mcp.md) 详讲消费 MCP Server
- 04-prompt-engineering：[07-production/05-evals.md](./docs/07-production/05-evals.md) 沿用 evalset 方法论
- 02-pydantic-ai：[08-practice/05-vs-others.md](./docs/08-practice/05-vs-others.md) 完整对比
- 01-langchain（LangGraph）：同上

---

## 学习路径

**最短路径**（半天上手）：
```
01-basics/01-overview
  → 01-basics/02-install-hello
  → 01-basics/03-agent-config
  → 02-tools/01-function-tools
  → 03-handoffs/01-handoffs-concept
```

**OpenAI 招牌特性体验**：
```
02-tools/02-hosted-tools  ← web_search / file_search
  → 03-handoffs/02-triage-pattern
  → 05-advanced/01-tracing
  → 08-practice/01-customer-triage
```

**生产部署**：
```
05-advanced/02-custom-tracer
  → 06-integration/03-fastapi-deploy
  → 07-production/02-cost-latency
  → 07-production/05-evals
```
