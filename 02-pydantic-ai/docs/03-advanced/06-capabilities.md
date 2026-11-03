# Pydantic AI 进阶 06：Capabilities（能力）与 ModelProfile

> capability 是 Pydantic AI V2 最主要的扩展方式：工具、指令、hooks、模型设置、模型选择都可以打包成一个单元，用 `Agent(capabilities=[...])` 挂上去，能复用、能组合，也能按需加载。`ModelProfile` 则描述一个模型能做什么，框架靠它决定默认走哪条路径。这篇上半讲 capability，下半讲 ModelProfile。

以 `pydantic-ai 2.64.0` 为准。V1 分散在 `builtin_tools=` / `history_processors=` / `instrument=` 等参数里的功能，V2 基本都变成了 capability。

---

# 上篇：Capability

## 1. 一个 capability 能装什么

```text
工具          —— 通过 toolset，或者原生工具
生命周期 hooks —— 拦截并修改模型请求、工具调用、整次运行
instructions  —— 静态或动态的指令
模型设置       —— 静态或按步骤变化的设置
模型           —— 静态或自适应地选模型，或按应用规则解析模型 ID
```

LangChain 的 middleware 以钩子为主，要加工具得另外改 `tools`，要加指令得在 `wrap_model_call` 里改 `system_prompt`。capability 把这些放进同一个单元，一起出现、一起生效，所以一种能力可以整体复用、分享，甚至从配置文件加载。

## 2. 内置的 capability

capability 来自两个包。核心包 `pydantic-ai` 里放的是需要模型或框架深度配合的（原生工具、厂商 API、循环内部的集成）；官方 harness 包 `pydantic-ai-harness` 放其余的，有 80 多个（见[clai 命令行与 Harness 工具集](../04-modules/05-cli-harness.md)）。按用途大致分成这些：

| 类别 | 例子 | 来自 |
|------|------|------|
| 完整 harness | `Coder`、`Researcher` | harness |
| 执行环境 | `LocalWorkspace`、`FileSystem`、`Shell`、各家沙箱（Modal / E2B / Docker …） | 核心 / harness |
| 工具与原生能力 | `MCP`、`NativeTool`、`ImageGeneration`、`FileUnderstanding` | 核心 |
| 网络与研究 | `WebSearch`、`WebFetch`、浏览器自动化 | 核心 / harness |
| 推理、规划、委派 | `Thinking`、`Planning`、`SubAgents`、`DynamicWorkflow`、`Advisor` | 核心 / harness |
| 上下文管理 | `CodeMode`、`ToolSearch`、`Caching`、压缩、工具输出截断 | 核心 / harness |
| 知识与记忆 | `Memory`、`Skills`、`RepoContext`、对话检索 | harness |
| 控制与安全 | `Guardrails`、提示词注入防御、花费上限、`AskUser`、轨迹评审 | harness |
| 运行时 | `Instrumentation`、`StepPersistence`、持久执行 | 核心 / harness |
| 循环定制 | `ProcessHistory`、`PrepareTools`、`PrefixTools`、`SelectModel`、`ModelFallback`、`Hooks` | 核心 |

前面几篇已经用过不少：`Thinking`（[Thinking（显式思考链）](03-thinking.md)）、`NativeTool` / `WebSearch`（[Native Tools 模型内置工具](../02-tools/05-native-tools.md)）、`ProcessHistory`（[消息与对话历史（Messages & History）](../01-basics/07-messages-history.md)）、`Hooks`（[Hooks（生命周期钩子）](04-hooks.md)）、`ModelFallback`（[模型与 Provider 全览](../01-basics/04-models-providers.md)）、`MCP`、`HandleDeferredToolCalls`（[Deferred Tools 与人审批](08-deferred-tools.md)）。

## 3. 写法一：`Capability` 便捷类

不用继承，像注册工具一样声明：

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import Capability

refunds = Capability(
    id="refunds",
    description="Use for refund eligibility and refund status.",
    instructions="Always confirm the order ID before issuing a refund.",
)

@refunds.tool_plain
def refund_status(order_id: str) -> str:
    """Look up the refund status for an order."""
    return f"Order {order_id}: refund issued on 2026-05-01."

agent = Agent("openai:gpt-6.1-sol", capabilities=[refunds])
```

`Capability` 能装 instructions、函数工具和 toolset。需要依赖类型时写成 `Capability[SupportDeps](...)`，再用 `@cap.instructions` / `@cap.tool` 往里加。官方文档的说法是，可以把它当作在定义一个 skill。

## 4. 写法二：继承 `AbstractCapability`

需要 hooks、模型设置、原生工具，或者每次运行要维护自己的状态时，继承 `AbstractCapability`：

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AgentToolset, FunctionToolset

math_toolset = FunctionToolset()

@math_toolset.tool_plain
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b

@dataclass
class MathTools(AbstractCapability[Any]):
    """Provides basic math operations."""
    def get_toolset(self) -> AgentToolset[Any] | None:
        return math_toolset

@dataclass
class KnowsCurrentTime(AbstractCapability[Any]):
    """Tells the agent what time it is."""
    def get_instructions(self):
        def _get_time(ctx: RunContext[Any]) -> str:
            return f"The current date and time is {datetime.now().isoformat()}."
        return _get_time

agent = Agent("openai:gpt-6.1-sol", capabilities=[MathTools(), KnowsCurrentTime()])
```

配置类的方法有：`get_toolset()`、`get_native_tools()`、`get_wrapper_toolset()`、`get_instructions()`、`get_model_settings()`、`get_workspace()`、`get_model()`、`resolve_model_id()`。hooks 就是[Hooks（生命周期钩子）](04-hooks.md)那张表里的方法名，比如 `before_model_request`、`after_tool_execute`、`wrap_run`（签名见[Hooks（生命周期钩子）](04-hooks.md)第 3 节）。

每次运行的状态要隔离。capability 实例在构造 agent 时绑定一次，之后所有运行共用这一个实例。要按运行计数之类，就重写 `for_run`，返回一个新实例，不要改 `self`：

```python
@dataclass
class RequestCounter(AbstractCapability[Any]):
    count: int = 0

    async def for_run(self, ctx: RunContext[Any]) -> "RequestCounter":
        return RequestCounter()          # 每次运行一个新实例

    async def before_model_request(self, ctx, request_context):
        self.count += 1
        return request_context
```

capability 也可以动态构建：往 `capabilities=` 里传一个接收 `RunContext`、返回 capability（或 `None`）的函数，每次运行前会调用它。

## 5. 组合：洋葱，以及 id

多个 capability 会组合成一个 `CombinedCapability`，规则和 middleware 一样。配置会合并：instructions 拼接，模型设置后者覆盖前者，toolset 合并。钩子方面，`before_*` 按列表顺序执行，`after_*` 逆序，`wrap_*` 第一个在最外层。需要固定位置时，重写 `get_ordering()` 返回 `CapabilityOrdering(position="outermost")`。

id 决定同类 capability 怎么共处，这一层规则 LangChain 没有：

- 同一个 agent 上出现两个同 id 的，视为同一份配置写了两遍，逐字段合并；
- 运行时传入的同 id capability 会整体替换 agent 级的那个，比如 `agent.run(capabilities=[Thinking(effort="high")])`；
- 确实需要两个独立实例（比如两个账号的凭证），就给它们不同的 id。

## 6. 按需加载（on-demand）

一个承担多种工作流的 agent，默认每一轮都要把所有工作流的 instructions 和工具 schema 发给模型。这既费 token，工具一多（官方给的数字是 30~50 个）模型还会开始选错。给 capability 设上 `defer_loading=True` 和一个固定的 `id`，它就折叠成目录里的一行：

```python
refunds = Capability(
    id="refunds",
    description="Use for refund eligibility, refund status, or processing a refund.",
    instructions="Always confirm the order ID before issuing a refund.",
    defer_loading=True,
)
```

第一轮模型看到的是：基础 instructions、框架提供的一个 `load_capability` 工具，以及一段目录（`- refunds: Use for refund eligibility...`）。退款相关的 instructions 和工具这时都看不到。模型需要时调用 `load_capability(id="refunds")`，instructions 作为工具结果返回，工具从下一次请求开始出现。加载是整包加载，工具、模型设置、hooks 一起生效，并且在整个运行中保持有效。几个细节：

- 目录有变化时是追加的，不会改写已缓存的提示词前缀；
- 按需 capability 的 instructions 以工具结果的形式进入消息历史，会出现在 UI 适配器发给前端的消息里，不想让前端看到的 instructions 就别设成按需；
- 加载原生工具会打破提示词缓存前缀，只含 instructions 和函数工具的按需 capability 对缓存最友好；
- 可以强制模型先加载再操作：在危险工具的 hook 里检查 `ctx.loaded_capability_ids`，没加载就抛 `ModelRetry`；
- `Hooks(defer_loading=True, id=..., description=...)` 也能按需加载；
- 离线测试时，`TestModel` 会用占位 id 调 `load_capability`，必然失败，测按需加载要用 `FunctionModel` 写死调用脚本。

Skills 就是建立在这套机制上的（见[clai 命令行与 Harness 工具集](../04-modules/05-cli-harness.md)）。

## 7. Agent Spec：用 YAML 声明 agent

几乎所有能力都是 capability，所以 agent 可以完全用配置文件声明：

```yaml
# agent.yaml
model: anthropic:claude-opus-5-5
instructions: You are a helpful research assistant.
model_settings:
  max_tokens: 8192
capabilities:
  - WebSearch:
      local: duckduckgo
  - Thinking:
      effort: high
```

```python
agent = Agent.from_file("agent.yaml")
```

capability 在 spec 里有三种写法：`"Thinking"`、`{"Thinking": "high"}`、`{"Thinking": {"effort": "high"}}`。instructions 支持 `{{name}}` 模板；spec 里还能写 `output_schema`、重试预算、`end_strategy` 等。自定义的 capability 实现了 `from_spec` 之后，也能写进 spec。

## 8. 与 LangChain middleware 对照

| 维度 | LangChain middleware | Pydantic AI capability |
|------|---------------------|------------------------|
| 能装什么 | 主要是钩子（加工具 / 指令要改别处） | 工具 + hooks + 指令 + 设置 + 模型，一个单元 |
| 顺序 | 洋葱 | 洋葱（`get_ordering` 可固定位置） |
| 同类重复 | 同名 middleware 报错 | 同 id 逐字段合并 |
| 按需出现 | 需自己写 | `defer_loading=True` + `load_capability` |
| 配置化 | 无 | Agent Spec（YAML） |

---

# 下篇：ModelProfile

## 9. 为什么需要能力描述

各家模型的能力差别很大：有的支持 JSON Schema 严格输出，有的不支持；Gemini 的 JSON Schema 方言要先转换；有些推理模型始终开着思考；本地模型可能根本不支持工具。Pydantic AI 要做到与模型无关，就需要一份统一的模型能力描述，也就是 `ModelProfile`。它解决三类问题：兼容性检查（比如你要 `NativeOutput` 而模型不支持，框架会直接告诉你）、自动选择默认路径、JSON Schema 转换。

---

## 10. ModelProfile 的核心属性

`pydantic_ai.profiles.ModelProfile` 在 V2 里是一个 `TypedDict`，所有键都可选。所以读取要用 `model.profile.get("supports_tools")`，构造用 `ModelProfile(supports_tools=True, ...)`（得到的其实就是个 dict），不再是 `profile.supports_tools` 这种属性访问。`model.profile` 一般已经和全局默认值、provider 默认值合并过，是完整的 profile。字段很多（包括缓存、`context_window`、工具延迟模式等），最关键的是这些：

| 字段 | 类型 | 含义 |
|------|------|------|
| `supports_tools` | bool | 是否支持工具调用（默认 `True`） |
| `supports_tool_return_schema` | bool | 是否原生支持工具返回结构化 schema |
| `supported_native_tools` | frozenset | 支持哪些 provider 原生工具（如 OpenAI 的 web_search、Anthropic 的 computer_use） |
| `supports_json_schema_output` | bool | 是否支持 NativeOutput（按 schema 严格输出） |
| `supports_json_object_output` | bool | 是否支持 OpenAI 风格的「任意合法 JSON」模式 |
| `default_structured_output_mode` | `'tool' \| 'native' \| 'prompted'` | 默认结构化输出模式 |
| `json_schema_transformer` | type or None | 改造 JSON Schema 以兼容模型限制 |
| `prompted_output_template` | str | PromptedOutput 用的模板，含 `{schema}` 占位 |
| `supports_thinking` | bool | 是否支持思维链 |
| `thinking_always_enabled` | bool | 思维链是否强制开（如部分 OpenAI 推理模型、Claude Opus 5.5、DeepSeek-R1） |
| `thinking_tags` | tuple | 思维链标签（默认 `('<think>', '</think>')`） |
| `supports_image_output` | bool | 是否能输出图片 |
| `supports_image_input` / `supports_document_input` / `supports_audio_input` / `supports_video_input` | bool | 能读哪类文件（默认 True，不保证一定能读） |
| `context_window` | int \| None | 上下文窗口大小，未知为 None |
| `ignore_streamed_leading_whitespace` | bool | 流式时是否丢弃前导空白（部分模型有 bug） |

Agent 内部很多自动决定，依据就是这些字段。

---

## 11. 三种结构化输出模式

日常最容易出问题的字段是 `default_structured_output_mode`：

| 模式 | 原理 | 适用模型 |
|------|------|----------|
| `tool` | 把 `output_type` 转成一个 tool，模型通过调用它返回结构化数据 | 任何支持 tools 的模型（默认值） |
| `native` | 走模型的 JSON Schema 严格模式 | OpenAI / Anthropic / Gemini 的较新型号 |
| `prompted` | 把 schema 拼进 system prompt，让模型按格式返回 | 任何模型，包括不支持工具的本地小模型 |

不显式指定时，Agent 会从 Profile 拿默认值，再根据 `output_type` 的字段类型检查兼容性。

---

## 12. 查看一个模型的 Profile

```python
from pydantic_ai.models.openai import OpenAIChatModel

model = OpenAIChatModel('qwen-flash')
print(model.profile.get('supports_tools'))                  # True
print(model.profile.get('supports_json_schema_output'))     # True
print(model.profile.get('default_structured_output_mode'))  # 'tool'
print(model.profile.get('supported_native_tools'))          # frozenset(...)
```

对比 Anthropic：

```python
from pydantic_ai.models.anthropic import AnthropicModel

m = AnthropicModel('claude-sonnet-5-5')
print(m.profile.get('supports_tools'))                  # True
print(m.profile.get('supports_json_schema_output'))     # 取决于版本
print(m.profile.get('default_structured_output_mode'))  # 'tool'
```

---

## 13. 启用 / 关闭能力

确定某个模型支持但 profile 没标出来，或者反过来想强制走兜底路径时，可以覆盖 profile：

```python
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile

# 强制把这个模型的 default_structured_output_mode 改成 prompted
model = OpenAIChatModel(
    'some-small-model',
    profile=ModelProfile(
        supports_tools=True,
        supports_json_schema_output=False,  # 关掉 native，让 Agent 走 prompted
        default_structured_output_mode='prompted',
    ),
)
```

更常见的是只改部分字段：

```python
from pydantic_ai.profiles.openai import openai_model_profile

from pydantic_ai.profiles import ModelProfile

# ModelProfile 是 dict：覆盖部分字段就是普通的字典合并
base_profile = openai_model_profile('gpt-6.1-sol')
custom_profile = {**base_profile, 'default_structured_output_mode': 'native'}
model = OpenAIChatModel('gpt-6.1-sol', profile=custom_profile)
```

---

## 14. 显式选择输出模式（用 Output 类型，不改 profile）

我更推荐这种做法：不动 profile，直接用 `NativeOutput` / `PromptedOutput` / `ToolOutput` 包一层：

```python
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput


class Issue(BaseModel):
    title: str
    severity: str


# A: 强制走 native JSON schema
agent_native = Agent(
    'openai-chat:qwen-flash',
    output_type=NativeOutput(Issue),
)

# B: 强制走 prompted（任何模型都能用）
agent_prompted = Agent(
    'openai-chat:some-small-model',
    output_type=PromptedOutput(Issue),
)

# C: 显式走 tool
agent_tool = Agent(
    'openai-chat:qwen-flash',
    output_type=ToolOutput(Issue),
)
```

怎么选：

- 质量好的模型、需要严格 schema，用 `NativeOutput`
- 本地小模型或老模型，用 `PromptedOutput`
- 想让结构化输出以工具调用的形式出现，用 `ToolOutput`（默认就是这个）

直接写 `output_type=Issue`（裸 Pydantic 类）时，用的是 profile 里的 `default_structured_output_mode`。内置 profile 几乎都是 `'tool'`，所以不会因为模型支持 native 就自动切过去，要 native 得显式写 `NativeOutput`。

---

## 15. JSON Schema 转换器

Gemini 的 JSON Schema 不支持 `additionalProperties`、`$defs` 等字段，原样传上去会报 400。`json_schema_transformer` 就是处理这个的：

```python
from pydantic_ai.profiles.google import google_model_profile

profile = google_model_profile('gemini-3.8-flash')
print(profile.get('json_schema_transformer'))
# <class 'pydantic_ai.profiles.google.GoogleJsonSchemaTransformer'>
```

一般用不着自己写 transformer。但如果接的是改过协议的 OpenAI 兼容服务，或者本地的 vLLM 模型，可以继承 `JsonSchemaTransformer` 写一个。

---

## 16. 自定义 Provider 时声明能力

接一个 OpenAI 兼容的国产模型（比如 DeepSeek、月之暗面、智谱）时，怎么告诉 Pydantic AI 它能做什么？

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

profile 不影响能不能调通，影响的是 Agent 默认用什么模式。声明错了不会马上报错，但结果会很奇怪，比如模型一直返回纯 JSON 字符串，而 Pydantic AI 把它当成普通文本。

---

## 17. 思维链相关字段

```python
profile = model.profile
profile.get('supports_thinking')          # 是否支持
profile.get('thinking_always_enabled')    # 是否强制开（Claude Opus 5.5、DeepSeek-R1 等是 True）
profile.get('thinking_tags')              # 思维链标签，例如 ('<think>', '</think>')
```

`thinking_tags` 是给 DeepSeek-R1 这类模型用的：它们把思考过程包在 `<think>...</think>` 里返回，Pydantic AI 用这两个标签把思考部分拆出来变成 `ThinkingPart`，不混进最终回答。

如果接的模型用别的标签（比如 `<reasoning>...</reasoning>`），这样改：

```python
from pydantic_ai.profiles import ModelProfile

custom_profile = ModelProfile(
    supports_thinking=True,
    thinking_tags=('<reasoning>', '</reasoning>'),
)
```

---

## 18. 实战：按模型能力自动选输出模式

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

    if profile.get('supports_json_schema_output'):
        output = NativeOutput(schema)
        mode = 'native'
    elif profile.get('supports_tools'):
        output = ToolOutput(schema)
        mode = 'tool'
    else:
        output = PromptedOutput(schema)
        mode = 'prompted'

    print(f'[make_agent] {model_obj.model_name} → {mode}')
    return Agent(model_obj, output_type=output)


agent = make_agent('openai-chat:qwen-flash')
result = agent.run_sync('登录页 500 报错')
print(result.output)
```

代码只写一遍，接不同的模型时自动走合适的路径。

---

## 19. 与 LangChain 对比

LangChain 没有 `ModelProfile` 这样集中的描述，相关信息分散在几处：

- `ChatModel.with_structured_output(schema, method=...)` 要手动指定 `function_calling | json_mode | json_schema`
- 调 `model.bind_tools([...])` 之前，你得自己知道模型支不支持工具
- JSON Schema 的转换由各家 partner 包在内部处理，外面看不到

Pydantic AI 把能力描述显式化了：声明一处，全局生效；能自动回退，少写 if/else；评测和调试时能清楚看到某个模型为什么走了某条路径。代价是第一次接一个陌生模型时，要花几分钟看看它的 profile。

---

## 20. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 以为模型支持 native JSON schema，框架却走了 tool 模式 | profile 默认 `default_structured_output_mode='tool'` | 显式用 `NativeOutput(schema)` |
| Gemini 报 `Invalid JSON schema: additionalProperties` | 绕过了 profile 的 transformer | 别手动改 `json_schema_transformer` |
| Agent 启动报 "output_type not supported by model" | profile 标了 `supports_tools=False` | 改 profile 或换模型 |
| 推理模型不返回思维链 | 没开思考，或厂商只给摘要 | 用 `Thinking(effort=...)` capability，OpenAI 加 `openai_reasoning_summary`；千问用 `extra_body={"enable_thinking": True}`（见[Thinking（显式思考链）](03-thinking.md)） |
| 自定义 provider 的结构化输出全返回字符串 | profile 没设 `supports_json_schema_output` | 显式声明 profile |
| 升级了模型版本后能力不对 | profile 按版本名前缀匹配 | 重新指定 profile，或升级 pydantic-ai |
| `PromptedOutput` 失败率高 | 小模型不擅长按 schema 输出 | 给 `prompted_output_template` 加 few-shot，或换 ToolOutput |
| Logfire 看不出走了哪种输出模式 | 默认 trace 不够细 | 开 `logfire.instrument_pydantic_ai()`，看 span attributes |
| `AttributeError: 'dict' object has no attribute 'supports_tools'` | V2 里 `ModelProfile` 是 TypedDict（dict） | `profile.get("supports_tools")` |
| 自己写的 capability 状态串到别的运行里 | 实例被所有运行共用 | 重写 `for_run` 返回新实例，别改 `self` |
| `TestModel` 下按需 capability 加载失败 | 它用占位 id 调 `load_capability` | 用 `FunctionModel` 写死调用脚本 |

---

## 21. 选型决策树

```
你的目标 = "让 Agent 结构化输出"
│
├─ 模型是 OpenAI / Anthropic / Gemini 的较新型号？
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

## 22. 本章 demo

完整可运行代码：[`demos/advanced/06_capabilities.py`](../../demos/advanced/06_capabilities.py)

demo 主要演示本篇后半部分的 ModelProfile；capability 的代码见本篇前半部分，以及[Hooks（生命周期钩子）](04-hooks.md)的 demo 里的 `Hooks`：
- 打印不同模型的 profile
- 对比 `NativeOutput` / `PromptedOutput` / `ToolOutput` 三种模式
- 自定义 ModelProfile（模拟接入国产模型）
- 按模型能力自动选输出模式的 Agent 工厂

下一篇：[Pydantic AI 进阶 07：HTTP 重试与 ModelRetry](07-retries-http.md)，讲 HTTP 重试与 ModelRetry 的整套机制。
