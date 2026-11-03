# OpenAI Agents SDK 概览

> OpenAI Agents SDK 是 OpenAI 官方的 Agent 框架，前身是实验项目 Swarm。它的核心概念不多，Agents、Tools、Handoffs、Guardrails、Sessions、Tracing 六个，学起来快；代价是和 OpenAI 自家服务绑得比较紧，hosted tools、Tracing 后台、Realtime 都是围绕 OpenAI 设计的。这篇先把它是什么、和同类框架怎么选讲清楚。

---

## 1. 它是什么

OpenAI Agents SDK（包 `openai-agents`）是 OpenAI 2025 年 GA 的官方 Agent 框架。

它的设计取向大致有四点：

1. 小：核心 API 不到 10 个，文档一个小时能读完
2. 贴合 Python 习惯：装饰器、类型注解、async/await
3. 和 OpenAI 生态绑定：默认把 trace 发到 OpenAI 后台，能用 hosted tools，Realtime API 支持得最早
4. 面向生产：guardrails、sessions、handoffs 是框架内建的能力，不需要另外包一层

前身 Swarm 是 OpenAI 2024 年 10 月开源的实验项目。Swarm 证明了 handoffs 这种多 Agent 写法好用，OpenAI 随后把它做成了正式产品。

---

## 2. 跟 Pydantic AI / LangGraph 怎么选

| 维度 | OpenAI Agents SDK | Pydantic AI | LangGraph |
|------|-------------------|-------------|-----------|
| 设计哲学 | 最小原语 + OpenAI 生态 | 类型安全 + 框架无关 | 图引擎 + 状态机 |
| 多 Agent | Handoffs（路由式） | Agent 嵌套调用 | Graph node + edge |
| 托管工具 | 有：web_search / file_search / code_interpreter / hosted MCP（仅 OpenAI Responses 模型） | 有：Native Tools 挂载 provider 原生工具（web search、code execution 等） | 无，自己接 |
| 观测 | OpenAI Dashboard（开箱） | Logfire 或外接 | LangSmith |
| 多 provider | Chat Completions 兼容端点 / Any-LLM / LiteLLM | 原生多 provider | 原生多 provider |
| 实时 / 语音 | Realtime + Voice Pipeline | 弱 | 弱 |
| 人在回路 | `needs_approval` + `RunState`（可序列化恢复） | Deferred Tools + `requires_approval`（原生） | `interrupt()` |
| 类型安全 | 中（output_type 用 pydantic） | 强 | 弱 |
| 学习曲线 | 平 | 平 | 较陡 |

如果你已经在用 GPT 系列模型、想直接用 hosted web_search / file_search，或者场景本身适合 handoffs（客服分流、按领域分给不同专家），又或者要做语音 Agent，OpenAI Agents SDK 是顺手的选择。它自带的 Tracing 后台不用任何配置就能看。

反过来，如果需要在多家模型之间平等切换、对类型约束要求严，Pydantic AI 更合适；流程是复杂状态机或要跑很久的工作流，用 LangGraph。

---

## 3. 六个核心原语

> 官方文档现在把核心原语列成 Agents / Handoffs / Guardrails / Sessions / Tracing 五个，工具算作 Agent 的一部分；这里单独拿出来，按六个讲。0.14 起还多了 beta 的 Sandbox Agents（让 agent 在隔离工作区里读写文件、跑命令），本手册不展开，可以看官方 [Sandbox agents 指南](https://openai.github.io/openai-agents-python/sandbox/guide/)。

### 3.1 Agent

```python
from agents import Agent

writer = Agent(
    name="Writer",
    instructions="你是技术博客作者，把概念解释得通俗。",
    model="gpt-6-luna",
)
```

Agent = LLM + 指令 + （工具 / handoffs / output_type / guardrails）。

### 3.2 Tools

```python
from agents import function_tool

@function_tool
def get_weather(city: str) -> str:
    return f"{city}: 22°C, 晴"

writer = Agent(name="W", instructions="...", tools=[get_weather])
```

### 3.3 Handoffs

```python
billing_agent = Agent(name="Billing", instructions="处理账单问题")
support_agent = Agent(name="Support", instructions="处理技术问题")

triage = Agent(
    name="Triage",
    instructions="分流到对应专家",
    handoffs=[billing_agent, support_agent],
)
```

Handoff 和工具、子 Agent 都不一样，区别见 [Handoffs 概念：与 Tool、Sub-Agent 的区别](../03-handoffs/01-handoffs-concept.md)。

### 3.4 Guardrails

```python
from agents import Agent, GuardrailFunctionOutput, input_guardrail

@input_guardrail
async def block_pii(ctx, agent, user_input):
    return GuardrailFunctionOutput(
        output_info=None,
        tripwire_triggered="身份证" in str(user_input),
    )

agent = Agent(name="A", instructions="...", input_guardrails=[block_pii])
```

### 3.5 Sessions

```python
from agents import Agent, Runner, SQLiteSession

session = SQLiteSession("user_42")
await Runner.run(agent, "你好", session=session)
await Runner.run(agent, "再问一次", session=session)  # 自动续接上下文（SQLiteSession 默认存内存，传文件路径才会持久化）
```

### 3.6 Tracing

```python
# 什么都不用配，跑 Agent 时自动上传到 platform.openai.com/traces
# 前提：有 OpenAI API key。用第三方模型又没有 OpenAI key，要先 set_tracing_disabled(True)
```

---

## 4. 最小可运行示例

```python
import asyncio
from agents import Agent, Runner

agent = Agent(
    name="Joke",
    instructions="你是讲冷笑话的 Agent。",
    model="gpt-6-luna",
)


async def main():
    result = await Runner.run(agent, "讲个程序员冷笑话")
    print(result.final_output)


asyncio.run(main())
```

跑：

```bash
pip install openai-agents
export OPENAI_API_KEY=sk-...
python hello.py
```

跑完到 https://platform.openai.com/traces 能看到这次调用的 trace。

---

## 5. 和 LangChain 的概念对照

| OpenAI Agents | LangChain | 备注 |
|---|---|---|
| Agent | AgentExecutor + ChatPromptTemplate | OpenAI 的更紧凑 |
| @function_tool | @tool | 几乎等价 |
| Handoffs | LangGraph node edges | 概念近 |
| Sessions | RunnableWithMessageHistory | OpenAI 的更简单 |
| Tracing | LangSmith | OpenAI Dashboard 开箱即用 |
| Guardrails | 自接 / 中间件 | OpenAI 内建 |

---

## 6. 和 Pydantic AI 的概念对照

| OpenAI Agents | Pydantic AI | 备注 |
|---|---|---|
| Agent | Agent | 名字一样，配置略不同 |
| @function_tool | @agent.tool | Pydantic AI 绑 agent，OpenAI 全局 |
| Handoffs | 自己写（agent.run(子)） | OpenAI 内建 |
| output_type | output_type | 一致 |
| Sessions | message_history | 概念近 |
| Tracing | Logfire | 不同生态 |

---

## 7. 版本与发布

- 2024-10：Swarm 实验项目发布（experimental）
- 2025-03：OpenAI Agents SDK GA（`openai-agents` 包）
- 2025 下半年～2026：版本号一直在 `0.x`（按约定，`0.Y` 的 Y 变化可能含破坏性改动，要稳就锁 `0.24.*`）。几个里程碑：
  - 0.3：Realtime 迁到 GA 接口（`gpt-realtime`）；0.4：要求 `openai` v2
  - 0.6/0.7：handoff 历史默认打包成一条 assistant 消息，后来又改回默认不嵌套（要嵌套得显式开 `nest_handoff_history`）
  - 0.8：同步函数工具改在工作线程里执行；本地 MCP 工具失败默认返回给模型而不是中断 run
  - 0.10：Responses API 的 WebSocket 传输；0.14：Sandbox Agents（beta）
  - 0.15：模型拒答抛 `ModelRefusalError`；0.19：Programmatic Tool Calling、`@tool` / `agents.decorators`
  - 0.20：默认模型改为 `gpt-5.6-luna`；0.21：要求 `openai>=3`，HTTP 换 httpx2；Python ≥ 3.10（0.9 起）
  - 0.22：更严格的失败与数据隔离（输出守卫拦下终端工具输出时的脱敏等）
- 本手册基于 `openai-agents 0.24.0` + `openai 3.42`（2026 年 11 月），其他版本的变化以 [官方 Release notes](https://openai.github.io/openai-agents-python/release/) 为准

---

## 8. 下一步

- 先装好跑起来：[安装与第一个 Agent](./02-install-hello.md)
- Agent 的全部配置项：[Agent 配置详解](./03-agent-config.md)
- OpenAI 的托管工具：[Hosted Tools：OpenAI 托管的内置工具](../02-tools/02-hosted-tools.md)
- 多 Agent 交接：[Handoffs 概念：与 Tool、Sub-Agent 的区别](../03-handoffs/01-handoffs-concept.md)

## 参考资料

- 官方 docs：https://openai.github.io/openai-agents-python/
- GitHub：https://github.com/openai/openai-agents-python
- Swarm（前身）：https://github.com/openai/swarm
