# PE Models 02：GPT 专用写法

> 给 GPT 系列（当前是 GPT-6 家族）写 prompt，需要知道它的四个特点：更习惯 markdown 结构；developer message 的优先级高于 user；有原生的 Structured Outputs；全系列都能调 reasoning effort。这篇讲这些特点分别怎么影响 prompt 写法。
>
> 本篇以 2026 年 11 月的版本为准：`gpt-6-astra` / `gpt-6.1-sol` / `gpt-6.1-terra` / `gpt-6-luna`，`openai` Python SDK 3.x，官方推荐新代码用 Responses API。

---

## 1. GPT 的脾气

| 倾向 | 含义 |
|------|------|
| Markdown 友好 | `## 标题` / `- bullet` 识别得好 |
| 严格 schema | strict 模式下返回的 JSON 一定符合 schema |
| 偏简洁 | 不太啰嗦，但也不像 Claude 那么收着 |
| developer message 优先于 user | 官方文档只讲 `developer` / `user` / `assistant` 三种角色 |
| 不再区分推理模型和非推理模型 | GPT-6 全系列都有 reasoning effort（Terra/Luna 可设 `none`） |
| 依赖工具描述 | 工具描述写得越清楚，选工具、填参数越准 |

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

markdown 的标题层级、列表、加粗，GPT 都能清楚地识别。

---

## 3. Developer Message

官方 prompt 指南现在只讲三种角色：`developer`（应用的规则和业务逻辑，优先级高于 user）、`user`（输入）、`assistant`。旧的 `system` 在 Chat Completions 里仍然能用，但新代码建议统一写 `developer`。Responses API 里还可以直接用 `instructions` 参数：

```python
from openai import OpenAI
client = OpenAI()

resp = client.responses.create(
    model="gpt-6.1-terra",
    instructions="<开发者级最高优先级指令>",      # 等价于最前面一条 developer message
    input="用户的真实输入",
)
print(resp.output_text)
```

`instructions` 只对当前这次请求生效。用 `previous_response_id` 续接对话时，之前的 instructions 不会自动带过来，每次都要重新传。

---

## 4. Structured Outputs

OpenAI 的 Structured Outputs 用 JSON Schema 约束输出，`strict` 模式下输出一定符合 schema。Responses API 里用 `text_format` 传 Pydantic 类：

```python
from pydantic import BaseModel

class Result(BaseModel):
    category: str
    confidence: float

resp = client.responses.parse(
    model="gpt-6-luna",
    input=[{"role": "user", "content": "App 闪退了"}],
    text_format=Result,
)
parsed = resp.output_parsed      # Result 实例
```

Chat Completions 的等价写法（SDK 3.x 里 `client.chat.completions.parse` 已经是正式方法，不用再走 `beta`）：

```python
resp = client.chat.completions.parse(
    model="gpt-6-luna",
    response_format=Result,
    messages=[...],
)
parsed = resp.choices[0].message.parsed
```

支持嵌套 BaseModel、`Literal` enum、Optional、list；递归、任意 dict、部分 Union 等限制以官方 "Supported schemas" 页面为准。模型拒答时，用 `parse` 要先检查输出里的 `refusal` 项，别直接读 `output_parsed`。

---

## 5. Reasoning effort

GPT-6 全系列都是推理模型，用 `reasoning.effort` 调思考深浅。取值因模型而异：Astra / Sol 支持 low 到 max；Terra / Luna 另外支持 `none`；Astra 设 `none` 会返回 400。

```python
resp = client.responses.create(
    model="gpt-6-astra",
    reasoning={"effort": "low", "summary": "auto"},   # summary 想看推理摘要才加
    input=[{"role": "user", "content": prompt}],
)
```

使用时注意：

- 内置长链推理，推理 token 按输出 token 计费
- prompt 里写清目标、约束、输出格式和完成标准，不要把每一步怎么推都写死。官方的说法是"像对资深同事交代，而不是对新人"
- 官方把 effort 定位成调节手段，而不是补救手段：质量不够时先改 prompt 和评测，再考虑动 effort
- Chat Completions 里对应的参数是 `reasoning_effort="low"`

---

## 6. OpenAI 官方的 Prompting 建议（GPT-6）

### 6.1 目标、约束、格式、验收标准
GPT-6 擅长从高层目标自己推导步骤，三两句说清目标，比写 10 段细节更有效。Agent 或研究类任务要写清楚"做到什么程度算完成、怎么自检"。

### 6.2 按任务选 effort
```python
reasoning={"effort": "low"}    # 简单任务：快、便宜
reasoning={"effort": "high"}   # 编程 / 数学 / 多步规划
```

### 6.3 边界写在 developer message 里
安全规则、角色、输出 schema 放 developer，user 里只放真实数据。

### 6.4 可复用内容放在最前面
指令、few-shot 这些不变的部分放在 prompt 开头，变量放末尾，这样能吃到 prompt caching。上线时固定到某个具体 snapshot，升级前后用评测集对比表现。

### 6.5 工具调用开 strict 模式
```python
tools = [{
    "type": "function",
    "name": "...",
    "parameters": {...},
    "strict": True,   # 强制 schema
}]
```
（Responses API 里 function 定义是扁平的，不再套一层 `"function": {...}`。）

---

## 7. Few-shot：放进 messages 数组

GPT 的 few-shot 常见写法是放进 messages 数组，而不是全堆在 system 里：

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

把示例伪装成之前的几轮对话，GPT 学得很快。

---

## 8. 模型选择

| 模型 | model id | 价格（输入/输出 每百万 token） | 适合 |
|------|----------|------------------------------|------|
| GPT-6 Astra | `gpt-6-astra` | $10 / $50 | 官方推荐的默认通用模型，最难的任务 |
| GPT-6.1 Sol | `gpt-6.1-sol` | $2 / $10 | 通用对话 / 写作 / 编程 |
| GPT-6.1 Terra | `gpt-6.1-terra` | $1 / $5 | 通用，性价比 |
| GPT-6 Luna | `gpt-6-luna` | $0.10 / $0.50 | 简单分类 / 抽取 / 高吞吐 |

全部 1.05M context，支持图像输入，都带 reasoning effort。原来 gpt-4o / gpt-4o-mini 的位置大致由 Terra / Luna 接替。Luna 和 Claude Haiku 5.5 价格相同，同属小模型档，具体用哪个通常要 A/B 一下。

---

## 9. Streaming

GPT API 的流式输出：

```python
stream = client.chat.completions.create(
    model="gpt-6.1-terra",
    messages=[...],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

适合在界面上实时显示。streaming 只改变调用方式，不影响 prompt 怎么写。

---

## 10. demo：GPT 的惯用写法

```python
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
    resp = client.chat.completions.parse(
        model="gpt-6-luna",
        response_format=Classification,
        messages=[
            {"role": "developer", "content": SYSTEM},
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
| 以为 GPT 只认 markdown | GPT 对 XML 标签和 Markdown 标题都能正确理解；跨模型共用 prompt 时，用 XML 标签分隔指令、资料、示例最稳 |
| 不用 Structured Outputs | 自己 parse JSON 容易出错 |
| few-shot 全塞进 system，和指令混在一起 | 放进 messages 数组，或用 XML 标签单独包起来 |
| 给 GPT-6 写 "step by step" | 用 reasoning effort 代替，别写死推理步骤 |
| 新代码还用 system 角色 | 改用 developer 或 `instructions` |
| 所有任务都开高 effort | 简单任务用 low（Terra/Luna 可设 none），否则又贵又慢 |
| 用 `previous_response_id` 续接时没再传 instructions | instructions 不会继承，每次都要传 |

---

## 12. 下一步

- [Gemini 与开源模型](./03-gemini-open.md)
- [跨模型可移植 + 成本性能权衡](./04-cross-model.md)
- Structured Outputs 深入：[结构化输出（JSON / XML / Schema）](../03-techniques/05-structured-output.md)

## 参考资料

- OpenAI Prompt Engineering Guide: https://developers.openai.com/api/docs/guides/prompt-engineering
- Models: https://developers.openai.com/api/docs/models
- Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Reasoning: https://developers.openai.com/api/docs/guides/reasoning
