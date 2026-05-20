# Pydantic AI 02-05：Native Tools 模型内置工具

> **一句话**：Native Tools 是"模型 provider 自带、由 provider 在云端执行"的工具（OpenAI 的 web search、Anthropic 的 code execution、Google 的 url context……），Pydantic AI 用统一接口暴露，你只管声明，不用写函数体。

---

## 1. 和 Common / Function Tools 的区别

```
Function tool      你写函数体 → Pydantic AI 在你本地执行 → 把结果送回模型
Common tool        Pydantic AI 帮你写了第三方 SDK 调用 → 也在本地执行
Native tool        ✨ 没有函数体 ✨  模型 provider 直接在它自己的云上执行
```

| 维度 | Function Tool | Common Tool | Native Tool |
|------|---------------|-------------|-------------|
| 函数体谁写 | 你 | Pydantic AI | 不用写 |
| 执行在哪 | 你本地 | 你本地 | provider 云端 |
| 计费 | 模型 token | 模型 token + 第三方 API | 模型 token + provider 工具费 |
| 跨 provider | ✅ | ✅ | ❌（看 provider 支持矩阵） |
| 典型例子 | 查 DB | DDG 搜索 | OpenAI web_search / Anthropic code execution |

---

## 2. 支持的 Native Tools 一览

```python
from pydantic_ai import (
    WebSearchTool,       # 网页搜索
    XSearchTool,         # X/Twitter 搜索（xAI 独占）
    CodeExecutionTool,   # 代码执行
    ImageGenerationTool, # 图片生成
    WebFetchTool,        # 抓网页
    MemoryTool,          # 记忆
    MCPServerTool,       # 把 MCP 当 native（由 provider 代理通信）
    FileSearchTool,      # 文件向量检索（RAG）
)
```

所有 native tool 都从 `pydantic_ai`（或 `pydantic_ai.native_tools`）顶层导出。

---

## 3. 怎么用：`capabilities=[NativeTool(...)]`

跟 function tool 不一样，**native tool 通过 `capabilities` 注册**：

```python
from pydantic_ai import Agent, WebSearchTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[NativeTool(WebSearchTool())],
)

result = agent.run_sync('Biggest AI news this week.')
print(result.output)
```

为什么要包 `NativeTool(...)`？因为 capability 系统是 Pydantic AI 用来挂"模型层能力"的统一入口，`NativeTool` 是其中一个。

---

## 4. WebSearchTool

最常用的 native 工具。配置参数因 provider 而异：

```python
from pydantic_ai import Agent, WebSearchTool, WebSearchUserLocation
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[
        NativeTool(
            WebSearchTool(
                search_context_size='high',
                user_location=WebSearchUserLocation(
                    city='Shanghai',
                    country='CN',
                    region='Shanghai',
                    timezone='Asia/Shanghai',
                ),
                blocked_domains=['example.com'],
                max_uses=5,   # Anthropic only
            )
        )
    ],
)
```

### 4.1 Provider 支持矩阵

| Provider | WebSearch | 备注 |
|----------|-----------|------|
| OpenAI Responses (`openai-responses:gpt-5.2`) | ✅ | 必须用 Responses API |
| Anthropic (`anthropic:claude-sonnet-4-6`) | ✅ | 全特性支持 |
| Google (`google:gemini-...`) | ✅ | 不支持参数；流式时没有 `NativeToolCallPart`；不能同时用 function tools |
| xAI (`xai:grok-...`) | ✅ | 支持 `blocked_domains` / `allowed_domains` |
| Groq | ✅ | 仅 compound models |
| OpenAI Chat Completions | ❌ | 用 Responses API 替代 |
| Bedrock / Mistral / Cohere | ❌ | 不支持 |

### 4.2 参数支持矩阵

| 参数 | OpenAI | Anthropic | xAI | Groq |
|------|--------|-----------|-----|------|
| `search_context_size` | ✅ | ❌ | ❌ | ❌ |
| `user_location` | ✅ | ✅ | ❌ | ❌ |
| `blocked_domains` | ❌ | ✅ | ✅ | ✅ |
| `allowed_domains` | ✅ | ✅ | ✅ | ✅ |
| `max_uses` | ❌ | ✅ | ❌ | ❌ |

> **Anthropic 注意**：`blocked_domains` 和 `allowed_domains` 二选一，不能同时用。

---

## 5. CodeExecutionTool

让模型在 provider 沙箱里跑代码，做计算 / 数据分析最爽：

```python
from pydantic_ai import Agent, CodeExecutionTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[NativeTool(CodeExecutionTool())],
)

r = agent.run_sync('Calculate the factorial of 50 and tell me the digit count.')
print(r.output)
```

支持矩阵：

| Provider | Code Execution | 备注 |
|----------|----------------|------|
| OpenAI | ✅ | 可生成图（charts）到 `result.response.images` |
| Anthropic | ✅ | Sonnet 4+ |
| Google | ✅ | 不能同时用 function tools |
| xAI | ✅ | 全特性 |
| Bedrock | ✅ | 仅 Nova 2.0 |
| 其他 | ❌ | |

### 5.1 拿到执行 log / 生成的图

执行细节在 `result.response.native_tool_calls` 里：

```python
for call, ret in result.response.native_tool_calls:
    print(call.tool_name, call.args)
    print(ret.content)  # stdout / stderr / files
```

OpenAI 上若想拿生成的图作为输出，开 model setting：

```python
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai import BinaryImage

agent = Agent(
    'openai-responses:gpt-5.2',
    capabilities=[NativeTool(CodeExecutionTool())],
    output_type=BinaryImage,
    model_settings=OpenAIResponsesModelSettings(openai_include_code_execution_outputs=True),
)
```

---

## 6. ImageGenerationTool

生成图：

```python
from pydantic_ai import Agent, BinaryImage, ImageGenerationTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'openai-responses:gpt-5.2',
    capabilities=[NativeTool(ImageGenerationTool())],
    output_type=BinaryImage,
)

r = agent.run_sync('Draw an axolotl wearing a hat.')
assert isinstance(r.output, BinaryImage)
```

支持矩阵：仅 **OpenAI Responses** 和 **Google**（Gemini Image 模型）。

配置：

```python
ImageGenerationTool(
    action='generate',      # 'auto' / 'generate' / 'edit'
    quality='high',
    size='1024x1024',
    output_format='png',
    background='transparent',
    aspect_ratio='16:9',    # 部分 provider 支持
)
```

---

## 7. WebFetchTool / UrlContextTool

抓网页内容塞进上下文：

```python
from pydantic_ai import Agent, WebFetchTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[NativeTool(WebFetchTool())],
)

r = agent.run_sync('Summarize https://ai.pydantic.dev')
```

Google 上对应的概念叫 **UrlContextTool**（Gemini 1.5+），用法类似。

> 想跨 provider 通吃，用 `WebFetch` capability（高层抽象，自动 fallback 到 common `web_fetch_tool`）。

---

## 8. MemoryTool / FileSearchTool / XSearchTool / MCPServerTool

- `MemoryTool`：让模型自己写/读"长期记忆"，Anthropic 提出，OpenAI 也跟进
- `FileSearchTool`：基于上传文件的 RAG（OpenAI Vector Stores / Anthropic Files）
- `XSearchTool`：搜 X/Twitter，xAI 独占
- `MCPServerTool`：把远程 MCP 服务当 native 工具，由 provider 代理通信

这些工具用法一致——都是 `NativeTool(...)` 包一下：

```python
from pydantic_ai import Agent, MemoryTool, FileSearchTool, MCPServerTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'openai-responses:gpt-5.2',
    capabilities=[
        NativeTool(MemoryTool()),
        NativeTool(FileSearchTool(vector_store_ids=['vs_xxx'])),
    ],
)
```

---

## 9. 动态配置：`NativeTool(callable)`

`WebSearchTool` 经常要按用户地理位置定制，可以传**一个函数**给 `NativeTool`，每次 run 时动态生成工具：

```python
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic_ai.capabilities import NativeTool

async def prepared_web_search(ctx: RunContext[dict]) -> WebSearchTool | None:
    if not ctx.deps.get('location'):
        return None  # 没地理位置就不开搜索
    return WebSearchTool(user_location={'city': ctx.deps['location']})

agent = Agent(
    'openai-responses:gpt-5.2',
    capabilities=[NativeTool(prepared_web_search)],
    deps_type=dict,
)
```

返回 `None` → 这次 run 不开工具；返回工具实例 → 用它。和 function tool 的 `prepare` 钩子神似。

---

## 10. 跨 provider 写法：用 capability 抽象

如果项目要在多家 provider 之间切，**不要写死 `NativeTool(...)`**，用更高层的 capability：

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch, WebFetch, ImageGeneration, MCP

agent = Agent(
    'openai:gpt-4o-mini',
    capabilities=[WebSearch(), WebFetch(), ImageGeneration()],
)
```

行为：

- 当前 model 支持 native → 用 native
- 不支持 → 自动 fallback 到 common tool（如 `duckduckgo_search_tool`）

业务代码一行不改，只换 `Agent(model=...)` 字符串。

---

## 11. 和自定义工具的差异

| 维度 | Function Tool | Native Tool |
|------|---------------|-------------|
| 你写函数体 | 必须 | 不用 |
| 注册方式 | `tools=[]` / `@agent.tool` | `capabilities=[NativeTool(...)]` |
| 控制粒度 | 完全可控 | 看 provider 暴露多少参数 |
| 计费 | 你掏第三方钱 | provider 收工具费 |
| 失败重试 | `ModelRetry` | provider 内部处理 |
| 观测 | `ToolCallPart` / `ToolReturnPart` | `NativeToolCallPart` / `NativeToolReturnPart` |
| 与 function tool 共存 | / | OpenAI / Anthropic 可以；Google **不行** |

> ⚠️ **Google 限制**：用了 native tool 就**不能同时**用 function tool 或 tool-output mode。要结构化输出请用 `PromptedOutput`。

---

## 12. 实战：搜索 + 代码执行的"数据 Agent"

```python
from pydantic_ai import Agent, CodeExecutionTool, WebSearchTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[
        NativeTool(WebSearchTool(max_uses=3)),
        NativeTool(CodeExecutionTool()),
    ],
    instructions=(
        "You are a data analyst. "
        "1) Search the web for raw data. "
        "2) Use code execution to compute statistics. "
        "3) Cite sources and explain the math."
    ),
)

r = agent.run_sync('Average box-office of top 5 animated films in 2025?')
print(r.output)
```

模型会自己"先搜、再算、最后讲"，每一步都在 Anthropic 云端跑，本地不写一行函数。

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `UserError: provider does not support WebSearchTool` | 模型不支持 | 切 provider，或用 `WebSearch` capability fallback |
| OpenAI 上 `WebSearchTool` 不工作 | 用了 Chat Completions API | 切到 `openai-responses:...` |
| Google 上同时用 native + function tool 报错 | Google 限制 | 二选一，结构化输出用 `PromptedOutput` |
| Anthropic `blocked_domains` + `allowed_domains` 同传报错 | Anthropic 互斥 | 二选一 |
| native 工具结果取不到 | 看错地方了 | `result.response.native_tool_calls` 不是 `all_messages()` |
| OpenAI code execution 没出图 | 没开 model setting | `OpenAIResponsesModelSettings(openai_include_code_execution_outputs=True)` |
| 费用突然飙升 | native 工具有自己的计费 | 用 `max_uses` 限制 / Logfire 追 |
| 流式时拿不到 native tool 事件 | Google 上不发流 | 用非流式 |

---

## 14. 生产建议

1. **跨 provider 一律用 capability 抽象**（`WebSearch()` / `WebFetch()` / `ImageGeneration()`），别写死 `NativeTool(...)`
2. **接 OpenAI native tool 必须用 `openai-responses:...` 模型字符串**，别用普通 `openai:...`
3. **`max_uses` / `max_results` 一定要设**，native 工具的成本很容易翻车
4. **Google 项目谨慎引入 native tool**，会冲掉 function tool / tool-output
5. **Logfire 监控 `native_tool_calls`**，分别计费
6. **动态启用用 `NativeTool(callable)`**，按 deps 决定开不开

---

## 15. 本章 demo

完整可运行代码：[`demos/tools/05_native_tools.py`](../../demos/tools/05_native_tools.py)

工具系统五篇到此结束。接下来你可以看：

- [《工具与 RAG 实战》](../03-advanced/) （正在写）
- [Pydantic AI 官网](https://ai.pydantic.dev) 的 `Deferred Tools` 章节，深入人在回路

