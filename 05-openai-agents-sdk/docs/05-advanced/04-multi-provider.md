# Multi-provider：用 LiteLLM 接 Claude / Gemini / 本地

> **一句话**：通过 `LitellmModel` 包装，OpenAI Agents SDK 能跑 Claude / Gemini / Mistral / 本地 Ollama 等几乎所有模型——但 hosted tools / Tracing dashboard 等 OpenAI 独门特性会失效。

---

## 1. 为啥要换 provider

- **成本**：Claude Haiku 比 GPT-4o-mini 便宜
- **能力**：某场景 Claude / Gemini 更强（长上下文 / 多模态）
- **合规**：内部要求本地 / 私有部署
- **降级**：OpenAI 挂了切到备选

---

## 2. 装 LiteLLM

```bash
pip install "openai-agents[litellm]"
# 或单独
pip install litellm
```

---

## 3. 用 LitellmModel

```python
from agents import Agent, Runner
from agents.extensions.models.litellm_model import LitellmModel


agent = Agent(
    name="Claude Agent",
    instructions="你是 Claude 模型驱动的助手",
    model=LitellmModel("anthropic/claude-sonnet-4-6"),
)

# 环境变量
# export ANTHROPIC_API_KEY=...

result = await Runner.run(agent, "你好")
print(result.final_output)
```

`model="anthropic/claude-sonnet-4-6"` 是 LiteLLM 的命名约定，详见 https://docs.litellm.ai/docs/providers。

---

## 4. 常见 model 字符串

```python
LitellmModel("anthropic/claude-sonnet-4-6")
LitellmModel("anthropic/claude-haiku-4-5-20251001")
LitellmModel("anthropic/claude-opus-4-7")

LitellmModel("gemini/gemini-2.5-pro")
LitellmModel("gemini/gemini-2.0-flash")

LitellmModel("groq/llama-3.3-70b-versatile")
LitellmModel("mistral/mistral-large-latest")

# 本地 ollama
LitellmModel("ollama/llama3.2")

# DeepSeek
LitellmModel("deepseek/deepseek-chat")
```

---

## 5. 自定义 endpoint（OpenAI 兼容 API）

```python
from agents.extensions.models.litellm_model import LitellmModel


agent = Agent(
    name="LocalAgent",
    model=LitellmModel(
        "openai/gpt-3.5-turbo",
        api_base="http://localhost:11434/v1",
        api_key="dummy",
    ),
)
```

适合：

- 自己部署的 vLLM / TGI
- 内部 API gateway 提供的 OpenAI 兼容接口

---

## 6. 不同 Agent 用不同模型

```python
researcher = Agent(
    name="Researcher",
    model=LitellmModel("anthropic/claude-haiku-4-5-20251001"),  # 便宜
)


writer = Agent(
    name="Writer",
    model=LitellmModel("anthropic/claude-sonnet-4-6"),  # 写作好
)


pm = Agent(
    name="PM",
    model="gpt-4o",  # 主协调用 OpenAI
    tools=[
        researcher.as_tool("research", "..."),
        writer.as_tool("write", "..."),
    ],
)
```

主流场景：贵的模型做协调 / 综合，便宜的模型跑子任务。

---

## 7. 哪些特性失效

用非 OpenAI provider 时**不能用**：

| 特性 | OpenAI 模型 | 非 OpenAI |
|------|-------------|-----------|
| `WebSearchTool` | ✅ | ❌ |
| `FileSearchTool` | ✅ | ❌ |
| `CodeInterpreterTool` | ✅ | ❌ |
| `ComputerTool` | ✅ | ❌ |
| `ImageGenerationTool` | ✅ | ❌ |
| Realtime API | ✅ | ❌ |
| Tracing Dashboard | ✅ trace 看得到 | ⚠️ trace 仍可以上传，但 LLM call 内容是 LiteLLM 转的 |

仍可用：

- `@function_tool`
- Handoffs
- Guardrails
- Sessions
- Lifecycle Hooks
- Output Types

---

## 8. 模型能力的差异

不同模型对 tool calling / structured output 的支持质量**有差异**：

| 模型 | function calling | structured output | 备注 |
|------|------------------|-------------------|------|
| gpt-4o | ✅ 强 | ✅ 强 | 标杆 |
| gpt-4o-mini | ✅ 强 | ✅ 强 | 便宜 |
| claude-sonnet-4-6 | ✅ 强 | ✅ 强（XML 也行） | 同标杆 |
| claude-haiku-4-5 | ✅ 强 | ✅ 中 | 便宜 |
| gemini-2.5-pro | ✅ 强 | ✅ 中 | 多模态强 |
| groq/llama-3.3 | ⚠️ 中 | ⚠️ 中 | 复杂 tool 弱 |
| 本地 7B | ❌ 弱 | ❌ 弱 | 慎用 |

实战：换 provider 后**重跑 evalset**——别假设新模型同质量。

---

## 9. 容错：备选 provider

```python
import asyncio

primary = Agent(name="Primary", model="gpt-4o")
fallback = Agent(name="Fallback", model=LitellmModel("anthropic/claude-sonnet-4-6"))


async def safe_run(query: str):
    try:
        return await asyncio.wait_for(
            Runner.run(primary, query),
            timeout=10,
        )
    except (asyncio.TimeoutError, Exception):
        return await Runner.run(fallback, query)
```

适合：OpenAI 偶发挂时切 Claude。

---

## 10. 提示词跨模型可移植性

```python
# 在 instructions 里别绑死 OpenAI 习惯
agent = Agent(
    name="X",
    instructions="...",  # 通用提示
)


# ❌ 别写
"Output JSON with the following schema using OpenAI structured outputs..."

# ✅ 写
"Output a JSON object matching this schema: {...}"
```

详见 [04-prompt-engineering/06-models/04-cross-model.md](../../../04-prompt-engineering/docs/06-models/04-cross-model.md)。

---

## 11. 完整 demo：多模型混搭

```python
# demos/advanced/04_multi_provider.py
import asyncio
from agents import Agent, Runner, function_tool
from agents.extensions.models.litellm_model import LitellmModel


@function_tool
def search(query: str) -> str:
    return f"results for {query}"


# 子 agent 用便宜模型
researcher = Agent(
    name="Researcher",
    instructions="搜资料",
    tools=[search],
    model=LitellmModel("anthropic/claude-haiku-4-5-20251001"),
)


writer = Agent(
    name="Writer",
    instructions="写报告",
    model=LitellmModel("anthropic/claude-sonnet-4-6"),
)


# 主 agent 用 OpenAI 协调
pm = Agent(
    name="PM",
    instructions="""协调：
1. research 拿素材
2. write 写报告
""",
    tools=[
        researcher.as_tool("research", "搜资料"),
        writer.as_tool("write", "写报告"),
    ],
    model="gpt-4o",
)


async def main():
    result = await Runner.run(pm, "AI agent 框架现状")
    print(result.final_output)


asyncio.run(main())
```

---

## 12. 跟 Pydantic AI / LangChain 对比

- **Pydantic AI**：原生多 provider，写法不用 LiteLLM 包，更原生
- **LangChain**：每个 provider 一个 `Chat...` 类
- **OpenAI Agents SDK**：原生只 OpenAI，靠 LiteLLM 适配——hosted tools 是它的护城河

要"多 provider 平等支持"用 Pydantic AI；要"主用 OpenAI 偶尔切别的"用 OpenAI Agents SDK + LiteLLM。

---

## 13. 下一步

- 📖 Realtime API → [05-realtime.md](./05-realtime.md)
- 📖 跟 Pydantic AI 完整对比 → [08-practice/05-vs-others.md](../08-practice/05-vs-others.md)
- 📖 备选 provider 在生产 → [07-production/03-error-handling.md](../07-production/03-error-handling.md)
