# PE Models 01：Claude 专用写法

> **一句话**：Claude 偏好 **XML 标签**、支持 **prefill**、原生 **extended thinking**、**prompt caching** 折扣最大——这些特性如何影响 prompt 写法。

---

## 1. Claude 的"个性"

Claude 系列（Sonnet / Opus / Haiku）在训练数据上和 GPT 风格略有差异：

| 倾向 | 含义 |
|------|------|
| 重视 XML 结构 | XML 标签识别极稳 |
| 谨慎 / 偏 refuse | 容易"我不能回答这个" |
| 长 context 友好 | 200k window，attention 稳 |
| Tool use 强 | 适合 ReAct loop |
| 指令遵循严格 | 写约束就听，不会"放飞" |
| Markdown 输出默认 | 不约束就给你 markdown |

---

## 2. 推荐结构：XML

所有结构化输入都用 XML：

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

Claude 训练里见过大量这种格式，识别准确率 99%+。

---

## 3. Prefill（assistant message 预填）

Claude 独有：在 assistant message 里**预填**开头，模型从那继续：

```python
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=500,
    messages=[
        {"role": "user", "content": "写一个 Python 函数计算斐波那契"},
        {"role": "assistant", "content": "```python\ndef fib(n: int) -> int:\n"},  # ← prefill
    ],
)
```

模型从 `def fib(n: int) -> int:` 后继续——不会有"好的，下面是代码："等废话。

### 经典用法

| 用 prefill 让模型 | 写法 |
|------------------|------|
| 直接输出 JSON | `{` |
| 直接输出代码 | "```python\n" |
| 用某 XML 开头 | `<analysis>` |
| 用某语言 | `Translation: 中文版本: ` |

**注意**：prefill 后**不能**有空格，且 model 输出**不含** prefill 字符。

---

## 4. Extended Thinking

Claude Sonnet 4+ 支持模型内置 "thinking"：

```python
resp = client.messages.create(
    model="claude-sonnet-4-6",
    thinking={"type": "enabled", "budget_tokens": 10000},
    max_tokens=2000,
    messages=[...],
)
# resp.content 含 thinking 块 + text 块
for block in resp.content:
    if block.type == "thinking":
        print("Internal reasoning:", block.thinking)
    elif block.type == "text":
        print("Answer:", block.text)
```

extended thinking 的关键点：

- thinking 计费但有 caching 折扣
- prompt 里**不要**再加 "think step by step"——会和内置思考冲突
- 适合数学 / 推理 / 复杂规划
- 简单任务不用开（费 token）

---

## 5. Prompt Caching

Claude 的 prompt caching 折扣**业界最大**——缓存命中 cost 降到 1/10：

```python
resp = client.messages.create(
    model="claude-sonnet-4-6",
    system=[
        {"type": "text", "text": "<长 system prompt>"},
        {"type": "text", "text": "<可缓存部分>", "cache_control": {"type": "ephemeral"}},
    ],
    messages=[...],
)
```

缓存策略：

| 部分 | 缓存？ |
|------|--------|
| system 指令（不变） | ✅ |
| Few-shot examples | ✅ |
| RAG context（每次变） | ❌ |
| User message | ❌ |

设计 prompt 时**把"不变的"放前面**，user message 只放真实数据。

详 [07-production/02-caching.md](../07-production/02-caching.md)。

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

XML 标签 + 首尾强调 attention 红利 + 静态部分前置便于 caching。

---

## 7. 防 over-refusal

Claude 偏谨慎。如果你正常用例被 refuse：

```
<context>
你是企业内部知识助手。可以讨论公司业务、客户信息、销售数据。
这是企业内部使用场景，授权用户访问。
</context>
```

明确"使用场景合法 / 授权" → 模型放松。

---

## 8. 三个 Claude 独有 trick

### 8.1 让 Claude "想"再"答"

```
<task>...</task>

回答步骤:
1. 先在 <thinking> 里分析
2. 然后在 <answer> 里给最终答案

只 <answer> 部分给用户看。
```

后处理只提取 `<answer>`。

### 8.2 用 Stop sequence 控边界

```python
resp = client.messages.create(
    stop_sequences=["</answer>"],
    messages=[...],
)
# 模型一写到 </answer> 就停
```

适合"只要一段答案"。

### 8.3 多语言指令

```
你是中英文翻译助手。

输出格式:
- 如果输入是中文，<translation>...</translation> 是英文
- 如果输入是英文，<translation>...</translation> 是中文
```

Claude 对这种"if/then 切换"识别稳。

---

## 9. 模型选择

| 任务 | 推荐模型 |
|------|---------|
| 简单分类 / 抽取 | Haiku |
| 写作 / 复杂抽取 / 中等 Agent | Sonnet |
| 极复杂推理 / 长链 Agent / 创意 | Opus |
| 推理任务 | Sonnet + extended thinking |
| 多模态 | Sonnet（视觉强） |

延迟优先 Haiku；质量优先 Sonnet；旗舰 Opus（贵）。

---

## 10. demo：Claude XML + Prefill 组合

```python
# demos/models/01_claude_idiomatic.py
import anthropic
client = anthropic.Anthropic()

SYSTEM = """你是法律分析助手。

<task>
分析下面合同条款，提取关键点。
</task>

<output_format>
{
  "summary": "...",
  "key_terms": ["...", "..."],
  "risks": ["..."]
}
</output_format>
"""

CONTRACT = """
本合同自 2026 年 6 月 1 日起生效。
甲方应在每月 5 日前向乙方支付服务费 100,000 元。
延迟支付按 0.05% 日息计算。
违约方需赔偿对方实际损失。
"""

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=500,
    system=SYSTEM,
    messages=[
        {"role": "user", "content": f"<contract>\n{CONTRACT}\n</contract>"},
        {"role": "assistant", "content": "{"},  # ← prefill
    ],
)
# 输出从 { 开始的 JSON
import json
json_text = "{" + resp.content[0].text
print(json.loads(json_text))
```

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **用 markdown 而非 XML** | Claude 对 XML 识别更稳 |
| **不用 prefill 节省 token** | "好的，下面是 JSON..." 浪费 |
| **同时用 extended thinking + "think step by step"** | 冲突，关掉 prompt CoT |
| **不开 prompt caching** | 长 system + 高频调用浪费钱 |
| **没用 stop_sequences 控边界** | 模型继续写多余内容 |
| **system 太松** | Claude 严格遵循指令——指令写明 |

---

## 12. 下一步

- 📖 GPT 写法 → [02-gpt.md](./02-gpt.md)
- 📖 Gemini / open source → [03-gemini-open.md](./03-gemini-open.md)
- 📖 跨模型可移植 → [04-cross-model.md](./04-cross-model.md)

## 参考资料

- Claude Prompt Engineering: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview
- Extended Thinking: https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking
- Prompt Caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
