# Multi-provider：接千问 / Claude / Gemini / 本地模型

> SDK 原生只说 OpenAI 协议，但任何 OpenAI 兼容端点（千问、DeepSeek、vLLM、Ollama…）都能用内置的 `OpenAIChatCompletionsModel` 接进来。Claude、Gemini 这类不兼容的 provider 要靠 Any-LLM 或 LiteLLM 适配器，代价是 hosted tools、Realtime 这些 OpenAI 独有的功能用不了。

---

## 1. 为什么要换 provider

常见的理由有四个：成本，千问 flash 这类小模型单价低，适合跑量大的子任务；能力，某些场景（长上下文、多模态）别家更强；合规，公司要求本地或私有部署；容灾，OpenAI 出故障时能切到备选。

---

## 2. 四条接入路径（官方文档）

| 方式 | 适用 | 作用范围 |
|------|------|----------|
| `set_default_openai_client(client)` | 一个 OpenAI 兼容端点当默认，几乎所有 agent 都用 | 全局 |
| `ModelProvider`（`RunConfig(model_provider=...)`） | 某次 run 里统一换 provider | 单次 run |
| `Agent(model=<Model 对象>)` | 不同 agent 用不同 provider | 单个 agent |
| 第三方适配器（Any-LLM / LiteLLM） | 内置路径覆盖不了的 provider 或路由 | 单个 agent 或 provider |

官方建议先用内置路径，大多数情况用不到适配器。

---

## 3. 最常用：OpenAI 兼容端点（以千问为例）

```python
import os
from openai import AsyncOpenAI
from agents import Agent, OpenAIChatCompletionsModel, Runner, set_tracing_disabled

set_tracing_disabled(True)          # 没有 OpenAI key：别上传 trace（否则 401）

client = AsyncOpenAI(
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key=os.environ["DASHSCOPE_API_KEY"],
)
agent = Agent(
    name="Qwen Agent",
    instructions="你是千问驱动的助手",
    model=OpenAIChatCompletionsModel(model="qwen-flash", openai_client=client),
)

result = await Runner.run(agent, "你好")
print(result.final_output)
```

如果整个应用都走同一个兼容端点，也可以全局切换：

```python
# 环境变量：OPENAI_API_KEY=<千问 key>  OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)
agent = Agent(name="A", model="qwen-flash")       # 直接写模型名
```

千问 `qwen-flash` 这类兼容端点支持 function tool、结构化输出（Pydantic / dataclass / `list[TypedDict]`）、handoffs、guardrails、sessions、MCP 工具、`needs_approval` 审批和流式事件；hosted tools 会直接报 `UserError: Hosted tools are not supported with the ChatCompletions API`。

官方文档列出了 Chat Completions 路径的几个坑：

- SDK 会静默丢掉 Chat Completions 发不了的 Responses 专属字段：`previous_response_id`、`conversation_id`、Responses 的 `prompt`、非纯文本的工具输出。想在开发阶段让它们报错，用 `OpenAIProvider(use_responses=False, strict_feature_validation=True)`；
- 流式默认不返回 token 用量，要加 `ModelSettings(include_usage=True)`，加上后 `usage.total_tokens` 才有值；
- 有些兼容端点流式返回 tool call 的增量块不可靠，可以用 `OpenAIProvider(use_responses=False, buffer_streamed_tool_calls=True)`，等流结束后一次性吐出；
- 有些端点不支持 `json_schema` 结构化输出，会报 `response_format.type ... not one of ['text','json_object']`，这时输出的 JSON 可能不合法，尽量选支持 JSON Schema 的 provider；
- `finish_reason="length"` 却没有产出文本、tool call 或拒答时，抛 `ModelBehaviorError`，意思是预算耗尽，和内容拒答无关；
- Chat Completions 返回音频时抛 `AgentsException("Audio is not currently supported")`，音频场景请用 Realtime 或 Voice。

---

## 4. 适配器 A：Any-LLM（需要 Python 3.11+）

```bash
pip install "openai-agents[any-llm]"
```

```python
from agents import Agent
from agents.extensions.models.any_llm_model import AnyLLMModel

agent = Agent(
    name="Any-LLM Agent",
    instructions="...",
    model=AnyLLMModel(
        model="openai/qwen-flash",          # <provider>/<model>
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        api="chat_completions",             # 也可 "responses"；不写则按 provider 自动选
    ),
)
```

`AnyLLMModel` 经 `openai/qwen-flash` 走千问兼容端点时工具调用正常，开了 `ModelSettings(include_usage=True)` 也能拿到用量。官方把它定位为 best-effort 的 beta 适配器，能力缺口取决于上游的 Any-LLM，所以上线前要在你实际要用的那条 provider 路径上验证结构化输出、tool calling 和用量统计。也可以通过 `MultiProvider` 用：`model="any-llm/<provider>/<model>"`。

---

## 5. 适配器 B：LiteLLM（和 openai v3 有依赖冲突）

```bash
pip install "openai-agents[litellm]"      # 文档里的装法
```

```python
from agents.extensions.models.litellm_model import LitellmModel

agent = Agent(name="Claude Agent", instructions="...",
              model=LitellmModel(model="anthropic/claude-sonnet-5-5", api_key=os.environ["ANTHROPIC_API_KEY"]))
```

这里有个依赖冲突。`openai-agents 0.24` 要求 `openai>=3,<4`，而目前所有版本的 `litellm`（到 1.104）都钉着 `openai<3`。在干净环境里 `pip install "openai-agents[litellm]"`，pip 会回溯，把 `openai` 降到 2.24，之后 `import agents` 直接报 `ImportError: cannot import name 'PromptCacheOptions'`。

所以想用 LiteLLM，就把它隔离到单独的服务或进程里，比如跑 LiteLLM Proxy，对外暴露 OpenAI 兼容端点，SDK 再按第 3 节的方式接；或者等 litellm 支持 openai v3。新项目优先选 Any-LLM 或第 3 节的兼容端点。

LiteLLM 适配器本身也是 best-effort 的 beta：有些 provider 默认不回填用量，要设 `ModelSettings(include_usage=True)`；`preserve_raw_usage` 对它无效；遇到 Pydantic 序列化警告，可以设 `OPENAI_AGENTS_ENABLE_LITELLM_SERIALIZER_PATCH=true`，这是对私有 API 打的补丁，升级 litellm 后要重新验证。

---

## 6. 常见 model 字符串（适配器里的命名）

```python
# Any-LLM / LiteLLM 都用 "<provider>/<model>" 约定
AnyLLMModel(model="anthropic/claude-sonnet-5-5")
AnyLLMModel(model="gemini/gemini-4-argon")
AnyLLMModel(model="mistral/mistral-large-latest")
AnyLLMModel(model="ollama/llama3.2")             # 本地 ollama
AnyLLMModel(model="deepseek/deepseek-chat")
```

具体的 provider 前缀和可用模型名，以 Any-LLM、LiteLLM 各自的文档为准，上面只是示意。自己部署的 vLLM、TGI 或内部网关不需要适配器，直接用第 3 节的 `OpenAIChatCompletionsModel(base_url=...)`。

---

## 7. 不同 Agent 用不同模型

```python
researcher = Agent(
    name="Researcher",
    model=OpenAIChatCompletionsModel(model="qwen-flash", openai_client=client),   # 便宜
)

writer = Agent(
    name="Writer",
    model=AnyLLMModel(model="anthropic/claude-sonnet-5-5"),   # 写作好
)

pm = Agent(
    name="PM",
    model="gpt-6.1-sol",  # 主协调用 OpenAI
    tools=[
        researcher.as_tool("research", "..."),
        writer.as_tool("write", "..."),
    ],
)
```

常见的搭配是贵的模型做协调和综合，便宜的模型跑子任务。官方建议一个 workflow 里尽量统一模型形态（Responses 或 Chat Completions），因为两种形态支持的特性和工具不一样；如果混用，要自己确认用到的特性两边都有。

---

## 8. 哪些特性会失效

| 特性 | OpenAI 模型 | 非 OpenAI |
|------|-------------|-----------|
| `WebSearchTool` | 支持 | 不支持 |
| `FileSearchTool` | 支持 | 不支持 |
| `CodeInterpreterTool` | 支持 | 不支持 |
| `ComputerTool` | 支持 | 不支持 |
| `ImageGenerationTool` | 支持 | 不支持 |
| Realtime API | 支持 | 不支持 |
| Tracing Dashboard | 支持，trace 看得到 | 需要 OpenAI key 才能上传；没有就 `set_tracing_disabled(True)` 或换处理器（401 是最常遇到的第一个坑） |
| `previous_response_id` / `conversation_id` / Responses `prompt` | 支持 | 不支持（静默丢弃） |
| 流式用量 | 支持 | 要 `include_usage=True` |
| 结构化输出 | 支持 | 取决于 provider 是否支持 `json_schema` |

`@function_tool`、Handoffs、Guardrails、Sessions、Lifecycle Hooks 和 Output Types 在非 OpenAI provider 上照常可用。

---

## 9. 模型能力的差异

不同模型对 tool calling 和结构化输出的支持质量不一样：

| 模型 | function calling | structured output | 备注 |
|------|------------------|-------------------|------|
| gpt-6.1-sol | 强 | 强 | 原生 Responses，hosted tools 全可用 |
| gpt-6-luna | 强 | 强 | 便宜 |
| qwen-flash（千问，兼容端点） | 支持多工具、审批、handoff 路由 | 支持 Pydantic / dataclass / `list[TypedDict]` | 便宜；流式要 `include_usage` |
| claude-sonnet-5-5 / claude-haiku-5-5 | 强 | 强 | 走适配器，能力取决于适配器 |
| gemini-4-argon | 强 | 取决于适配器 | 走适配器 |
| 本地小模型 | 弱 | 弱 | 慎用 |

这张表只描述能力范围，具体质量以自己的评测集为准。换了 provider 之后要重跑 evalset，别默认新模型的质量和原来一样。

---

## 10. 容错：备选 provider

```python
import asyncio

primary = Agent(name="Primary", model="gpt-6.1-sol",
                model_settings=ModelSettings(timeout=10.0))          # 单次模型调用超时 → ModelTimeoutError
fallback = Agent(name="Fallback", model=AnyLLMModel(model="anthropic/claude-sonnet-5-5"))


async def safe_run(query: str):
    try:
        return await Runner.run(primary, query)
    except (ModelTimeoutError, APIError):
        return await Runner.run(fallback, query)
```

这适合 OpenAI 偶尔出故障时切到别家。同一个 provider 内部的瞬时故障（限流、5xx、网络抖动），优先用 SDK 自带的 `ModelSettings(retry=...)`，见 [错误处理与重试](../07-production/03-error-handling.md)；只有跨 provider 才需要这种手写的 fallback。

---

## 11. 提示词在不同模型间的可移植性

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

详见 Prompt Engineering 手册的 [PE Models 04：跨模型可移植 + 成本性能权衡](../../../04-prompt-engineering/docs/06-models/04-cross-model.md)。

---

## 12. 完整 demo：多模型混搭

这段只依赖一个 OpenAI 兼容端点：qwen-flash 同时充当「便宜模型」和「写作模型」，Researcher 和 Writer 走 `OpenAIChatCompletionsModel`，PM 也用同一个客户端。换成 OpenAI + Claude 时，把相应的 `model=` 换成字符串模型名或 `AnyLLMModel(...)` 即可。

```python
import asyncio
import os
from openai import AsyncOpenAI
from agents import Agent, OpenAIChatCompletionsModel, Runner, function_tool, set_tracing_disabled

set_tracing_disabled(True)
client = AsyncOpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"])
cheap = OpenAIChatCompletionsModel(model="qwen-flash", openai_client=client)
strong = OpenAIChatCompletionsModel(model="qwen-flash", openai_client=client)   # 生产里换成更强的模型


@function_tool
def search(query: str) -> str:
    """搜资料"""
    return f"results for {query}"


# 子 agent 用便宜模型
researcher = Agent(
    name="Researcher",
    instructions="搜资料",
    tools=[search],
    model=cheap,
)


writer = Agent(
    name="Writer",
    instructions="写报告",
    model=strong,
)


# 主 agent 负责协调
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
    model=strong,
)


async def main():
    result = await Runner.run(pm, "AI agent 框架现状")
    print(result.final_output)


asyncio.run(main())
```

---

## 13. 跟 Pydantic AI / LangChain 对比

Pydantic AI 原生支持多 provider，不用再包一层 LiteLLM；LangChain 是每个 provider 一个 `Chat...` 类；OpenAI Agents SDK 原生只支持 OpenAI 协议，兼容端点走 Chat Completions，其他 provider 靠 Any-LLM 或 LiteLLM 适配，它的优势在 hosted tools。

我的建议是：需要各家 provider 一视同仁，用 Pydantic AI；主要用 OpenAI、偶尔切到别家，用 OpenAI Agents SDK 配兼容端点或 Any-LLM。

---

## 14. 下一步

- [Realtime Agent：实时语音 API](./05-realtime.md)
- 跟 Pydantic AI 的完整对比 → [横向对比：OpenAI Agents SDK vs Pydantic AI vs LangGraph](../08-practice/05-vs-others.md)
- 生产环境里的备选 provider → [错误处理与重试](../07-production/03-error-handling.md)
