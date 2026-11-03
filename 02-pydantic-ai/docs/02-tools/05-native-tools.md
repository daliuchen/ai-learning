# Pydantic AI 02-05：Native Tools 模型内置工具

> 有一类工具不用你写函数体：OpenAI 的 web search、Anthropic 的 code execution、Google 的 url context，都由模型 provider 在自己的云端执行。Pydantic AI 把它们统一成 native tools，你只要声明要用哪个。这篇讲怎么挂载、各家支持到什么程度，以及怎么写才能在 provider 之间切换。

以 `pydantic-ai 2.64.0` 为准。V1 的 `builtin_tools=` 在 V2 改名为 native tools，通过 `NativeTool` capability 挂载。千问等兼容端点没有原生工具，本篇示例需要 Anthropic、OpenAI Responses 或 Google 的 key。

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
| 跨 provider | 可以 | 可以 | 不一定，看各家支持情况 |
| 例子 | 查 DB | DDG 搜索 | OpenAI web_search / Anthropic code execution |

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
    AdvisorTool,         # 让快模型在生成中途咨询强模型（Anthropic、OpenRouter）
)
```

所有 native tool 都可以从 `pydantic_ai`（或 `pydantic_ai.native_tools`）顶层导入。

---

## 3. 怎么用：`capabilities=[NativeTool(...)]`

和 function tool 不同，native tool 通过 `capabilities` 注册：

```python
from pydantic_ai import Agent, WebSearchTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-5-5',
    capabilities=[NativeTool(WebSearchTool())],
)

result = agent.run_sync('Biggest AI news this week.')
print(result.output)
```

为什么要包一层 `NativeTool(...)`？capability 是 Pydantic AI 挂载模型层能力的统一入口，`NativeTool` 是其中一种。

---

## 4. WebSearchTool

用得最多的 native 工具。能配哪些参数，因 provider 而异：

```python
from pydantic_ai import Agent, WebSearchTool, WebSearchUserLocation
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-5-5',
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

### 4.1 Provider 支持情况

| Provider | WebSearch | 备注 |
|----------|-----------|------|
| OpenAI Responses （`openai:` 默认就是 Responses API） | 支持 | 必须用 Responses API |
| Anthropic （`anthropic:`） | 支持 | 全部特性 |
| Google (`google:gemini-...`) | 支持 | 不支持参数（带限制参数会被视为不支持）；Gemini 3 起可以和 function tools 同时用，更早的 Gemini 不行 |
| OpenRouter | 支持 | 走 OpenRouter 的 web-search server tool |
| xAI (`xai:grok-...`) | 支持 | 支持 `blocked_domains` / `allowed_domains` |
| Groq | 支持 | 仅 compound models |
| OpenAI Chat Completions（`openai-chat:`，包括千问等兼容端点） | 不支持 | 改用 Responses API（`openai:`） |
| Bedrock / Mistral / Cohere | 不支持 | |

### 4.2 参数支持情况

| 参数 | OpenAI | Anthropic | xAI | Groq |
|------|--------|-----------|-----|------|
| `search_context_size` | 支持 | 不支持 | 不支持 | 不支持 |
| `user_location` | 支持 | 支持 | 不支持 | 不支持 |
| `blocked_domains` | 不支持 | 支持 | 支持 | 支持 |
| `allowed_domains` | 支持 | 支持 | 支持 | 支持 |
| `max_uses` | 不支持 | 支持 | 不支持 | 不支持 |

> Anthropic 上 `blocked_domains` 和 `allowed_domains` 只能二选一。

---

## 5. CodeExecutionTool

让模型在 provider 的沙箱里跑代码，做计算和数据分析很合适：

```python
from pydantic_ai import Agent, CodeExecutionTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-5-5',
    capabilities=[NativeTool(CodeExecutionTool())],
)

r = agent.run_sync('Calculate the factorial of 50 and tell me the digit count.')
print(r.output)
```

支持情况：

| Provider | Code Execution | 备注 |
|----------|----------------|------|
| OpenAI | 支持 | 生成的图（charts）可以放到 `result.response.images` |
| Anthropic | 支持 | Sonnet 4+ |
| Google | 支持 | Gemini 3 起可与 function tools 共用 |
| xAI | 支持 | 全部特性 |
| Bedrock | 支持 | 仅 Nova 2.0 |
| 其他 | 不支持 | |

### 5.1 拿到执行日志和生成的图

执行细节在 `result.response.native_tool_calls` 里：

```python
for call, ret in result.response.native_tool_calls:
    print(call.tool_name, call.args)
    print(ret.content)  # stdout / stderr / files
```

在 OpenAI 上想把生成的图当作输出，要打开对应的 model setting：

```python
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai import BinaryImage

agent = Agent(
    'openai:gpt-6.1-sol',
    capabilities=[NativeTool(CodeExecutionTool())],
    output_type=BinaryImage,
    model_settings=OpenAIResponsesModelSettings(openai_include_code_execution_outputs=True),
)
```

---

## 6. ImageGenerationTool

生成图片：

```python
from pydantic_ai import Agent, BinaryImage, ImageGenerationTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'openai:gpt-6.1-sol',
    capabilities=[NativeTool(ImageGenerationTool())],
    output_type=BinaryImage,
)

r = agent.run_sync('Draw an axolotl wearing a hat.')
assert isinstance(r.output, BinaryImage)
```

目前只有 OpenAI Responses 和 Google（Gemini Image 模型）支持。

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

## 7. WebFetchTool

把网页内容抓进上下文：

```python
from pydantic_ai import Agent, WebFetchTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-5-5',
    capabilities=[NativeTool(WebFetchTool())],
)

r = agent.run_sync('Summarize https://pydantic.dev/docs/ai')
```

目前支持它的是 Anthropic 和 Google。在 Google 上，同一个 `WebFetchTool` 会被映射成 Gemini 的 URL context 工具，不需要换类（V1 时代单独的 `UrlContextTool` 在 V2 里已经没有了）。

> 要在所有 provider 上都能用，换成 `WebFetch` capability，它是更高一层的封装，不支持时自动回退到 common 的 `web_fetch_tool`。

---

## 8. MemoryTool / FileSearchTool / XSearchTool / MCPServerTool

- `MemoryTool`：让模型自己读写长期记忆，Anthropic 先提出，OpenAI 随后也支持了
- `FileSearchTool`：基于上传文件做 RAG（OpenAI Vector Stores / Anthropic Files）
- `XSearchTool`：搜 X/Twitter，只有 xAI 有
- `MCPServerTool`：把远程 MCP 服务当 native 工具，由 provider 代为通信（更高层的写法是 `MCP(url, native=True)` capability，不支持时回退到本地连接）
- `AdvisorTool`：让执行任务的快模型在生成途中去问一个更强的顾问模型（Anthropic、OpenRouter 支持）

用法都一样，用 `NativeTool(...)` 包一下：

```python
from pydantic_ai import Agent, MemoryTool, FileSearchTool, MCPServerTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'openai:gpt-6.1-sol',
    capabilities=[
        NativeTool(MemoryTool()),
        NativeTool(FileSearchTool(vector_store_ids=['vs_xxx'])),
    ],
)
```

---

## 9. 动态配置：`NativeTool(callable)`

`WebSearchTool` 经常要按用户所在地定制。可以给 `NativeTool` 传一个函数，每次运行时动态生成工具：

```python
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic_ai.capabilities import NativeTool

async def prepared_web_search(ctx: RunContext[dict]) -> WebSearchTool | None:
    if not ctx.deps.get('location'):
        return None  # 没地理位置就不开搜索
    return WebSearchTool(user_location={'city': ctx.deps['location']})

agent = Agent(
    'openai:gpt-6.1-sol',
    capabilities=[NativeTool(prepared_web_search)],
    deps_type=dict,
)
```

返回 `None`，这次运行就不开这个工具；返回工具实例就用它。思路和 function tool 的 `prepare` 钩子一样。

---

## 10. 跨 provider 写法：用 capability

项目要在几家 provider 之间切换时，不要写死 `NativeTool(...)`，改用更高层的 capability：

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch, WebFetch, ImageGeneration, MCP

agent = Agent(
    'openai:gpt-6.1-sol',
    capabilities=[WebSearch(local='duckduckgo'), WebFetch(local=True), ImageGeneration()],
)
```

当前模型支持原生工具就用原生的；不支持时，回退到你通过 `local=` 指定的 common tool（比如 `duckduckgo_search_tool`）。业务代码不用改，换的只是 `Agent(model=...)` 里的字符串。

---

## 11. 和自定义工具的差异

| 维度 | Function Tool | Native Tool |
|------|---------------|-------------|
| 你写函数体 | 必须 | 不用 |
| 注册方式 | `tools=[]` / `@agent.tool` | `capabilities=[NativeTool(...)]` |
| 控制粒度 | 完全可控 | 取决于 provider 暴露多少参数 |
| 计费 | 你付第三方的费用 | provider 收工具费 |
| 失败重试 | `ModelRetry` | provider 内部处理 |
| 观测 | `ToolCallPart` / `ToolReturnPart` | `NativeToolCallPart` / `NativeToolReturnPart` |
| 与 function tool 共存 | / | OpenAI / Anthropic 可以；Google 的 Gemini 3 起可以，更早的不行 |

> Google 有个限制：Gemini 3 之前的模型用了 native tool，就不能同时用 function tool 或 tool-output 模式，要结构化输出得用 `PromptedOutput`。Gemini 3 起支持这些组合（包括 `NativeOutput`）。

---

## 12. 实战：搜索 + 代码执行的数据 Agent

```python
from pydantic_ai import Agent, CodeExecutionTool, WebSearchTool
from pydantic_ai.capabilities import NativeTool

agent = Agent(
    'anthropic:claude-sonnet-5-5',
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

模型会先搜索、再计算、最后解释，每一步都在 Anthropic 那边执行，本地一个函数都不用写。

---

## 13. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `UserError: provider does not support WebSearchTool` | 模型不支持 | 换 provider，或用 `WebSearch` capability 回退 |
| OpenAI 上 `WebSearchTool` 不工作 | 用了 Chat Completions API（`openai-chat:`） | 改成 `openai:...`（Responses） |
| 老版 Gemini 上同时用 native 和 function tool 报错 | Gemini 3 之前不支持这种组合 | 二选一，结构化输出用 `PromptedOutput`；或换 Gemini 3 |
| 千问 / DeepSeek 等兼容端点用 native 工具报 `UserError` | 它们走 Chat Completions，没有原生工具 | 用 `WebSearch(local=...)` 这类 capability 回退到本地工具 |
| Anthropic 同时传 `blocked_domains` 和 `allowed_domains` 报错 | Anthropic 上两者互斥 | 二选一 |
| 取不到 native 工具的结果 | 找错地方了 | 看 `result.response.native_tool_calls`，不是 `all_messages()` |
| OpenAI code execution 没出图 | 没开 model setting | `OpenAIResponsesModelSettings(openai_include_code_execution_outputs=True)` |
| 费用突然上涨 | native 工具单独计费 | 用 `max_uses` 限制，用 Logfire 追踪 |
| 流式时拿不到 native tool 事件 | Google 上不发流 | 用非流式 |

---

## 14. 生产建议

1. 要跨 provider，就用 capability（`WebSearch()` / `WebFetch()` / `ImageGeneration()`），别写死 `NativeTool(...)`
2. 用 OpenAI 的 native tool 要走 Responses API：V2 里 `openai:...` 默认就是 Responses，`openai-chat:...`（Chat Completions）不支持
3. 一定要设 `max_uses` / `max_results`，native 工具的费用很容易失控
4. 老版 Gemini 项目引入 native tool 要谨慎，会让 function tool 和 tool-output 不能用（Gemini 3 起已经放开）
5. 用 Logfire 监控 `native_tool_calls`，这部分单独计费
6. 需要按条件启用时用 `NativeTool(callable)`，按 deps 决定开不开

---

## 15. 本章 demo

完整可运行代码：[`demos/tools/05_native_tools.py`](../../demos/tools/05_native_tools.py)

工具部分的五篇到这里结束。相关阅读：

- [Pydantic AI 进阶 08：Deferred Tools 与人审批](../03-advanced/08-deferred-tools.md)：人在回路、审批、外部执行
- [Pydantic AI 进阶 06：Capabilities（能力）与 ModelProfile](../03-advanced/06-capabilities.md)：capability，以及 `WebSearch` / `WebFetch` 这类按 provider 自动适配的能力
- [Pydantic AI 官网](https://pydantic.dev/docs/ai) 的 *Native Tools* 与 *Deferred Tools* 章节
