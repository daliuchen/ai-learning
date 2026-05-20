# OpenAI Agents SDK 概览

> **一句话**：OpenAI 官方出的 Agent 框架——核心只有 **Agents / Tools / Handoffs / Guardrails / Sessions / Tracing** 6 个原语，但跟 OpenAI 生态深度绑定（hosted tools / tracing dashboard / realtime）。它是 Swarm 项目的"长大版"。

---

## 1. 是什么 / 为啥造它

OpenAI Agents SDK（包 `openai-agents`）是 OpenAI 2025 年 GA 的官方 Agent 框架。

设计目标：

1. **最小**：核心 API 不到 10 个，文档读 1 小时
2. **Python-native**：用装饰器、类型 hints、async/await
3. **跟 OpenAI 生态绑定**：默认接 Tracing dashboard、可用 hosted tools、第一手支持 Realtime API
4. **生产可用**：guardrails / sessions / handoffs 都是一等公民，不是 wrapper

它的前身是 OpenAI 2024 年 10 月开源的 **Swarm**（实验项目）——Swarm 验证了 handoffs 这个模式好用，OpenAI 直接把它打磨成正式产品。

---

## 2. 跟 Pydantic AI / LangGraph 怎么选

| 维度 | OpenAI Agents SDK | Pydantic AI | LangGraph |
|------|-------------------|-------------|-----------|
| **设计哲学** | 最小原语 + OpenAI 生态 | 类型安全 + 框架无关 | 图引擎 + 状态机 |
| **多 Agent** | **Handoffs**（路由式） | Agent 嵌套调用 | Graph node + edge |
| **托管工具** | ✅ web_search / file_search / code_interpreter / computer_use | ❌ 自接 | ❌ 自接 |
| **观测** | OpenAI Dashboard（开箱） | Logfire 或外接 | LangSmith |
| **多 provider** | 通过 LiteLLM 接 | 原生多 provider | 原生多 provider |
| **实时 / 语音** | ✅ Realtime + Voice Pipeline | 弱 | 弱 |
| **类型安全** | 中（output_type 用 pydantic） | 强 | 弱 |
| **学习曲线** | 平 | 平 | 较陡 |

**选 OpenAI Agents SDK 的场景**：
- 已经在用 GPT 系列、想要 hosted web_search / file_search
- 用例适合 **handoffs**（客服分流、专家系统）
- 想做语音 Agent / Realtime
- 重视开箱即用的 Tracing dashboard

**不选的场景**：
- 多 provider 平等支持（用 Pydantic AI）
- 复杂状态机 / 长期工作流（用 LangGraph）
- 严格类型安全约束（用 Pydantic AI）

---

## 3. 6 个核心原语速览

### 3.1 Agent

```python
from agents import Agent

writer = Agent(
    name="Writer",
    instructions="你是技术博客作者，把概念解释得通俗。",
    model="gpt-4o-mini",
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

### 3.3 Handoffs（独门）

```python
billing_agent = Agent(name="Billing", instructions="处理账单问题")
support_agent = Agent(name="Support", instructions="处理技术问题")

triage = Agent(
    name="Triage",
    instructions="分流到对应专家",
    handoffs=[billing_agent, support_agent],
)
```

Handoffs ≠ Tool ≠ Sub-Agent，详见 [03-handoffs/01-handoffs-concept.md](../03-handoffs/01-handoffs-concept.md)。

### 3.4 Guardrails

```python
from agents import Agent, input_guardrail

@input_guardrail
async def block_pii(ctx, agent, user_input: str):
    if "身份证" in user_input:
        return GuardrailFunctionOutput(tripwire_triggered=True)
    return GuardrailFunctionOutput()

agent = Agent(name="A", instructions="...", input_guardrails=[block_pii])
```

### 3.5 Sessions

```python
from agents import Agent, Runner, SQLiteSession

session = SQLiteSession("user_42")
await Runner.run(agent, "你好", session=session)
await Runner.run(agent, "再问一次", session=session)  # 自动续接上下文
```

### 3.6 Tracing

```python
# 啥也不用做，跑 Agent 自动上传到 platform.openai.com/traces
```

---

## 4. 完整 hello world

```python
import asyncio
from agents import Agent, Runner

agent = Agent(
    name="Joke",
    instructions="你是讲冷笑话的 Agent。",
    model="gpt-4o-mini",
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

去 https://platform.openai.com/traces 能看到这次调用的 trace。

---

## 5. 跟 LangChain 视角的对应

| OpenAI Agents | LangChain | 备注 |
|---|---|---|
| Agent | AgentExecutor + ChatPromptTemplate | OpenAI 的更紧凑 |
| @function_tool | @tool | 几乎等价 |
| Handoffs | LangGraph node edges | 概念近 |
| Sessions | RunnableWithMessageHistory | OpenAI 的更简单 |
| Tracing | LangSmith | OpenAI Dashboard 开箱即用 |
| Guardrails | 自接 / 中间件 | OpenAI 一等公民 |

---

## 6. 跟 Pydantic AI 视角的对应

| OpenAI Agents | Pydantic AI | 备注 |
|---|---|---|
| Agent | Agent | 名字一样，配置略不同 |
| @function_tool | @agent.tool | Pydantic AI 绑 agent，OpenAI 全局 |
| Handoffs | 自己写（agent.run(子)） | OpenAI 一等公民 |
| output_type | output_type | 一致 |
| Sessions | message_history | 概念近 |
| Tracing | Logfire | 不同生态 |

---

## 7. 版本与发布

- 2024-10：Swarm 实验项目发布（experimental）
- 2025-03：OpenAI Agents SDK GA（`openai-agents` 包）
- 当前文档基于 `openai-agents >= 0.0.6`

---

## 8. 下一步

- 📖 装一下，跑 hello world → [02-install-hello.md](./02-install-hello.md)
- 📖 Agent 完整配置 → [03-agent-config.md](./03-agent-config.md)
- 📖 想立刻看招牌特性 → [02-tools/02-hosted-tools.md](../02-tools/02-hosted-tools.md)
- 📖 想看 handoffs → [03-handoffs/01-handoffs-concept.md](../03-handoffs/01-handoffs-concept.md)

## 参考资料

- 官方 docs：https://openai.github.io/openai-agents-python/
- GitHub：https://github.com/openai/openai-agents-python
- Swarm（前身）：https://github.com/openai/swarm
