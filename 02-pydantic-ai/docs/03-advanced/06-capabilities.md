# Pydantic AI 进阶 06：Capabilities 与 ModelProfile

> **一句话**：`ModelProfile` 描述了"这个模型支持哪些能力（capabilities）"，Pydantic AI 在 Agent 启动时会读它来决定**走 NativeOutput 还是 PromptedOutput**、**用不用工具**、**思维链怎么解析**等等，**搞清楚 Profile 才能真正驾驭多 Provider**。

---

## 1. 为什么要"能力描述"

LLM 这个世界很碎：

- OpenAI gpt-4o 系列支持 **JSON Schema 严格模式**，而 gpt-3.5 不支持
- Claude 3.5 支持 **strict tools**，老 Claude 不支持
- Gemini 的 JSON Schema 不接受 `additionalProperties: false`，必须先转换
- DeepSeek-R1、OpenAI o-series 是 **always-thinking** 模型，不能手动关闭
- 某些本地 Ollama 模型完全没有 tools 支持

Pydantic AI 想做"模型无关"，就必须有一个**统一的能力描述**，让 Agent 在运行前能知道"这个模型能干啥、要不要走兜底路径"。这个描述就是 `ModelProfile`。

它解决三类问题：

1. **兼容性检查**：你想用 `NativeOutput` 但模型不支持，框架在启动时就要告诉你
2. **自动 fallback**：模型不支持 native JSON schema，自动降级为 `PromptedOutput`
3. **Schema 转换**：Gemini / Anthropic / OpenAI 对 JSON Schema 的要求各不一样，profile 里挂个 transformer 自动转

---

## 2. ModelProfile 的核心属性

`pydantic_ai.profiles.ModelProfile` 的字段不多但都关键：

| 字段 | 类型 | 含义 |
|------|------|------|
| `supports_tools` | bool | 是否支持工具调用（默认 `True`） |
| `supports_tool_return_schema` | bool | 是否原生支持工具返回结构化 schema |
| `supported_native_tools` | frozenset | 支持哪些 provider 原生工具（如 OpenAI 的 web_search、Anthropic 的 computer_use） |
| `supports_json_schema_output` | bool | 是否支持 NativeOutput（按 schema 严格输出） |
| `supports_json_object_output` | bool | 是否支持 OpenAI 风格的"任意合法 JSON 模式" |
| `default_structured_output_mode` | `'tool' \| 'native' \| 'prompted'` | 默认结构化输出模式 |
| `json_schema_transformer` | type or None | 改造 JSON Schema 以兼容模型限制 |
| `prompted_output_template` | str | PromptedOutput 用的模板，含 `{schema}` 占位 |
| `supports_thinking` | bool | 是否支持思维链 |
| `thinking_always_enabled` | bool | 思维链是否强制开（如 o1） |
| `thinking_tags` | tuple | 思维链标签（默认 `('<think>', '</think>')`） |
| `supports_image_output` | bool | 是否能输出图片 |
| `ignore_streamed_leading_whitespace` | bool | 流式时是否丢弃前导空白（部分模型有 bug） |

理解了这些字段你就懂了 **Agent 内部那些"自动决策"是从哪儿来的**。

---

## 3. 三种结构化输出模式

`default_structured_output_mode` 是日常最容易踩的字段：

| 模式 | 原理 | 适用模型 |
|------|------|----------|
| `tool` | 把 `output_type` 转成一个 tool，模型"调用"它返回结构化数据 | 任何支持 tools 的模型（默认值） |
| `native` | 走模型的 JSON Schema 严格模式 | OpenAI gpt-4o、Anthropic 3.5+ 等 |
| `prompted` | 把 schema 拼进 system prompt，让模型按格式返回 | 任何模型，包括没工具的本地小模型 |

如果你没显式指定，Agent 会**从 Profile 拿默认值**，再根据 `output_type` 的字段类型自动校验兼容性。

---

## 4. 查看一个模型的 Profile

```python
from pydantic_ai.models.openai import OpenAIChatModel

model = OpenAIChatModel('gpt-4o-mini')
print(model.profile.supports_tools)                  # True
print(model.profile.supports_json_schema_output)     # True
print(model.profile.default_structured_output_mode)  # 'tool'
print(model.profile.supported_native_tools)          # frozenset(...)
```

对比 Anthropic：

```python
from pydantic_ai.models.anthropic import AnthropicModel

m = AnthropicModel('claude-sonnet-4-5')
print(m.profile.supports_tools)                  # True
print(m.profile.supports_json_schema_output)     # 取决于版本
print(m.profile.default_structured_output_mode)  # 'tool'
```

---

## 5. 启用 / 关闭能力

如果你确定某个模型支持但 profile 没标，或者反之要强制走兜底，可以**覆盖 profile**：

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile

# 强制把这个模型的 default_structured_output_mode 改成 prompted
model = OpenAIChatModel(
    'gpt-3.5-turbo',
    profile=ModelProfile(
        supports_tools=True,
        supports_json_schema_output=False,  # 关掉 native，让 Agent 走 prompted
        default_structured_output_mode='prompted',
    ),
)
```

更常见的写法是**只改部分字段**，用 `update`：

```python
from pydantic_ai.profiles.openai import openai_model_profile

base_profile = openai_model_profile('gpt-4o-mini')
custom_profile = base_profile.update(
    ModelProfile(default_structured_output_mode='native')
)
model = OpenAIChatModel('gpt-4o-mini', profile=custom_profile)
```

---

## 6. 显式选择输出模式（用 Output 类型而不是改 profile）

更推荐的做法：**别动 profile，直接用 `NativeOutput` / `PromptedOutput` / `ToolOutput` 包一层**：

```python
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput


class Issue(BaseModel):
    title: str
    severity: str


# A: 强制走 native JSON schema
agent_native = Agent(
    'openai:gpt-4o-mini',
    output_type=NativeOutput(Issue),
)

# B: 强制走 prompted（任何模型都能用）
agent_prompted = Agent(
    'openai:gpt-3.5-turbo',
    output_type=PromptedOutput(Issue),
)

# C: 显式走 tool
agent_tool = Agent(
    'openai:gpt-4o-mini',
    output_type=ToolOutput(Issue),
)
```

**口诀**：

- 高质量模型 + 严格 schema → `NativeOutput`
- 本地小模型 / 老模型 → `PromptedOutput`
- 想让结构化输出"长得像工具调用" → `ToolOutput`（默认）

如果你用 `output_type=Issue`（裸 Pydantic 类）而模型不支持 native，Pydantic AI 会**自动从 Profile 选最合适的模式**。

---

## 7. JSON Schema 转换器

Gemini 的 JSON Schema 不支持 `additionalProperties`、`$defs` 等字段，直接传上去会 400 报错。`json_schema_transformer` 就是来解决这事的：

```python
from pydantic_ai.profiles.google import google_model_profile

profile = google_model_profile('gemini-2.0-flash')
print(profile.json_schema_transformer)
# <class 'pydantic_ai.profiles._json_schema.GoogleJsonSchemaTransformer'>
```

你不会经常自己写 transformer，但万一你接了一个"魔改 OpenAI"或者本地 vLLM 模型，可以继承 `JsonSchemaTransformer` 写自己的。

---

## 8. 自定义 Provider 时声明 Capabilities

如果你接了一个 OpenAI-compatible 的国产模型（比如 DeepSeek、月之暗面、智谱），怎么告诉 Pydantic AI 它的能力？

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.profiles import ModelProfile

# DeepSeek 支持 tools 和 JSON 模式，但不支持 JSON Schema 严格输出
deepseek_profile = ModelProfile(
    supports_tools=True,
    supports_json_object_output=True,
    supports_json_schema_output=False,
    default_structured_output_mode='tool',
)

model = OpenAIChatModel(
    'deepseek-chat',
    provider=OpenAIProvider(
        base_url='https://api.deepseek.com/v1',
        api_key='sk-...',
    ),
    profile=deepseek_profile,
)
```

**关键**：profile 不影响"能不能调通"，它影响"Agent 默认会用什么模式"。声明错了不会立刻报错，但你会得到一些奇怪的结果（比如模型一直返回纯 JSON 字符串而 Pydantic AI 当成普通文本）。

---

## 9. 思维链相关能力

```python
profile = model.profile
profile.supports_thinking          # 是否支持
profile.thinking_always_enabled    # 是否强制开（o1/o3 是 True）
profile.thinking_tags              # 思维链标签，例如 ('<think>', '</think>')
```

`thinking_tags` 的用途：**DeepSeek-R1 这种模型把思维过程裹在 `<think>...</think>` 里返回**，Pydantic AI 用这两个 tag 把思维部分拆出来变成 `ThinkingPart`，而不污染最终回答。

如果你接了一个用别的标签的模型（如 `<reasoning>...</reasoning>`），可以这么改：

```python
from pydantic_ai.profiles import ModelProfile

custom_profile = ModelProfile(
    supports_thinking=True,
    thinking_tags=('<reasoning>', '</reasoning>'),
)
```

---

## 10. 实战：capability-aware 的输出兜底

写一个工厂函数，根据传入模型的能力自动选 Output 类型：

```python
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput
from pydantic_ai.models import Model


class Issue(BaseModel):
    title: str
    severity: str


def make_agent(model: Model | str, schema=Issue) -> Agent:
    """根据模型 profile 自动挑最合适的结构化输出模式。"""
    # 字符串模型先实例化拿 profile
    if isinstance(model, str):
        from pydantic_ai.models import infer_model
        model_obj = infer_model(model)
    else:
        model_obj = model

    profile = model_obj.profile

    if profile.supports_json_schema_output:
        output = NativeOutput(schema)
        mode = 'native'
    elif profile.supports_tools:
        output = ToolOutput(schema)
        mode = 'tool'
    else:
        output = PromptedOutput(schema)
        mode = 'prompted'

    print(f'[make_agent] {model_obj.model_name} → {mode}')
    return Agent(model_obj, output_type=output)


agent = make_agent('openai:gpt-4o-mini')
result = agent.run_sync('登录页 500 报错')
print(result.output)
```

这就是 capability-aware：**你写一遍代码，对接不同模型时自动走最优路径**。

---

## 11. 与 LangChain 对比

LangChain 没有 `ModelProfile` 这种集中描述，能力散落在：

- `ChatModel.with_structured_output(schema, method=...)` 里要手动指定 `function_calling | json_mode | json_schema`
- `model.bind_tools([...])` 之前你得自己知道模型支不支持
- JSON Schema 转换由各家 partner 包内部处理，外部看不到

Pydantic AI 的做法是**把能力描述显式化**，好处是：

- 一处声明，全局生效
- 自动 fallback，少写 if/else
- 评测 / 调试时能清楚知道"为什么这个模型走了这条路径"

代价是：**第一次接陌生模型时要花 5 分钟看 profile**。

---

## 12. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 你以为模型支持 native JSON schema，结果框架走了 tool 模式 | profile 默认 `default_structured_output_mode='tool'` | 显式用 `NativeOutput(schema)` |
| Gemini 报 `Invalid JSON schema: additionalProperties` | 你绕过了 profile 的 transformer | 别手动改 `json_schema_transformer` |
| Agent 启动报"output_type not supported by model" | profile 标了 `supports_tools=False` | 改 profile 或 model |
| o1 模型不给我思维链 | `thinking_always_enabled=True` 但你没设 `output_settings.include_thinking` | 用 `Agent(..., model_settings=ModelSettings(extra_body=...))` 或对应模型 settings |
| 自定义 provider 跑结构化输出全返回字符串 | profile 没设 `supports_json_schema_output` | 显式声明 profile |
| 升级了模型版本，能力跑不通 | profile 是按"版本名前缀"匹配的 | 重新指定 profile 或升级 pydantic-ai |
| `PromptedOutput` 失败率高 | 小模型不擅长按 schema 输出 | 给 `prompted_output_template` 加 few-shot 或换 ToolOutput |
| Logfire 看不出走了哪种 output 模式 | 默认 trace 不细 | `Agent(..., instrument=True)` 看 span attributes |

---

## 13. 选型决策树

```
你的目标 = "让 Agent 结构化输出"
│
├─ 模型是 OpenAI gpt-4o 系列 / Claude 3.5+ / Gemini 2.0+？
│  ├─ 字段都是基础类型 → 直接 output_type=Pydantic 类（走 tool）
│  └─ 要最高保真 → output_type=NativeOutput(Pydantic 类)
│
├─ 模型是老 / 国产 / 本地小模型？
│  ├─ 支持 tools → output_type=ToolOutput(Pydantic 类)
│  └─ 不支持 tools → output_type=PromptedOutput(Pydantic 类)
│
└─ 自定义 provider（如 OpenAI-compatible 接的国产）？
   └─ 先声明 ModelProfile，再选 Output
```

---

## 14. 本章 demo

完整可运行代码：[`demos/advanced/06_capabilities.py`](../../demos/advanced/06_capabilities.py)

demo 涵盖：
- 打印不同模型的 profile
- `NativeOutput` / `PromptedOutput` / `ToolOutput` 三种模式对比
- 自定义 ModelProfile（模拟接国产模型）
- capability-aware 的 Agent 工厂

下一篇：[`07-retries-http.md`](07-retries-http.md) —— HTTP 重试与 ModelRetry 全套机制。
