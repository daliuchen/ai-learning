# PE Models 02：GPT 专用写法

> **一句话**：GPT 系列（GPT-5 / 4o / 4o-mini）偏好 **markdown 结构**、有 **developer message** 优先级、原生 **Structured Outputs**、**reasoning models** 支持 effort 调整——这些特性影响 prompt 写法。

---

## 1. GPT 的"个性"

| 倾向 | 含义 |
|------|------|
| Markdown 友好 | `## 标题` / `- bullet` 识别好 |
| 严格 schema | structured output 100% 准确 |
| 偏简洁 | 不太啰嗦，但不像 Claude 那么"克制" |
| developer message > system | 新模型推荐 |
| reasoning models 独立 | o1 / o3 / GPT-5 系列推理强 |
| Tool use 不如 Claude 稳 | 工具描述质量更敏感 |

---

## 2. 推荐结构：Markdown

```
# Role
You are a customer service classifier.

# Task
Classify the user feedback into one of 8 categories.

## Categories
- **bug**: software errors
- **feature**: requests for new features
- ...

## Output Format
JSON with fields: category, confidence, reasoning

## Examples
- "App crashes" → bug
- "Add dark mode" → feature

# Important
- Strictly use the enum
- Empty / off-topic → "other"
```

GPT 对 markdown 标题层级、bullet、bold 都有清晰识别。

---

## 3. Developer Message vs System Message

GPT-5 后引入 `developer` role，优先级 > `system` > `user`：

```python
client.chat.completions.create(
    model="gpt-5",
    messages=[
        {"role": "developer", "content": "<开发者级最高优先级指令>"},
        {"role": "system", "content": "<可被 user 覆盖的指令>"},
        {"role": "user", "content": "..."},
    ],
)
```

新代码推荐用 `developer`——更稳的安全约束。

---

## 4. Structured Outputs

OpenAI 在 2024 后期推出 **Structured Outputs**：100% 满足 schema：

```python
from pydantic import BaseModel

class Result(BaseModel):
    category: str
    confidence: float

resp = client.beta.chat.completions.parse(
    model="gpt-4o-mini",
    response_format=Result,
    messages=[...],
)
parsed = resp.choices[0].message.parsed  # Result 实例
```

支持：
- 嵌套（多层 BaseModel）
- Literal enum
- Optional fields
- list / dict

不支持：
- Union（讨论中）
- 递归 schema
- 任意 dict

---

## 5. Reasoning Models（o1 / o3 / GPT-5）

OpenAI 的推理模型用 **reasoning_effort**：

```python
resp = client.chat.completions.create(
    model="gpt-5",
    reasoning_effort="medium",   # low / medium / high
    messages=[...],
)
```

特性：
- 内置长链推理
- 推理 token 单独计费（折扣）
- 不需要 "think step by step"
- 适合数学 / 编程 / 多步规划

---

## 6. GPT-5 的 Prompting 建议（官方）

OpenAI 官方 GPT-5 Prompting Guide 重点：

### 6.1 Less is more
GPT-5 不需要冗长指令——一句话清楚比 10 段啰嗦好。

### 6.2 显式 reasoning_effort
```python
# 简单任务用 low（快、便宜）
reasoning_effort="low"
# 编程 / 数学 用 high
reasoning_effort="high"
```

### 6.3 用 developer message 写边界
安全 / role / 输出 schema 放 developer，user 放真实数据。

### 6.4 Tool use 用 strict mode
```python
tools = [{
    "type": "function",
    "function": {
        "name": "...",
        "parameters": {...},
        "strict": True,   # 强制 schema
    }
}]
```

---

## 7. Few-shot：用 messages 数组

GPT 推荐 few-shot 走 messages 数组（不在 system 里堆）：

```python
messages=[
    {"role": "system", "content": "你是分类器..."},
    {"role": "user", "content": "App 闪退"},
    {"role": "assistant", "content": '{"category": "bug"}'},
    {"role": "user", "content": "希望加深色模式"},
    {"role": "assistant", "content": '{"category": "feature"}'},
    {"role": "user", "content": "客服真差"},   # 真实输入
]
```

把"示例"模拟成"过去的对话"——GPT 学得快。

---

## 8. 模型选择

| 任务 | 推荐 |
|------|------|
| 简单分类 / 抽取 | gpt-4o-mini |
| 通用对话 / 写作 | gpt-4o |
| 推理 / 编程 / 数学 | gpt-5 (reasoning) |
| 极便宜 / 大量调用 | gpt-4o-mini |
| 多模态 | gpt-4o |

GPT-4o-mini vs Claude Haiku 都是"小模型"层——常需 A/B 测看哪个更好。

---

## 9. Streaming

GPT API 的 streaming：

```python
stream = client.chat.completions.create(
    model="gpt-4o",
    messages=[...],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

适合 UI 实时显示。streaming 不影响 prompt 设计——只影响调用方式。

---

## 10. demo：GPT idiomatic

```python
# demos/models/02_gpt_idiomatic.py
from typing import Literal
from pydantic import BaseModel, Field
from openai import OpenAI

client = OpenAI()


class Classification(BaseModel):
    category: Literal["bug", "feature", "complaint", "praise", "question", "billing", "account", "other"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(max_length=50)


SYSTEM = """# Role
You are a customer service classifier.

# Task
Classify the user input into one category.

## Categories
- **bug**: software errors / crashes
- **feature**: requests for new functionality
- **complaint**: service / experience complaints (non-bug)
- **praise**: positive feedback
- **question**: how-to / usage
- **billing**: payment / refund
- **account**: login / password
- **other**: fallback for empty / off-topic / unclear

## Rules
- Use the enum strictly
- Empty / off-topic → "other"
- Sarcasm → classify by intent (e.g., "great, never using again" → complaint)
"""


def classify(text: str) -> Classification:
    resp = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
        response_format=Classification,
        messages=[
            # 注：旧 API 用 system，新模型推荐 developer
            {"role": "system", "content": SYSTEM},
            # Few-shot 走 messages
            {"role": "user", "content": "App crashes on startup"},
            {"role": "assistant", "content": '{"category": "bug", "confidence": 0.95, "reasoning": "explicit crash report"}'},
            {"role": "user", "content": "Please add dark mode"},
            {"role": "assistant", "content": '{"category": "feature", "confidence": 0.92, "reasoning": "feature request"}'},
            # 真实输入
            {"role": "user", "content": text or "(empty)"},
        ],
    )
    return resp.choices[0].message.parsed


if __name__ == "__main__":
    TESTS = ["客服真差", "怎么改密码", "退款失败", "好用 5 星"]
    for t in TESTS:
        print(classify(t))
```

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **用 XML 而非 markdown** | GPT 不像 Claude 那么钟爱 XML |
| **不用 Structured Outputs** | 自己 parse JSON 容易挂 |
| **Few-shot 都塞 system** | 走 messages 数组更稳 |
| **GPT-5 用 "step by step"** | 用 reasoning_effort 替代 |
| **GPT-5 用 system 不用 developer** | 新代码推荐 developer |
| **小任务用 reasoning model** | overkill，gpt-4o-mini 即可 |

---

## 12. 下一步

- 📖 Gemini / open source → [03-gemini-open.md](./03-gemini-open.md)
- 📖 跨模型适配 → [04-cross-model.md](./04-cross-model.md)
- 📖 OpenAI Structured Output 深入 → [03-techniques/05-structured-output.md](../03-techniques/05-structured-output.md)

## 参考资料

- OpenAI Prompt Engineering Guide: https://platform.openai.com/docs/guides/prompt-engineering
- GPT-5 Prompting Guide: https://platform.openai.com/docs/guides/gpt-5-prompting
- Structured Outputs: https://platform.openai.com/docs/guides/structured-outputs
- Reasoning Models: https://platform.openai.com/docs/guides/reasoning
