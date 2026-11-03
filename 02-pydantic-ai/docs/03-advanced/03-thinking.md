# Pydantic AI 进阶 03：Thinking（显式思考链）

> Claude、OpenAI 的推理模型、Gemini、千问都支持先在内部思考一阵再回答，但各家暴露思考内容的方式不一样：有的用 `<think>` 标签，有的放在 `reasoning_content` 字段。Pydantic AI 把它们统一成 `ThinkingPart`，再用一个 `Thinking` capability / `thinking` 设置来开关。这篇讲怎么开、怎么取、怎么控制成本。

以 `pydantic-ai 2.64.0` 为准。千问等兼容端点开思考要传 `extra_body={'enable_thinking': True}`，统一的 `thinking` 设置对它们不生效（见 3.4 节）。

---

## 1. 什么是 Thinking

普通对话模型直接把答案一口气写完。有些模型（Claude 的 extended / adaptive thinking、OpenAI 的推理模型、Gemini 2.5 起的思考模型、千问混合推理模型等）支持先思考、再回答的两段式输出（下面是示意）：

```
模型输出:
  <thinking>
    用户问的是 N 皇后问题…
    先考虑回溯解法…
    复杂度 O(n!)…
  </thinking>
  <answer>
    解法是：…
  </answer>
```

`<thinking>` 那段是模型的草稿，用户默认看不到，也不改变主回答的含义。但开发者常常想看它：

- 调试：模型为什么答错了，看草稿就知道
- 透明：给用户展示模型是怎么想的（比如 Claude 的折叠思考块）
- 留档：把推理过程存下来供审计

各 provider 暴露思考的格式完全不同：

| Provider | 暴露方式 |
|----------|---------|
| Anthropic Claude | `content: [{type: "thinking", thinking: "...", signature: "..."}, ...]` |
| OpenAI Responses | `response.output[].summary[].text` |
| DeepSeek | `reasoning_content` 字段 |
| Gemini | 响应 parts 里带 `thought: true` 标记的部件（思考摘要） |

Pydantic AI 把它们全部转成 `ThinkingPart`，放进 ModelResponse 的 parts 列表。

---

## 2. ThinkingPart 是什么

```python
from pydantic_ai.messages import ThinkingPart

part = ThinkingPart(
    content="...",          # 思考文本
    signature="abc123...",  # provider 给的签名（Anthropic 用于回传以维持思考链）
)
part.part_kind  # 'thinking'
```

它和 `TextPart` / `ToolCallPart` 并列，是 `ModelResponse.parts` 里的一种元素：

```python
result = agent.run_sync("证明素数无穷多")
for part in result.all_messages()[-1].parts:
    print(part.part_kind, "->", part)
# 输出：
# thinking -> ThinkingPart(content='用反证法...')
# text -> TextPart(content='假设素数有限...')
```

---

## 3. 启用 Thinking

### 3.1 统一开关（推荐）

Pydantic AI 提供了一个所有 provider 通用的开关：

```python
from pydantic_ai import Agent

# 通过 model_settings
agent = Agent(
    "anthropic:claude-opus-5-5",
    model_settings={"thinking": "high"},
)

# 或通过 Capability
from pydantic_ai.capabilities import Thinking

agent = Agent(
    "anthropic:claude-opus-5-5",
    capabilities=[Thinking(effort="high")],
)
```

`thinking` / `effort` 可以取这些值：

| 值 | 含义 |
|----|------|
| `True` | 默认强度 |
| `False` | 关闭 |
| `'minimal'` | 最低强度（OpenAI） |
| `'low'` / `'medium'` / `'high'` | 三档强度 |
| `'xhigh'` / `'max'` | 极高（部分 Claude / OpenAI 型号；不支持的档位自动映射到最接近的） |

Pydantic AI 会把它翻译成各家的具体参数：Anthropic 新型号用 adaptive thinking + effort，老型号用 `budget_tokens`（`'low'`→2048、`'high'`→16384、`'xhigh'`/`'max'`→32768）；OpenAI 用 `reasoning_effort`；Google 的 Gemini 3 用 `thinking_level`，Gemini 2.5 用 `thinking_budget`；Groq、Mistral、OpenRouter 等也各有映射。`False` 在没法关闭思考的模型上表示尽量少想；模型根本没有思考设置时保持默认。

### 3.2 Anthropic 原生设置（更细的控制）

```python
from pydantic_ai.models.anthropic import AnthropicModelSettings

# Extended Thinking（老风格，给定预算）
settings = AnthropicModelSettings(
    anthropic_thinking={"type": "enabled", "budget_tokens": 4096},
)

# Adaptive Thinking（Claude Opus 4.6+ 风格，自适应）
settings = AnthropicModelSettings(
    anthropic_thinking={"type": "adaptive"},
    anthropic_effort="high",
)

agent = Agent("anthropic:claude-opus-5-5", model_settings=settings)
```

`budget_tokens` 是上限，模型不一定用满。Adaptive 模式由模型自己决定想多少。

### 3.3 OpenAI 原生设置

```python
from pydantic_ai.models.openai import (
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)

settings = OpenAIResponsesModelSettings(
    openai_reasoning_effort="medium",
    openai_reasoning_summary="detailed",  # 'auto' / 'concise' / 'detailed'
)
model = OpenAIResponsesModel("gpt-6.1-sol")  # 或其它 reasoning 型号
agent = Agent(model, model_settings=settings)
```

OpenAI 的推理模型默认不输出思考全文，设了 `reasoning_summary="detailed"` 才能拿到思考摘要。完整的 chain-of-thought 不会返回给用户，这是 OpenAI 的政策。

---

### 3.4 千问等 OpenAI 兼容端点

```python
# 千问（DashScope 兼容端点）的混合推理模型：用 extra_body 打开思考
agent = Agent(
    "openai-chat:qwen-flash",
    model_settings={"extra_body": {"enable_thinking": True}},
)
result = agent.run_sync("若 a, b, c 都是质数，且 a^2 + b^2 = c^2，求所有解")
# result.all_messages()[-1].parts → [ThinkingPart(...), TextPart(...)]
```

`qwen-flash` 非流式调用也能返回 `ThinkingPart`（`reasoning_content` 被映射成了 `ThinkingPart`）。这类题的思考内容常有几千字，token 成本明显更高。

### 3.5 中途调整强度

强度可以每次运行换一个，也可以在运行内逐步调整（用可调用的 `model_settings`，或 capability 的 `get_model_settings`）：

```python
agent = Agent("openai:gpt-6.1-sol")
r = agent.run_sync("规划数据库迁移。", model_settings={"thinking": "low"})
r = agent.run_sync(
    "现在检查这份计划有没有可能丢数据的地方。",
    message_history=r.all_messages(),
    model_settings={"thinking": "high"},
)
```

请求级的强度是 provider 提示词缓存键的一部分，直接改会让整段对话重新处理。API 支持从对话某个位置开始改强度时，Pydantic AI 会用这种方式发送，并在消息历史里留一个 `ModelSettingsDeltaPart` 记下这次变化。

---

## 4. 取出思考结果

运行结束后，从 `all_messages()` 里找：

```python
from pydantic_ai.messages import ThinkingPart, TextPart

result = agent.run_sync("如果 a^2 + b^2 = c^2 而 a, b, c 都是质数，求所有解")

for msg in result.all_messages():
    for part in getattr(msg, "parts", []):
        if isinstance(part, ThinkingPart):
            print("[思考]", part.content[:200])
        elif isinstance(part, TextPart):
            print("[回答]", part.content)
```

或者封装成一个函数：

```python
def get_thinking(result) -> str:
    for msg in reversed(result.all_messages()):
        for part in getattr(msg, "parts", []):
            if isinstance(part, ThinkingPart):
                return part.content
    return ""
```

---

## 5. 流式中的 Thinking

流式输出时，思考内容先于正文到达，可以分开渲染：

```python
import asyncio
from pydantic_ai import Agent
from pydantic_ai.messages import ThinkingPart, TextPart, PartDeltaEvent, PartStartEvent

agent = Agent("anthropic:claude-opus-5-5", model_settings={"thinking": "high"})

async def main():
    async with agent.iter("证明 √2 是无理数") as run:
        async for node in run:
            # 节点级
            pass

asyncio.run(main())
```

想拿到思考的增量，用 `run_stream_events()`：

```python
async with agent.run_stream_events("证明 √2 是无理数") as events:
    async for event in events:
        # event 可能是 PartStartEvent / PartDeltaEvent / AgentRunResultEvent
        if isinstance(event, PartStartEvent) and isinstance(event.part, ThinkingPart):
            print("\n[开始思考]", end="", flush=True)
        elif isinstance(event, PartDeltaEvent) and hasattr(event.delta, "content_delta"):
            print(event.delta.content_delta or "", end="", flush=True)   # 工具参数的增量没有 content_delta
```

具体格式因 provider 而略有不同，但在事件流里，思考块和正文块始终是分开的。

---

## 6. Provider 支持情况

| Provider / 模型 | 支持 Thinking | 推荐方式 |
|-----------------|---------------|----------|
| Anthropic 较老的 Claude | 支持（Extended） | `anthropic_thinking={"type": "enabled", "budget_tokens": N}` |
| Anthropic 新一代 Claude（Opus / Sonnet 4.6+ / 5 系） | 支持（Adaptive） | `anthropic_thinking={"type": "adaptive"}` + `anthropic_effort` |
| OpenAI 推理模型（GPT-5 起的 GPT 系列，以及旧的 o 系列） | 支持（Reasoning） | 统一 `thinking`，或 `openai_reasoning_effort=...` |
| Google Gemini 2.5 / 3 | 支持 | 统一 `thinking`（自动映射到 `thinking_budget` / `thinking_level`） |
| DeepSeek-R1 / 千问混合推理 | 支持 | DeepSeek 用统一设置；千问用 `extra_body={"enable_thinking": True}`（见 3.4） |
| 其他普通模型 | 不支持 | 设置无效 |

判断一个模型支不支持，看 `model.profile` 里的 `supports_thinking` / `thinking_always_enabled`，比看名字可靠（见[Capabilities（能力）与 ModelProfile](06-capabilities.md)）。

---

## 7. 实战：把复杂数学题的推理过程展开

```python
from pydantic_ai import Agent
from pydantic_ai.messages import ThinkingPart, TextPart

agent = Agent(
    "anthropic:claude-opus-5-5",
    model_settings={"thinking": "high"},
    instructions="你是一位严谨的数学老师，先思考再答题。",
)

result = agent.run_sync(
    "证明：对任意正整数 n，1 + 2 + ... + n = n(n+1)/2"
)

print("===== 模型思考过程 =====")
for msg in result.all_messages():
    for part in getattr(msg, "parts", []):
        if isinstance(part, ThinkingPart):
            print(part.content)

print("\n===== 最终回答 =====")
print(result.output)

print(f"\n[usage] {result.usage}")
```

你会看到模型先考虑数学归纳法，再考虑高斯配对法，然后选一种写出证明。

---

## 8. 普通对话和 Thinking 对话的差异

| 维度 | 普通对话 | Thinking 对话 |
|------|---------|---------------|
| 首字延迟 | 短（毫秒级） | 长（思考阶段可能几秒到几十秒） |
| 总耗时 | 短 | 明显更长 |
| token 成本 | 只计回答 | 加上思考的 token（OpenAI 也会计费） |
| 准确率（推理题） | 一般 | 通常明显更高 |
| 准确率（事实题） | 一般 | 提升不明显 |
| 适合场景 | 闲聊 / 总结 / 翻译 | 数学 / 代码 / 多步推理 / 规划 |

并不是所有对话都该开 Thinking，简单任务开了只会又贵又慢。常见的做法是先用一个路由 Agent 判断难度，再决定开不开。

---

## 9. 与工具调用共存

Thinking 和工具调用可以一起用，有几个细节：

1. 模型在决定调用工具之前会先思考，思考内容里可能就有「我应该调用 weather 工具」这样的话
2. Anthropic 的 signature 字段必须随消息历史传回去，否则下一轮的思考链会断；这一点 Pydantic AI 会自动处理
3. 流式时工具调用可能出现在思考块之后，事件顺序是 `ThinkingPart → ToolCallPart → ToolReturnPart → TextPart`

如果你手工构造历史消息（比如断点续传），**不要删掉 ThinkingPart**，否则 Claude 会拒绝这段历史。

---

## 10. 成本与延迟控制

| 手段 | 说明 |
|------|------|
| 限制 budget | `budget_tokens=2048`，防止模型一直想下去 |
| 降 effort | 简单题用 `'low'`，难题才用 `'high'` |
| 路由 | 先用便宜模型判断要不要 thinking，再决定交给谁 |
| 缓存 | 同一道题的思考结果存起来复用 |
| 截断显示 | 渲染时只展示思考的前 200 字，加一个展开按钮 |
| 交给用户 | UI 上加一个「深度思考」开关，默认关闭 |

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 设了 `thinking=True`，但没看到 ThinkingPart | 模型不支持 thinking，或者是 OpenAI 兼容端点（千问）不认统一设置 | 换支持的模型（见第 6 节）；千问加 `extra_body={"enable_thinking": True}` |
| OpenAI 推理模型不显示思考内容 | OpenAI 的政策默认不返回思考全文 | 设 `openai_reasoning_summary='detailed'` 拿摘要 |
| 多轮对话第二轮报 "missing thinking signature" | 历史里的 ThinkingPart 被删了 | 把 `all_messages()` 完整传回 |
| Token 暴涨 | thinking effort 太高 | 降到 `'medium'` / `'low'`，或限制 `budget_tokens` |
| 流式时思考和正文混在一起 | 没区分 PartDeltaEvent 属于哪个 part | 用 `event.part` / `event.index` 区分 |
| TestModel 下没有思考输出 | TestModel 不模拟 thinking | 只有真实模型才有 |
| Anthropic + 工具调用偶发 "signature mismatch" | 手工改过思考内容 | 不要改 ThinkingPart 的内容和 signature |
| 思考被截断 | budget_tokens 用完了 | 加大 budget，或者把问题拆简单一些 |

---

## 12. 本章 demo

完整可运行代码：[`demos/advanced/03_thinking.py`](../../demos/advanced/03_thinking.py)

下一篇：[Pydantic AI 进阶 04：Hooks（生命周期钩子）](04-hooks.md)，讲 Agent 的生命周期钩子。
