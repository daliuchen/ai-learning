# Pydantic AI 04：模型与 Provider 全览

> Pydantic AI 用统一的 `Model` 抽象接了 30 多家 LLM 厂商，换厂商通常只改一个字符串，比如 `"openai:gpt-6.1-sol"` 改成 `"anthropic:claude-sonnet-5-5"`；千问这类 OpenAI 兼容端点用 `"openai-chat:qwen-flash"` + `OPENAI_BASE_URL`，或者内置的 `alibaba:` provider。

以 `pydantic-ai 2.64.0` 为准。文中的模型名只用来演示格式。

---

## 1. 三种指定模型的方式

```python
from pydantic_ai import Agent

# 方式 A：字符串简写（最常用）
agent = Agent("openai:gpt-6.1-sol")

# 方式 B：Model 对象（需要自定义设置 / client 时）
from pydantic_ai.models.openai import OpenAIChatModel
agent = Agent(OpenAIChatModel("gpt-6.1-sol"))

# 方式 C：Model 对象 + Provider 对象（最完整控制：base_url / api_key / 自定义 http client）
from pydantic_ai.providers.openai import OpenAIProvider
provider = OpenAIProvider(api_key="sk-xxx", base_url="https://api.openai.com/v1")
agent = Agent(OpenAIChatModel("gpt-6.1-sol", provider=provider))
```

大多数场景用方式 A 就够了，要切 OpenRouter、Azure 或自己的代理时用方式 C。

官方文档里有三个术语，最好先分清：

- Model：按某家 API 格式发请求的类（如 `OpenAIChatModel`），负责抹平各家消息 / 工具格式差异；
- Provider：负责认证与连接（`OpenAIProvider(base_url=...)`），换 endpoint 就是换 provider；
- Profile：描述某个模型的请求该怎么构造，比如支持哪些输出模式、JSON Schema 方言限制、能不能看图等。`model.profile` 是个 `TypedDict`，用 `.get()` 读（见 [Capabilities（能力）与 ModelProfile](../03-advanced/06-capabilities.md)）。

---

## 2. 字符串简写一览

格式是 `"<provider>:<model_id>"`。V2 的常见前缀（完整目录见官方 Models 页）：

| 前缀 | 说明 |
|------|------|
| `openai:` | 默认走 Responses API（`OpenAIResponsesModel`）；V1 时它是 Chat Completions |
| `openai-chat:` | Chat Completions API（`OpenAIChatModel`），第三方 OpenAI 兼容端点用这个 |
| `openai-responses:` | 显式 Responses API |
| `anthropic:` | Claude（`AnthropicModel`） |
| `google:` | Gemini API（`GoogleModel`）；Vertex 走 `google-cloud:` |
| `bedrock:` / `azure:` / `google-cloud:` | 云平台（Anthropic 等模型也可从这些云上访问） |
| `groq:` `mistral:` `cohere:` `xai:` `cerebras:` `deepseek:` `moonshotai:` `zai:` `huggingface:` | 各家 |
| `openrouter:` `vercel:` `litellm:` `together:` `fireworks:` `vllm:` `ollama:` ... | 网关 / 推理平台 / 本地 |
| `alibaba:` | 阿里云百炼（千问），默认连国际站，国内要改 `base_url`（见 §3.4） |
| `gateway/<provider>:` | Pydantic AI Gateway |
| `test` | 内置测试模型（`Agent("test")`） |

> V1 的 `google-gla:` / `google-vertex:` 前缀在 V2 里已经收敛为 `google:` / `google-cloud:`。除 OpenAI / Anthropic / Google 外，其它 provider 需要装对应 extra，例如 `pip install "pydantic-ai[groq]"`。

---

## 3. 各家 Provider 详解

### 3.1 OpenAI

环境变量：`OPENAI_API_KEY`。有两个模型类：

```python
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

# Chat Completions API（兼容性最好，几乎所有 OpenAI 兼容服务都支持）
agent = Agent(OpenAIChatModel("gpt-6.1-sol"))

# Responses API（OpenAI 新接口，V2 的 "openai:" 默认走它；支持服务端状态、原生工具等）
agent = Agent(OpenAIResponsesModel("gpt-6.1-sol"))
```

自定义 base_url（走代理或 OpenAI 兼容服务）：

```python
from pydantic_ai.providers.openai import OpenAIProvider

provider = OpenAIProvider(api_key="sk-xxx", base_url="https://api.deepseek.com/v1")
agent = Agent(OpenAIChatModel("deepseek-chat", provider=provider))
```

`OpenAIProvider` 不传 `base_url` 时会读 `OPENAI_BASE_URL` 环境变量（OpenAI SDK 的行为），所以只设环境变量也能接千问：

```bash
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

```python
agent = Agent("openai-chat:qwen-flash")     # 本手册 demo 就是这么接的
```

### 3.2 Anthropic

环境变量：`ANTHROPIC_API_KEY`。

```python
from pydantic_ai.models.anthropic import AnthropicModel

agent = Agent(AnthropicModel("claude-sonnet-5-5"))
# 或者
agent = Agent("anthropic:claude-sonnet-5-5")
```

几点区别：

- Claude 不支持 OpenAI 的 `seed`、`logprobs` 等参数；
- 思考（thinking）有 Anthropic 专属设置，也可以用统一的 `Thinking` capability（见 [Thinking（显式思考链）](../03-advanced/03-thinking.md)）；
- 流式 + 工具调用的 chunk 形态与 OpenAI 不同（Pydantic AI 已抹平）。

### 3.3 Google Gemini

```python
agent = Agent("google:gemini-4-argon")        # Gemini API（API key：GOOGLE_API_KEY / GEMINI_API_KEY）
agent = Agent("google-cloud:gemini-4-argon")  # Vertex AI（企业 GCP，走 gcloud 默认认证）
```

> V1 时代是 `google-gla:` / `google-vertex:`，V2 统一了前缀；具体可用的模型 id 以 Google 文档为准。

### 3.4 阿里云百炼 / 千问（本手册 demo 用）

有两种接法。

第一种是 OpenAI 兼容端点加 `openai-chat:`，本手册 demo 就是这么做的，依赖最少：

```python
# 环境变量 OPENAI_API_KEY + OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
agent = Agent("openai-chat:qwen-flash")
```

第二种是内置的 `alibaba:` provider：

```python
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.alibaba import AlibabaProvider

# 读 ALIBABA_API_KEY 或 DASHSCOPE_API_KEY
agent = Agent("alibaba:qwen-max")

# ⚠️ AlibabaProvider 默认连国际站 dashscope-intl.aliyuncs.com，国内账号要改 base_url
model = OpenAIChatModel(
    "qwen-max",
    provider=AlibabaProvider(
        api_key="your-api-key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",   # 中国区
    ),
)
agent = Agent(model)
```

用千问时有两个限制：

- DashScope 的兼容模式 Chat Completions 不接受文档内容部件：传 `DocumentUrl` / 文档 `BinaryContent`（PDF 等）会被服务端拒绝，图片可以；
- 千问的思考模式要在请求里加 `enable_thinking`（见 [Thinking（显式思考链）](../03-advanced/03-thinking.md)）。

### 3.5 Groq / Mistral / Cohere / xAI ……

```python
agent = Agent("groq:llama-3.3-70b-versatile")      # 需要 pip install "pydantic-ai[groq]"
agent = Agent("mistral:mistral-large-latest")      # [mistral]
agent = Agent("cohere:command-r-plus")             # [cohere]
```

### 3.6 AWS Bedrock

```python
agent = Agent("bedrock:us.anthropic.claude-sonnet-5-5")      # 需要 [bedrock]，走 boto3；model id 以 Bedrock 控制台为准
```

需要 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`。注意 Bedrock 的 SDK 自己会重试，重试预算别和别的层叠加（见[HTTP 重试与 ModelRetry](../03-advanced/07-retries-http.md)）。

### 3.7 Hugging Face

```python
agent = Agent("huggingface:Qwen/Qwen3-235B-A22B")   # 需要 [huggingface] 与 HF_TOKEN
```

### 3.8 Ollama（本地模型）

```python
agent = Agent("ollama:qwen3:14b")     # 默认连 http://localhost:11434/v1，可用 OLLAMA_BASE_URL 改
```

本地小模型对 function calling 的支持参差不齐，建议用原生支持工具的模型。

---

## 4. OpenAI 兼容生态

很多服务的接口和 OpenAI 一样，V2 给常见的几家做了专门的前缀（`deepseek:` `together:` `fireworks:` `openrouter:` `vllm:` `litellm:` ……），也可以走 `OpenAIChatModel` + 自定义 provider：

| 服务 | 前缀 / base_url | 备注 |
|------|----------------|------|
| DeepSeek | `deepseek:` | 国产，便宜 |
| 千问（DashScope） | `alibaba:` 或 `openai-chat:` + base_url | 见 §3.4 |
| OpenRouter | `openrouter:` | 多模型聚合 |
| Together / Fireworks | `together:` / `fireworks:` | 开源模型托管 |
| vLLM / LiteLLM | `vllm:` / `litellm:` | 自建推理 / 网关 |
| Azure OpenAI | `azure:` / `azure-responses:` | 企业 Azure |
| 其它任意兼容端点 | `OpenAICompatibleProvider(name, base_url=...)` | 官方 Other endpoints |

通用写法：

```python
provider = OpenAIProvider(api_key=os.getenv("XXX_KEY"), base_url="...")
agent = Agent(OpenAIChatModel("model-name", provider=provider))
```

关于 HTTP 客户端的生命周期：provider 自己创建的 HTTP client 由它管。长期运行的服务里，用 `async with agent:`（或 model / provider 作上下文）让连接在退出时关闭；不用的话，agent 自己的模型对应的 client 会一直保持，直到进程结束；而每次运行时传字符串模型名（`agent.run(..., model="openai:...")`）会每次新建 provider 与连接池，运行结束再关闭。默认超时 600 秒（连接 5 秒），连接池最多 1000 条、保活 100 条。

---

## 5. ModelSettings：温度、token 上限等

```python
from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

agent = Agent(
    "openai:gpt-6.1-sol",
    model_settings=ModelSettings(
        temperature=0.0,
        max_tokens=1000,
        top_p=0.95,
        seed=42,
        timeout=30,
        stop_sequences=["\n\n###"],
        max_retries=0,       # provider SDK 层的重试次数（和传输层重试相乘，见 03-advanced/07）
    ),
)
```

`ModelSettings` 在 V2 里是个 `TypedDict`，所以直接写字典 `model_settings={"temperature": 0}` 同样可以。它还有一个统一的 `thinking` 字段（见[Thinking（显式思考链）](../03-advanced/03-thinking.md)）。单次调用也可以临时覆盖：

```python
agent.run_sync("...", model_settings={"temperature": 0.9})
```

设置有三层，后者覆盖前者：模型自带的 → agent 构造时的 → 运行时传的。注意各家支持的参数不一样（OpenAI / Anthropic / Gemini 各有专属设置，如 `OpenAIResponsesModelSettings`、`AnthropicModelSettings`）：

| 参数 | OpenAI | Anthropic | Gemini | Groq |
|------|--------|-----------|--------|------|
| `temperature` | 支持 | Claude 5.x 已移除 | 支持 | 支持 |
| `max_tokens` | 支持 | 支持 | 支持 | 支持 |
| `top_p` | 支持 | Claude 5.x 已移除 | 支持 | 支持 |
| `seed` | 支持 | 不支持 | 支持 | 支持 |
| `stop_sequences` | 支持 | 支持 | 支持 | 支持 |
| `timeout` | 支持 | 支持 | 支持 | 支持 |

Claude 5.x（Opus / Sonnet / Haiku 5.5 等）收到 `temperature`、`top_p`、`top_k` 会直接返回 400，用 Claude 时不要在 `model_settings` 里设这几项；想要输出稳定，用结构化输出，或者多次采样取多数结果。

其余不支持的参数会被默默忽略，不报错。这样省事，但调试时一定要确认参数真的生效了，比如去 Logfire trace 里看实际发出的请求。

---

## 6. 模型回退：`ModelFallback` 与 `FallbackModel`

主模型挂了自动切备用。V2 推荐用 `ModelFallback` capability，老的 `FallbackModel` 包装类仍然可用：

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import ModelFallback

# 先试 agent 自己的模型，失败了依次试后面的
agent = Agent("openai:gpt-6.1-sol", capabilities=[ModelFallback("anthropic:claude-sonnet-5-5")])

# 老写法：把多个模型包成一个
from pydantic_ai.models.fallback import FallbackModel
agent = Agent(FallbackModel("openai:gpt-6.1-sol", "anthropic:claude-sonnet-5-5"))
```

默认在请求抛 `ModelAPIError` 时回退（provider API 报的 4xx / 5xx，含 429 限流、500/502/503）。这些情况不回退：Pydantic AI 自己的校验失败、`ModelRetry`、本地代码异常。可以用 `fallback_on=` 自定义：异常类型元组、异常处理函数、甚至「响应处理函数」（比如把 `finish_reason == 'length'` 的截断响应也当作失败）。

回退和重试是两回事：下一个模型通常用不上前一个模型建好的提示词缓存，答案和价格都可能不同；瞬时故障（连接断了）留给传输层 / SDK 重试，回退只处理这个模型暂时用不了的情况。

---

## 7. TestModel / FunctionModel

### 7.1 TestModel：完全 mock

```python
from pydantic_ai.models.test import TestModel
from pydantic_ai import Agent

agent = Agent(TestModel(), output_type=Invoice)       # 或 Agent("test", output_type=Invoice)
result = agent.run_sync("发票内容")
# TestModel 自动生成符合 Invoice schema 的"假对象"（字符串字段是 'a'，数字是 0）
```

可以指定假数据；还可以让它只调某些工具：

```python
TestModel(custom_output_text="hello world")
TestModel(custom_output_args={"amount": 100.0, "vendor": "X", "date": "2024-01-01"})
TestModel(call_tools=["get_weather"])      # 默认会调用所有工具，各传入占位参数
```

### 7.2 FunctionModel：自己写假模型

```python
from pydantic_ai.models.function import FunctionModel, AgentInfo
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart

def fake(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    user_msg = messages[-1].parts[-1].content
    return ModelResponse(parts=[TextPart(content=f"假回答：{user_msg}")])

agent = Agent(FunctionModel(fake))
```

单元测试里要精确控制模型行为时用它，比如模拟先调工具再回答。需要流式时再传 `stream_function`。

---

## 8. 价格与用量

价格表过时太快，这里不列了，只说两件事：

- Pydantic AI 内置了模型价格表（来自 genai-prices），每次响应的 `usage` 里能直接算出 `cost`；新模型发布后用 `prices.update_in_background()` 每小时刷新；
- Pydantic AI Gateway：一个 key 访问 OpenAI / Anthropic / Google Cloud / Groq / Bedrock，带花费上限与成本监控（走 Logfire）。

比起记价格，我更建议记选型思路：日常和高并发用便宜的快模型（flash / haiku / mini 档），难任务再切大模型；不赶时间的离线批处理，OpenAI / Anthropic / Gemini 的 batch API 大约半价（`model_request_batch`，见官方 Batch 页）。

---

## 9. 实战：3 行切模型对比

```python
from pydantic_ai import Agent

prompt = "用一句话讲清楚 Python GIL。"

for m in ["openai-chat:qwen-flash", "anthropic:claude-haiku-5-5", "groq:llama-3.3-70b-versatile"]:
    agent = Agent(m)
    r = agent.run_sync(prompt)
    print(f"[{m}] → {r.output}")
```

只有模型字符串在变，业务代码一行没动，这就是模型无关的好处。

---

## 10. vs LangChain

| 任务 | LangChain | Pydantic AI |
|------|-----------|-------------|
| 用 OpenAI | `ChatOpenAI(model="...")` / `init_chat_model("openai:...")` | `Agent("openai:...")` |
| 用 Anthropic | `ChatAnthropic(...)` | `Agent("anthropic:...")` |
| 切 OpenRouter / 兼容端点 | `ChatOpenAI(base_url=...)` | `Agent("openrouter:...")` / `OpenAIProvider(base_url=...)` |
| 失败降级 | middleware（如 `ModelFallbackMiddleware`）/ `with_fallbacks` | `ModelFallback` capability / `FallbackModel` |
| 单测 mock | `GenericFakeChatModel` | `TestModel` / `FunctionModel` |

最大的差别在打包：LangChain 一家一个 partner 包（`langchain-openai`、`langchain-anthropic`……），Pydantic AI 是一个包加 extras。另外 Pydantic AI 把 model / provider / profile 拆成三层，同一个模型换个 endpoint 时只需换 provider。

---

## 11. 常见坑

| 现象 | 原因 | 解法 |
|------|------|------|
| `UserError: Set the OPENAI_API_KEY ...` | 没 load .env；V2 创建 Agent 时就检查 | `load_dotenv()`；或 `defer_model_check=True` |
| 接千问 / DeepSeek 用 `openai:` 报 404 / 400 | V2 的 `openai:` 走 Responses API，第三方兼容端点一般不支持 | 用 `openai-chat:` |
| `alibaba:` 连不上 / 认证失败（国内账号） | `AlibabaProvider` 默认连国际站 | 传 `base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"` |
| 千问传 PDF 报 500 | 兼容模式不接受文档部件 | 先抽文本再传；或换支持文档的 provider |
| `Unknown model` / provider 找不到 | 拼错前缀，或没装对应 extra | 查官方 Models 页；`pip install "pydantic-ai[groq]"` |
| Gemini 报 400 schema | Gemini schema 子集比 OpenAI 严格 | 简化 Pydantic Model；必要时用 `PromptedOutput`（见[结构化输出（Output Types）](06-output-types.md)） |
| Ollama 工具调用不灵 | 本地小模型不会 function calling | 换原生支持工具的模型 |
| 回退不触发 | 错误不是 `ModelAPIError`（比如是校验错误） | 业务错本就不该回退；需要时自定义 `fallback_on` |
| `ModelSettings(temperature=0)` 还是随机 | 模型不支持或被 provider 忽略 | 检查模型文档 |

---

## 12. 本章 demo

完整可运行代码：[`demos/basics/04_models_providers.py`](../../demos/basics/04_models_providers.py)

下一章：[Pydantic AI 05：依赖注入（Dependencies）](05-dependencies.md)。
