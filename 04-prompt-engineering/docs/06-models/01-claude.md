# PE Models 01：Claude 专用写法

> 给 Claude 写 prompt，有几条和其他模型不一样的习惯：它对 XML 标签特别敏感；prefill 已经被移除，要改用结构化输出；thinking 的深浅由 adaptive 模式加 effort 参数控制；prompt caching 要显式打断点，缓存读取默认按 0.1× 计费，Opus 5.5 / Sonnet 5.5 为 0.05×。这篇逐条讲这些特性对 prompt 写法的影响。
>
> 本篇以 2026 年 11 月的 Claude 5 家族为准（Fable 5.1 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5），`anthropic` Python SDK 2.x。

---

## 1. Claude 的脾气

Claude 系列（Fable / Opus / Sonnet / Haiku）的训练数据和 GPT 有些差别，用下来能感觉到这些倾向：

| 倾向 | 含义 |
|------|------|
| 重视 XML 结构 | 对 XML 标签的识别非常稳 |
| 偏谨慎，容易拒答 | 常见"我不能回答这个" |
| 长上下文友好 | Claude 5 家族 1M window（Haiku 4.5 仍是 200K），注意力稳定 |
| 工具调用强 | 适合 ReAct 循环 |
| 指令遵循严格 | 写了约束就照做，不会自由发挥 |
| 默认输出 Markdown | 不加约束就给你 markdown |

---

## 2. 推荐结构：XML

结构化的输入一律用 XML 包起来：

```
<task>...</task>

<context>
<doc id="1">...</doc>
<doc id="2">...</doc>
</context>

<question>...</question>

<output_format>
按以下结构输出：
<analysis>...</analysis>
<answer>...</answer>
</output_format>
```

Anthropic 官方也推荐用 XML 标签把指令、资料和示例分开，Claude 对这种结构的识别很稳定。

---

## 3. Prefill 已被移除，改用结构化输出

prefill 是 Claude 早期独有的用法：在最后一条 assistant message 里预先写好开头，让模型接着往下写。Claude 4.6 及之后的所有模型都不再支持它，Opus 4.6+、Sonnet 4.6+、Fable 5/5.1、Opus 5/5.5、Sonnet 5/5.5、Haiku 5.5 收到带 prefill 的请求会直接返回 400。

```python
# ❌ 旧写法：Claude 4.6+ 返回 400
messages=[
    {"role": "user", "content": "写一个 Python 函数计算斐波那契"},
    {"role": "assistant", "content": "```python\ndef fib(n: int) -> int:\n"},
]
```

原来靠 prefill 做的事，现在按官方 "Migrating away from prefilled responses" 的建议这样替代：

| 原来用 prefill 做 | 现在怎么做 |
|------------------|-----------|
| 强制输出 JSON / YAML、做分类 | 结构化输出（`output_config.format`），或带 enum 字段的 tool |
| 去掉"好的，下面是……"开场白 | system 里直接写"直接回答，不要前言，不要以'以下是'开头"；或者要求把答案放进 `<answer>` 标签再后处理 |
| 绕过不必要的拒答 | 在 user 消息里把使用场景说清楚（见第 7 节），不再需要 prefill |
| 续写被截断的输出 | 把已生成的内容放进 user 消息，让模型"从这里继续" |

```python
import anthropic
from pydantic import BaseModel

class Fib(BaseModel):
    code: str
    complexity: str

client = anthropic.Anthropic()
resp = client.messages.parse(          # SDK 校验并解析成 Pydantic 对象
    model="claude-sonnet-5-5",
    max_tokens=1000,
    messages=[{"role": "user", "content": "写一个 Python 函数计算斐波那契，给出代码和时间复杂度"}],
    output_format=Fib,
)
print(resp.parsed_output)
```

> `messages.parse(output_format=...)` 是 SDK 的便捷写法，底层发的是 `output_config={"format": {...}}`；直接用 `messages.create` 时写 `output_config`（旧的顶层 `output_format` 参数已废弃）。详见 [结构化输出（JSON / XML / Schema）](../03-techniques/05-structured-output.md)。

---

## 4. Thinking：adaptive + effort（不再有 budget_tokens）

从 Claude 4.6 开始 thinking 的配置方式变了，`budget_tokens` 在逐步淘汰：

| 模型 | thinking 怎么配 |
|------|----------------|
| Opus 4.6 / Sonnet 4.6 | 推荐 `thinking={"type": "adaptive"}`；`budget_tokens` 已废弃但还能用 |
| Opus 4.7 / 4.8 | `{"type": "adaptive"}`；`budget_tokens` 返回 400 |
| Fable 5/5.1、Opus 5.5、Sonnet 5.5、Haiku 5.5 | thinking 默认就是 adaptive；`budget_tokens` 返回 400；Fable 5.1 / Opus 5.5 / Sonnet 5.5 不能关 thinking（`disabled` 返回 400），用 `effort` 调深浅；Sonnet 5.5 可以发 `{"type": "between_tools"}` 去掉回答前的思考，只保留工具调用之间的简短思考（仅 `low` / `medium` / `high` effort） |

```python
resp = client.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    thinking={"type": "adaptive", "display": "summarized"},  # display 默认是 omitted（thinking 文本为空）
    output_config={"effort": "high"},                        # low | medium | high | xhigh | max
    messages=[...],
)
for block in resp.content:
    if block.type == "thinking":
        print("Reasoning summary:", block.thinking)
    elif block.type == "text":
        print("Answer:", block.text)
```

几个要点：

- 想让模型多想一点或少想一点，改 `effort`，不用改 prompt，也不用给 token 预算。Opus 5.5 和 Haiku 5.5 默认 `medium`，Sonnet 5.5 默认 `high`，上线前要自己做一轮 effort 对比评测
- `display` 默认是 `omitted`：thinking 照样发生、照样计费，只是返回的文本为空。要给用户看推理摘要，就显式设成 `summarized`
- thinking 块要原样随对话历史传回。Claude 5.5 起 thinking 块和产生它的模型、对话绑定，改写历史会让之前的 thinking 失效
- prompt 里不必再写"think step by step"，内置思考已经在做这件事；想让它多想，先调高 effort
- 分类、抽取这类简单任务，把 effort 降到 `low`，或者直接用 Haiku
- `temperature` / `top_p` / `top_k` 已被移除：Claude 4.7+ 不接受这些参数，API 版本 `2026-10-21` 起对所有模型发送它们都会返回 400（`anthropic` 2.x SDK 的 `messages.create` 已经没有这些参数）。要多样性就靠 prompt 或多次采样，要稳定就靠 prompt、结构化输出和评测，见 [Temperature、Top-p 与采样](../01-foundations/04-sampling.md)

## 5. Prompt Caching

Claude 的缓存命中（cache read）默认按基础输入价的 0.1× 计费，Opus 5.5 / Sonnet 5.5 为 0.05×。缓存要显式加 `cache_control` 断点（或在请求顶层开自动缓存），什么都不设就不会缓存：

```python
resp = client.messages.create(
    model="claude-sonnet-5-5",
    system=[
        {"type": "text", "text": "<长 system prompt>"},
        {"type": "text", "text": "<可缓存部分>", "cache_control": {"type": "ephemeral"}},
    ],
    messages=[...],
)
```

哪些部分适合缓存：

| 部分 | 缓存？ |
|------|--------|
| system 指令（不变） | 是 |
| Few-shot examples | 是 |
| RAG context（每次变） | 否 |
| User message | 否 |

所以写 prompt 时把不变的内容放在前面，user message 里只放每次真正变化的数据。

细节见 [Prompt Caching 实战](../07-production/02-caching.md)。

---

## 6. System Prompt 推荐结构

```
你是<role>。

<task>
任务描述
</task>

<constraints>
- 约束 1
- 约束 2
</constraints>

<examples>
<example>...</example>
</examples>

<output_format>
...
</output_format>

重要：<最关键约束的最后强调>
```

这个结构同时照顾了三件事：XML 标签好识别；最关键的约束在 `<constraints>` 里写一次、结尾再用一句「重要：」重复，吃到末尾位置的注意力优势；静态内容都在前面，方便缓存。

---

## 7. 防止过度拒答

Claude 偏谨慎，正常的用例有时也会被拒。以前可以用 prefill 绕过去，现在只能靠把场景交代清楚：

> 另外，Fable / Opus 5.5 / Sonnet 5.5 带安全分类器，命中时返回 `stop_reason: "refusal"`（HTTP 200）并附 `stop_details.category`。读 `content` 之前要先判断 `stop_reason`；官方推荐同时开启 `fallbacks`，命中时自动降级到别的模型。

```
<context>
你是企业内部知识助手。可以讨论公司业务、客户信息、销售数据。
这是企业内部使用场景，授权用户访问。
</context>
```

写明使用场景合法、用户已授权，模型就会放松很多。

---

## 8. 三个 Claude 专属技巧

### 8.1 先想再答

```
<task>...</task>

回答步骤:
1. 先在 <thinking> 里分析
2. 然后在 <answer> 里给最终答案

只 <answer> 部分给用户看。
```

后处理时只取 `<answer>` 里的内容。

### 8.2 用 stop sequence 控制边界

```python
resp = client.messages.create(
    stop_sequences=["</answer>"],
    messages=[...],
)
# 模型一写到 </answer> 就停
```

只需要一段答案时很好用。

### 8.3 多语言指令

```
你是中英文翻译助手。

输出格式:
- 如果输入是中文，<translation>...</translation> 是英文
- 如果输入是英文，<translation>...</translation> 是中文
```

这种 if/then 式的切换，Claude 识别得很稳。

---

## 9. 模型选择

| 模型 | model id | 价格（输入/输出 每百万 token） | 适合 |
|------|----------|------------------------------|------|
| Haiku 5.5 | `claude-haiku-5-5` | $0.10 / $0.50 | 简单分类 / 抽取 / 高吞吐 |
| Sonnet 5.5 | `claude-sonnet-5-5` | $2 / $10 | 写作 / 复杂抽取 / 中等 Agent，视觉强 |
| Opus 5.5 | `claude-opus-5-5` | $4 / $20 | 复杂推理 / 长链 Agent / 创意 |
| Fable 5.1 | `claude-fable-5-1` | $10 / $50 | 最难的推理与长程 agent 任务 |

- 全部 1M context（Haiku 4.5 等旧模型是 200K）；Fable 5.1 / Opus 5.5 / Sonnet 5.5 不能关 thinking，用 effort 控制深浅（Sonnet 5.5 可用 `between_tools` 去掉回答前的思考，见第 4 节）
- 推理类任务想要更好的结果，先在同一个模型上调高 `effort`，而不是去改 prompt
- 延迟和成本优先选 Haiku，质量优先选 Sonnet / Opus；价格以官方页面为准
- 官方有按模型分的 prompting 指南，每个新模型一页，写明和上一代相比 prompt 要怎么调。升级模型时先读那一页：给旧模型写的那种很啰嗦、反复强调的 prompt（"CRITICAL: 你必须……"），在新模型上往往会被过度执行，需要删减

---

## 10. demo：Claude XML + 结构化输出

```python
import anthropic
from pydantic import BaseModel

client = anthropic.Anthropic()

class ContractAnalysis(BaseModel):
    summary: str
    key_terms: list[str]
    risks: list[str]

SYSTEM = """你是法律分析助手。

<task>
分析 <contract> 里的合同条款，提取关键点。
</task>
"""

CONTRACT = """
本合同自 2026 年 6 月 1 日起生效。
甲方应在每月 5 日前向乙方支付服务费 100,000 元。
延迟支付按 0.05% 日息计算。
违约方需赔偿对方实际损失。
"""

resp = client.messages.parse(
    model="claude-sonnet-5-5",
    max_tokens=1000,
    system=SYSTEM,
    messages=[{"role": "user", "content": f"<contract>\n{CONTRACT}\n</contract>"}],
    output_format=ContractAnalysis,      # 代替旧的 prefill "{"
)
print(resp.parsed_output)
```

> 运行这段需要 Anthropic API key。其中 XML 标签的写法对 Qwen 等模型同样适用，见 [跨模型可移植 + 成本性能权衡](./04-cross-model.md)。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| 用 markdown 而不是 XML 分隔 | Claude 对 XML 识别更稳 |
| 还在用 prefill | Claude 4.6+ 返回 400；改用结构化输出，或在 system 里禁止前言 |
| 开着内置 thinking 又写"think step by step" | 多半是多余的，去掉 prompt 里的 CoT，改用 effort 控制 |
| 用 `resp.content[0].text` 取答案 | Claude 5.x 默认带 adaptive thinking，`content[0]` 经常是文本为空的 thinking 块；取 `content[-1].text`，或遍历找 `type == "text"` |
| `max_tokens` 给得太小 | 思考 token 也计入 `max_tokens`，给 50、200 这种小值可能在输出前就被截断；本手册的 Claude 示例都给到 1024 以上，简单任务还可以把 `effort` 降到 `low`，并检查 `stop_reason` |
| 还在传 `budget_tokens` / `temperature` | 新模型直接 400；改用 `effort`，删掉采样参数 |
| 不开 prompt caching | 长 system + 高频调用，白花钱 |
| 没用 stop_sequences 控制边界 | 模型会继续写多余内容 |
| system 写得太松 | Claude 严格照指令办事，想要什么就明确写出来 |

---

## 12. 下一步

- [GPT 专用写法](./02-gpt.md)
- [Gemini 与开源模型](./03-gemini-open.md)
- [跨模型可移植 + 成本性能权衡](./04-cross-model.md)

## 参考资料

- Prompting best practices（含各模型专页、prefill 迁移）: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- Adaptive thinking / Effort: https://platform.claude.com/docs/en/build-with-claude/thinking
- Structured outputs: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Prompt Caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
