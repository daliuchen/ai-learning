# Pydantic AI 进阶 03：Thinking（显式思考链）

> **一句话**：Thinking 是 Claude / OpenAI o-series 等模型的"模型先在内部想一阵子、再开口答"的能力，Pydantic AI 用 `ThinkingPart` 这一个统一类型把它从所有 provider 抽出来，让你不用再去看各家 SDK 是 `<think>` 标签还是 `reasoning_content` 字段。

---

## 1. 什么是 Thinking

普通对话模型的输出基本只有一种：**模型一口气把答案写完**。但有些模型（Claude 3.7+ Extended Thinking、OpenAI o1 / o3 / o4-mini、Gemini Flash Thinking 等）支持"先思考、再回答"两段式：

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

`<thinking>` 那段是**模型的草稿**，对用户不可见、不影响主回答的语义。但开发者经常想看：

- 调试：模型为什么答错了，看草稿就知道
- 透明度：给用户展示"AI 是怎么想的"（比如 Claude 的折叠思考块）
- 决策记录：把推理过程存档供审计

各 provider 暴露思考的格式完全不同：

| Provider | 暴露方式 |
|----------|---------|
| Anthropic Claude | `content: [{type: "thinking", thinking: "...", signature: "..."}, ...]` |
| OpenAI Responses | `response.output[].summary[].text` |
| DeepSeek | `reasoning_content` 字段 |
| Gemini Thinking | 标签嵌入正文 |

Pydantic AI 的处理：**全部转成 `ThinkingPart` 放进 ModelResponse 的 parts 数组**。

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

它和 `TextPart` / `ToolCallPart` 并列，是 `ModelResponse.parts` 列表里的一种元素：

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

Pydantic AI 提供了**一个跨 provider 通用**的开关：

```python
from pydantic_ai import Agent

# 通过 model_settings
agent = Agent(
    "anthropic:claude-opus-4-7",
    model_settings={"thinking": "high"},
)

# 或通过 Capability
from pydantic_ai.capabilities import Thinking

agent = Agent(
    "anthropic:claude-opus-4-7",
    capabilities=[Thinking(effort="high")],
)
```

`thinking` / `effort` 接受值：

| 值 | 含义 |
|----|------|
| `True` | 默认强度 |
| `False` | 关闭 |
| `'minimal'` | 最低强度（OpenAI） |
| `'low'` / `'medium'` / `'high'` | 三档强度 |
| `'xhigh'` | 极高（Claude Opus 4.7+） |

底层 Pydantic AI 会自动翻译成每家 provider 的具体参数。

### 3.2 Anthropic 原生设置（更细控制）

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

agent = Agent("anthropic:claude-opus-4-7", model_settings=settings)
```

`budget_tokens` 是上限，模型可能用不到那么多。Adaptive 模式则让模型自己拿捏。

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
model = OpenAIResponsesModel("gpt-5.2")  # 或 o3 / o4-mini
agent = Agent(model, model_settings=settings)
```

**注意**：OpenAI 的 reasoning 模型默认不输出思考全文，`reasoning_summary="detailed"` 才能拿到"思考摘要"。完整 chain-of-thought 是不返回给用户的，这是 OpenAI 的政策限制。

---

## 4. 取出思考结果

跑完之后从 `all_messages()` 里翻：

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

或者一行写法：

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

流式里**思考会先于正文到达**，你可以分别渲染：

```python
import asyncio
from pydantic_ai import Agent
from pydantic_ai.messages import ThinkingPart, TextPart, PartDeltaEvent, PartStartEvent

agent = Agent("anthropic:claude-opus-4-7", model_settings={"thinking": "high"})

async def main():
    async with agent.iter("证明 √2 是无理数") as run:
        async for node in run:
            # 节点级
            pass

asyncio.run(main())
```

更细的"思考增量流"用 `run_stream_events()`：

```python
async with agent.run_stream_events("证明 √2 是无理数") as events:
    async for event in events:
        # event 可能是 PartStartEvent / PartDeltaEvent / AgentRunResultEvent
        if isinstance(event, PartStartEvent) and isinstance(event.part, ThinkingPart):
            print("\n[开始思考]", end="", flush=True)
        elif isinstance(event, PartDeltaEvent):
            print(event.delta.content_delta, end="", flush=True)
```

实际格式会因 provider 不同而略有差异，但**思考块和正文块在事件流里是分开的**这条不变。

---

## 6. Provider 支持矩阵

| Provider / 模型 | 支持 Thinking | 推荐方式 |
|-----------------|---------------|----------|
| Anthropic Claude 3.7 Sonnet | ✅ Extended | `anthropic_thinking={"type": "enabled", "budget_tokens": N}` |
| Anthropic Claude Opus 4.6+ | ✅ Adaptive | `anthropic_thinking={"type": "adaptive"}` + `effort` |
| OpenAI o1 / o3 / o4-mini | ✅ Reasoning | `openai_reasoning_effort=...` |
| OpenAI gpt-5 系列 | ✅ Reasoning | 同上 |
| Google Gemini 2.0 Flash Thinking | ✅ | `thinking=True` |
| DeepSeek-R1 | ✅ | `thinking=True` |
| 其他普通模型 | ❌ | 设置无效 |

**便捷判断**：如果模型名字带 `o1` / `o3` / `o4` / `r1` / `thinking` / `reasoning`，多半支持；否则不支持。

---

## 7. 实战：复杂数学题展开链路

```python
from pydantic_ai import Agent
from pydantic_ai.messages import ThinkingPart, TextPart

agent = Agent(
    "anthropic:claude-opus-4-7",
    model_settings={"thinking": "high"},
    system_prompt="你是一位严谨的数学老师，先思考再答题。",
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

print(f"\n[usage] {result.usage()}")
```

你会看到模型先"考虑数学归纳法"、"考虑 Gauss 配对法"、再选定一种写出证明。

---

## 8. 普通对话 vs Thinking 对话差异

| 维度 | 普通对话 | Thinking 对话 |
|------|---------|---------------|
| 首字延迟 | 短（毫秒级） | **长**（思考阶段可能几秒到几十秒） |
| 总耗时 | 短 | 显著长 |
| token 成本 | 只计回答 | **加上思考的 token**（OpenAI 也会计费） |
| 准确率（推理题） | 一般 | **大幅提升** |
| 准确率（事实题） | 一般 | **提升不明显** |
| 适合场景 | 闲聊 / 总结 / 翻译 | 数学 / 代码 / 多步推理 / 规划 |

**结论**：不是所有对话都该开 Thinking。**简单任务开了反而又贵又慢**。一个常见做法是用"路由 Agent"先判断难度再决定要不要开。

---

## 9. 与工具调用共存

Thinking 和工具调用可以共存，但有几个微妙点：

1. 模型会在"决定调用工具"前先思考，思考内容里可能包含"我应该调用 weather 工具"
2. **Anthropic 的 signature 字段必须随消息历史回传**，否则下一轮思考链断裂——Pydantic AI 自动处理这点
3. 流式中工具调用可能在思考块之后出现，事件流顺序是 `ThinkingPart → ToolCallPart → ToolReturnPart → TextPart`

如果你手工构造历史消息（比如断点续传），**别把 ThinkingPart 删掉**，否则 Claude 会拒绝认这段历史。

---

## 10. 成本与延迟控制

| 手段 | 说明 |
|------|------|
| 限制 budget | `budget_tokens=2048` 防止模型一直想 |
| 降 effort | 简单题用 `'low'`，难题才用 `'high'` |
| 路由 | 先用便宜模型判断是否需要 thinking，再决定下一跳 |
| 缓存 | 同一题的思考结果存起来复用 |
| 截断思考 | 渲染时只展示思考摘要（前 200 字）+ 一个"展开"按钮 |
| 用户可控 | UI 加 "深度思考" 开关，默认关闭 |

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 设了 `thinking=True` 但没看到 ThinkingPart | 用的模型不支持 thinking | 换支持的模型（看 §6） |
| OpenAI o3 不显示思考内容 | OpenAI 政策默认不返回思考全文 | 设 `openai_reasoning_summary='detailed'` 拿摘要 |
| 多轮对话第二轮报"missing thinking signature" | 历史里把 ThinkingPart 删掉了 | 保持 `all_messages()` 完整传回 |
| Token 暴涨 | thinking effort 太高 | 降到 `'medium'` / `'low'` 或限制 `budget_tokens` |
| 流式时思考和正文混在一起 | 没区分 PartDeltaEvent 的目标 part | 用 `event.part` / `event.index` 区分 |
| TestModel 下没思考输出 | TestModel 不模拟 thinking | 真实模型才有 |
| Anthropic + 工具调用偶发"signature mismatch" | 自己手工修改了思考内容 | 不要改 ThinkingPart 内容和 signature |
| 思考被截断 | budget_tokens 用满了 | 加大 budget，或者降低问题难度 |

---

## 12. 本章 demo

完整可运行代码：[`demos/advanced/03_thinking.py`](../../demos/advanced/03_thinking.py)

下一篇：[04-hooks.md](04-hooks.md) —— Agent 生命周期钩子全攻略。
