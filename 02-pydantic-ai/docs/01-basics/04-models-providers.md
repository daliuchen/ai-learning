# Pydantic AI 04：模型与 Provider 全览

> **一句话**：Pydantic AI 通过统一的 `Model` 抽象支持 15+ 家 LLM 厂商，切换厂商只需要改一行字符串，比如 `"openai:gpt-4o"` 改成 `"anthropic:claude-sonnet-4-5"`。

---

## 1. 三种指定模型的方式

```python
from pydantic_ai import Agent

# 方式 A：字符串简写（最常用）
agent = Agent("openai:gpt-4o-mini")

# 方式 B：Model 对象（需要 base_url / 自定义 client 时）
from pydantic_ai.models.openai import OpenAIChatModel
agent = Agent(OpenAIChatModel("gpt-4o-mini"))

# 方式 C：Model 对象 + Provider 对象（最完整控制）
from pydantic_ai.providers.openai import OpenAIProvider
provider = OpenAIProvider(api_key="sk-xxx", base_url="https://api.openai.com/v1")
agent = Agent(OpenAIChatModel("gpt-4o-mini", provider=provider))
```

90% 场景用方式 A，需要切 OpenRouter / Azure / 自己代理时用方式 C。

---

## 2. 字符串简写一览

| 字符串 | 等价 |
|--------|------|
| `"openai:gpt-4o-mini"` | `OpenAIChatModel("gpt-4o-mini")` |
| `"openai:gpt-4o"` | `OpenAIChatModel("gpt-4o")` |
| `"openai-responses:gpt-4o"` | `OpenAIResponsesModel("gpt-4o")` |
| `"anthropic:claude-sonnet-4-5"` | `AnthropicModel("claude-sonnet-4-5")` |
| `"anthropic:claude-3-5-haiku-latest"` | `AnthropicModel("claude-3-5-haiku-latest")` |
| `"google-gla:gemini-1.5-pro"` | `GoogleModel("gemini-1.5-pro")`，Gemini GLA API |
| `"google-vertex:gemini-1.5-pro"` | Vertex AI 后端 |
| `"groq:llama-3.3-70b-versatile"` | `GroqModel(...)` |
| `"mistral:mistral-large-latest"` | `MistralModel(...)` |
| `"cohere:command-r-plus"` | `CohereModel(...)` |
| `"bedrock:anthropic.claude-3-5-sonnet"` | `BedrockConverseModel(...)` |
| `"huggingface:meta-llama/Llama-3.3-70B"` | HF Inference |

格式都是 `"<provider>:<model_id>"`。

---

## 3. 各家 Provider 详解

### 3.1 OpenAI

**环境变量**：`OPENAI_API_KEY`

**两个模型类**：

```python
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

# Chat Completions API（兼容性最好）
agent = Agent(OpenAIChatModel("gpt-4o-mini"))

# Responses API（OpenAI 新接口，支持服务端状态）
agent = Agent(OpenAIResponsesModel("gpt-4o"))
```

**自定义 base_url**（走代理 / OpenAI 兼容服务）：

```python
from pydantic_ai.providers.openai import OpenAIProvider

provider = OpenAIProvider(
    api_key="sk-xxx",
    base_url="https://api.deepseek.com/v1",
)
agent = Agent(OpenAIChatModel("deepseek-chat", provider=provider))
```

### 3.2 Anthropic

**环境变量**：`ANTHROPIC_API_KEY`

```python
from pydantic_ai.models.anthropic import AnthropicModel

agent = Agent(AnthropicModel("claude-sonnet-4-5"))
# 或者
agent = Agent("anthropic:claude-3-5-haiku-latest")
```

注意点：

- Claude **不支持** OpenAI 的 `seed`、`logprobs`、`response_format=json_object` 等参数
- 流式 + 工具调用的 chunk 形态与 OpenAI 不同（Pydantic AI 已抹平）

### 3.3 Google Gemini

**两套 API**（Gemini 一直有这个坑）：

| API | provider 前缀 | 环境变量 |
|-----|---------------|---------|
| Google AI（GLA，个人 key） | `google-gla:` | `GEMINI_API_KEY` |
| Vertex AI（企业 GCP） | `google-vertex:` | GCP 默认认证（gcloud auth） |

```python
agent = Agent("google-gla:gemini-1.5-pro")
agent = Agent("google-vertex:gemini-1.5-pro")
```

### 3.4 Groq

**环境变量**：`GROQ_API_KEY`

```python
agent = Agent("groq:llama-3.3-70b-versatile")
```

Groq 主打**超快推理**（用 LPU 芯片），适合实时聊天场景。

### 3.5 Mistral / Cohere

```python
agent = Agent("mistral:mistral-large-latest")
agent = Agent("cohere:command-r-plus")
```

### 3.6 AWS Bedrock

```python
from pydantic_ai.models.bedrock import BedrockConverseModel

agent = Agent(BedrockConverseModel("anthropic.claude-3-5-sonnet-20241022-v2:0"))
```

走 boto3，需要 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`。

### 3.7 Hugging Face

```python
from pydantic_ai.models.huggingface import HuggingFaceModel

agent = Agent(HuggingFaceModel("meta-llama/Llama-3.3-70B-Instruct"))
```

需要 `HF_TOKEN`，背后走 HF Inference API。

### 3.8 Ollama（本地模型）

Ollama 兼容 OpenAI 协议，所以用 `OpenAIChatModel` + 自定义 base_url：

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

provider = OpenAIProvider(base_url="http://localhost:11434/v1", api_key="ollama")
agent = Agent(OpenAIChatModel("qwen2.5:14b", provider=provider))
```

⚠️ 本地小模型对 function calling 支持参差不齐，建议用 `qwen2.5`、`llama3.3` 这类原生支持工具的模型。

---

## 4. OpenAI 兼容生态

很多服务"长得像 OpenAI"，都可以走 `OpenAIChatModel` + 自定义 provider：

| 服务 | base_url | 备注 |
|------|----------|------|
| DeepSeek | `https://api.deepseek.com/v1` | 国产，便宜 |
| Together AI | `https://api.together.xyz/v1` | 一堆开源模型 |
| OpenRouter | `https://openrouter.ai/api/v1` | 100+ 模型聚合 |
| Fireworks | `https://api.fireworks.ai/inference/v1` | 速度快 |
| Perplexity | `https://api.perplexity.ai` | 联网搜索 |
| LiteLLM | `http://localhost:4000` | 代理一切 |
| Azure OpenAI | `https://<resource>.openai.azure.com/...` | 企业 Azure |
| 公司内网网关 | `https://gateway.corp/v1` | 大多数公司都有这种 |

通用写法：

```python
provider = OpenAIProvider(api_key=os.getenv("XXX_KEY"), base_url="...")
agent = Agent(OpenAIChatModel("model-name", provider=provider))
```

Pydantic AI 还内置了一些便捷 Provider，比如：

```python
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider

agent = Agent(OpenAIChatModel("anthropic/claude-3.5-sonnet", provider=OpenRouterProvider()))
```

---

## 5. ModelSettings：温度、token 上限等

```python
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

agent = Agent(
    "openai:gpt-4o-mini",
    model_settings=ModelSettings(
        temperature=0.0,
        max_tokens=1000,
        top_p=0.95,
        seed=42,
        timeout=30,
        stop_sequences=["\n\n###"],
    ),
)
```

也可以**单次调用临时覆盖**：

```python
agent.run_sync("...", model_settings=ModelSettings(temperature=0.9))
```

注意各家支持的参数不一样：

| 参数 | OpenAI | Anthropic | Gemini | Groq |
|------|--------|-----------|--------|------|
| `temperature` | ✅ | ✅ | ✅ | ✅ |
| `max_tokens` | ✅ | ✅（必填） | ✅ | ✅ |
| `top_p` | ✅ | ✅ | ✅ | ✅ |
| `seed` | ✅ | ❌ | ❌ | ✅ |
| `logprobs` | ✅ | ❌ | ❌ | ✅ |
| `stop_sequences` | ✅ | ✅ | ✅ | ✅ |
| `timeout` | ✅ | ✅ | ✅ | ✅ |

Pydantic AI 会把不支持的参数**默默忽略**，不会报错——这有利有弊，**调试时一定要确认你的参数生效了**。

---

## 6. FallbackModel：失败自动降级

主模型挂了自动切备用：

```python
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.anthropic import AnthropicModel

primary = OpenAIChatModel("gpt-4o")
backup = AnthropicModel("claude-sonnet-4-5")

agent = Agent(FallbackModel(primary, backup))
```

触发降级的情况：

- HTTP 429（限流）
- HTTP 500/502/503
- 网络超时

不触发降级：

- 业务逻辑错误（schema 校验失败、ModelRetry）
- 401 / 403（鉴权问题）

生产环境强烈推荐用 `FallbackModel`，**一行代码避免单点**。

---

## 7. TestModel / FunctionModel

### 7.1 TestModel —— 完全 mock

```python
from pydantic_ai.models.test import TestModel
from pydantic_ai import Agent

agent = Agent(TestModel(), output_type=Invoice)
result = agent.run_sync("发票内容")
# TestModel 自动生成符合 Invoice schema 的"假对象"
```

可以指定假数据：

```python
TestModel(custom_output_text="hello world")
TestModel(custom_output_args={"amount": 100.0, "vendor": "X", "date": "2024-01-01"})
```

### 7.2 FunctionModel —— 自己写"假模型"

```python
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

def fake(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    # 自己决定返回什么
    user_msg = messages[-1].parts[-1].content
    return ModelResponse(parts=[TextPart(content=f"假回答：{user_msg}")])

agent = Agent(FunctionModel(fake))
```

**适合单元测试里精确控制模型行为**，比如模拟"先调工具再回答"。

---

## 8. 价格与延迟参考

| 模型 | 价格（input/output 每 1M token） | 延迟 | 适用 |
|------|---------------------------------|------|------|
| OpenAI gpt-4o-mini | $0.15 / $0.60 | 中 | 日常 / 高并发 |
| OpenAI gpt-4o | $2.50 / $10.00 | 中 | 高质量 |
| Anthropic Claude 3.5 Haiku | $0.80 / $4.00 | 快 | 高并发 |
| Anthropic Claude Sonnet 4.5 | $3.00 / $15.00 | 中 | 高质量、长上下文 |
| Gemini 1.5 Flash | $0.075 / $0.30 | 快 | 性价比之王 |
| Gemini 1.5 Pro | $1.25 / $5.00 | 中 | 长上下文（1M+） |
| Groq Llama 3.3 70B | $0.59 / $0.79 | **极快** | 实时聊天 |
| DeepSeek-V3 | $0.27 / $1.10 | 中 | 国产平替 |

⚠️ 价格随时变，请以官方为准。本表 2026-05 数据。

---

## 9. 实战：3 行切模型对比

```python
from pydantic_ai import Agent

prompt = "用一句话讲清楚 Python GIL。"

for m in ["openai:gpt-4o-mini", "anthropic:claude-3-5-haiku-latest", "groq:llama-3.3-70b-versatile"]:
    agent = Agent(m)
    r = agent.run_sync(prompt)
    print(f"[{m}] → {r.output}")
```

**只有模型字符串变，业务代码完全不动**——这就是 Pydantic AI 模型无关的核心价值。

---

## 10. vs LangChain

| 任务 | LangChain | Pydantic AI |
|------|-----------|-------------|
| 用 OpenAI | `ChatOpenAI(model="gpt-4o")` | `Agent("openai:gpt-4o")` |
| 用 Anthropic | `ChatAnthropic(model="claude-3-5-sonnet")` | `Agent("anthropic:claude-3-5-sonnet")` |
| 切 OpenRouter | 装 `langchain-openrouter` 或自己拼 base_url | `OpenAIProvider(base_url=...)` |
| 失败降级 | `chain.with_fallbacks([backup])` | `FallbackModel(primary, backup)` |
| 单测 mock | `FakeListChatModel` | `TestModel` / `FunctionModel` |

最大差别：**LangChain 一家一个 partner 包**（`langchain-openai`、`langchain-anthropic`、`langchain-google-genai`...），Pydantic AI **一个 slim 包 + extras**。

---

## 11. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| `OpenAIError: api_key client option must be set` | 没 load .env | `load_dotenv()` 加在 import 前 |
| `Unknown model 'openai:gpt-5'` | 拼错模型名 | 查 OpenAI docs 确认 |
| Anthropic 没流式工具结果 | Claude tools 流式 chunk 与 OpenAI 不同 | 已抹平，但要用 `stream_text(delta=True)` |
| Gemini 报 400 schema | Gemini schema 子集比 OpenAI 严格 | 简化 Pydantic Model，去掉 Union[None, X] 用 Optional |
| Bedrock 报 ValidationException | 模型 ID 拼错（要带 region 前缀的版本号） | 查 AWS 控制台精确 model ID |
| Ollama 工具调用不灵 | 本地小模型不会 function calling | 换 qwen2.5 / llama3.3 这种支持的 |
| `FallbackModel` 不触发 | 错误不是网络错而是业务错 | 业务错本就不该重试 |
| `ModelSettings(temperature=0)` 还是随机 | 模型不支持或被 provider 忽略 | 检查模型文档 |

---

## 12. 本章 demo

完整可运行代码：[`demos/basics/04_models_providers.py`](../../demos/basics/04_models_providers.py)

下一章：[05-dependencies.md](05-dependencies.md) —— 依赖注入。
