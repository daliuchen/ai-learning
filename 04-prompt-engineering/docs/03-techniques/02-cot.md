# PE Technique 02：Chain-of-Thought（CoT）

> CoT 就是让模型在给出最终答案之前先把推理写出来。最简单的写法是在 prompt 末尾加一句 "let's think step by step"，更稳的写法是让模型把推理放进 `<thinking>` 标签。多步推理的任务上提升明显，简单任务上却只是白花 token。这篇讲什么时候该用、怎么用才有效。

---

## 1. 一个 CoT 能起死回生的例子

```
[zero-shot] 一个班 30 人，男生比女生多 4 个，问男生女生各几个？

回答 (no CoT): 男生 18, 女生 14   ← 错
回答 (with CoT, "先一步步推理"):
  设女生为 x，男生为 x + 4
  x + x + 4 = 30
  2x = 26
  x = 13
  → 女生 13, 男生 17   ← 对
```

CoT 把一次性给答案改成先列推理步骤、再总结答案，模型用更多 token 把题算出来。

---

## 2. CoT 的三种写法

### 2.1 最简写法
在 prompt 末尾加一句：

```
请一步步思考。
Let's think step by step.
```

几乎不用改 prompt，适合不在乎输出里多一段推理的场景。

### 2.2 结构化写法（推荐）
让模型把思考放进指定的标签：

```
回答之前，请按以下步骤思考：
1. 在 <thinking> 标签里写你的推理过程
2. 在 <answer> 标签里给最终答案

示例输出：
<thinking>
... 推理 ...
</thinking>
<answer>
17
</answer>
```

这样做有三个好处：程序可以直接提取 `<answer>`；推理过程留着，出错时可以回看；给思考一个明确的位置，效果比无结构的 step by step 好。

### 2.3 模型内置 thinking
Claude 的 adaptive thinking（4.6 起的写法，5.x 家族默认开启）、GPT-6 的 reasoning、Gemini 3+ 的 thinking，都是在 API 层自带思考 token，不会混进正文（或者单独返回）。现在的主流模型默认就在思考，多数时候不必再写 CoT prompt，调 effort 就行。

```python
# Claude adaptive thinking：深浅用 effort 控（旧的 budget_tokens 在 4.7+/5.x 上返回 400）
resp = client.messages.create(
    model="claude-sonnet-5-5",
    thinking={"type": "adaptive", "display": "summarized"},   # display 默认 omitted（thinking 文本为空）
    output_config={"effort": "medium"},                        # low | medium | high | xhigh | max
    max_tokens=4000,
    messages=[{"role": "user", "content": "..."}],
)
# resp.content 包含 thinking 块（摘要）+ text 块
```

```python
# GPT-6 reasoning
resp = client.responses.create(
    model="gpt-6.1-terra",
    reasoning={"effort": "medium"},
    input=[...],
)

# Gemini 3+ thinking
from google.genai import types
resp = gclient.models.generate_content(
    model="gemini-3.8-flash",
    contents="...",
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level="medium"),
    ),
)
```

内置 thinking 是专门训练过的，通常比手写的 CoT prompt 效果更好、效率更高。

---

## 3. 什么时候用 CoT

| 任务 | 用 CoT？ |
|------|---------|
| 多步算术 / 数学 | 强烈推荐（或用 Program of Thoughts） |
| 多步推理（逻辑、规划） | 用 |
| 复杂多类别分类（10+ 类） | 看情况，边界判断时有帮助 |
| 长文档总结 / 提取 | 看复杂度 |
| 简单分类（< 5 类） | 不用，浪费 token |
| 翻译 / 改写 | 不用 |
| 单词填空 / 简短问答 | 不用 |
| 创意生成 | 不用（CoT 会让输出变僵） |

判断标准：人做这件事要不要先打草稿？要，CoT 就有用；不要，直接答更省。

---

## 4. CoT 的代价

| 代价 | 量级（经验值） |
|------|------|
| Token 成本 | 通常多 200-2000 tokens（是简单回答的 3-10 倍） |
| 延迟 | 推理 token 也要生成，慢 1-5 倍 |
| 输出处理 | 要用程序提取答案部分 |
| 流式体验差 | 用户要先看一大段推理才看到答案 |

省钱的办法：

- 用内置 thinking，并按任务调低 effort。thinking token 照常计费，只是省了手写推理，简单任务把 effort 降到 low。
- 推理放在 system 里引导，最终答案用 structured output 收口（tool 或 response_format 强制 schema）。
- 高频任务先试 zero-shot，够用就不上 CoT。

---

## 5. CoT 进阶变体

### 5.1 Self-consistency CoT
同一个问题跑 N 次 CoT，投票取最常见的答案。见 [Self-Consistency 采样投票](./09-self-consistency.md)。

### 5.2 Tree of Thoughts (ToT)
思路不再一条走到底，而是分叉、评估、回溯。它的调用次数是 CoT 的好几倍，日常任务很少值得，只在搜索空间大、单条推理容易走偏的题目上考虑。

### 5.3 Program of Thoughts (PoT)
不让模型写自然语言推理，而是写代码，交给 Python 去算。数学和算术任务上，它比 CoT 还好：

```
请写 Python 代码解决以下问题，不要直接回答。
我会执行代码并把结果给你。

问题：{problem}
```

```python
# 你的执行环境
def execute(code: str) -> str:
    # safe exec ... 实际用 sandboxes
    return run_python(code)
```

### 5.4 Plan-and-Solve
明确分成两步：先列计划，再按计划执行。

```
# 第一步: 不解决问题，先列计划
"列出解决这个问题的步骤计划。"

# 第二步: 按计划执行
"按上述计划逐步解决，给出最终答案。"
```

适合链条长的任务。

---

## 6. CoT 的常见坑

几个排查起来比较直接的问题：

| 坑 | 排查 |
|----|------|
| 没有用程序提取 answer | 把推理当答案用，下游解析失败 |
| `<thinking>` 内容泄漏给用户 | 在 UI 层过滤 |
| 还在传 `budget_tokens` | Claude 4.7+/5.x 会返回 400，改用 `effort` |
| CoT 太长超出 token 限制 | thinking 也占 max_tokens |

下面四个更容易被忽略，单独说一下。

### 6.1 CoT 也会错
模型可能胡乱推理一通，最后给个错答案。有了 CoT 并不等于稳了，最好再配上 self-consistency 和人工抽检。

### 6.2 简单任务上反而变差
模型用力过猛，把简单的事想复杂了，研究里叫 overthinking：

```
问题：1 + 1 = ?
不带 CoT: 2
带 CoT: "首先我需要理解什么是 1 + 1...在十进制中...也许在二进制下...答案可能是 2 也可能是 10..."
```

所以只在复杂任务上用 CoT。

### 6.3 推理显式化后可能被劫持
攻击者看到 `<thinking>` 标签，可以往里注入「忽略指令」之类的内容。对策是把 thinking 块当作不可信内容，最终行为交给结构化输出和 tool call 来约束。

### 6.4 开了内置 thinking 就别再写 "step by step"
Claude 的内置 thinking 已经在推理，prompt 里再叠一句 "let's think step by step" 多半是多余的。想让它想得更深，先调高 effort；还不够，再用官方文档给的那种引导句，比如 "This task involves multistep reasoning. Think carefully before responding."。

---

## 7. 实战 demo

```python
"""对比 zero-shot / simple CoT / structured CoT / 调高 effort 的内置 thinking"""
# 注意：Claude 5.x 默认就带 adaptive thinking，所以 zero-shot 也在"思考"；
# 想看到 CoT prompt 本身的效果，用不带内置 thinking 的模型（如 Qwen flash / 老模型）对比更干净。
import re
import anthropic

client = anthropic.Anthropic()
PROBLEM = "一个班 30 人，男生比女生多 4 个，男女各几个？只返回最终数字 '男=X 女=Y' 不要别的。"

def zero_shot():
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=2000,  # 5.x 默认带 thinking，max_tokens 要给思考留空间
        messages=[{"role": "user", "content": PROBLEM}],
    )
    return resp.content[-1].text

def simple_cot():
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": PROBLEM + "\n\n请一步步思考。"}],
    )
    return resp.content[-1].text

def structured_cot():
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=4000,
        system="""回答数学题时，先在 <thinking> 里推理，然后在 <answer> 里只放最终答案。""",
        messages=[{"role": "user", "content": PROBLEM}],
    )
    return resp.content[-1].text

def builtin_thinking():
    resp = client.messages.create(
        model="claude-sonnet-5-5",
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        max_tokens=4000,
        messages=[{"role": "user", "content": PROBLEM}],
    )
    for block in resp.content:
        if block.type == "text":
            return block.text
    return ""


for name, fn in [("zero-shot", zero_shot), ("simple-cot", simple_cot),
                  ("structured-cot", structured_cot), ("builtin-thinking", builtin_thinking)]:
    print(f"\n=== {name} ===")
    print(fn())
```

在复杂的数学题上，后三种写法明显更稳。

---

## 8. 三家对比

| 维度 | Anthropic | OpenAI | Gemini |
|------|-----------|--------|--------|
| 简单 CoT | "请一步步推理" | 同 | 同 |
| 结构化 CoT | 推荐 `<thinking>` `<answer>` XML | 推荐 markdown 标题分段 | 同 OpenAI |
| 内置 thinking | adaptive thinking + `effort`（4.6+；5.x 默认开） | `reasoning.effort`（GPT-6 全系列） | `thinking_level`（3+，默认开） |
| Thinking 计费 | 按输出 token 计 | 推理 token 计入输出 | 思考 token 计入 `max_output_tokens` 与计费 |

---

## 9. 下一步

- [Role Prompting 与角色边界](./03-role-prompting.md)
- [Decomposition 任务拆解](./04-decomposition.md)：可以看作 CoT 的工程化版本
- [Self-Consistency 采样投票](./09-self-consistency.md)
- [Tool Use Prompting](../04-advanced/02-tool-use.md)：Program of Thoughts 的延伸

## 参考资料

- "Chain-of-Thought Prompting Elicits Reasoning" (Wei et al. 2022): https://arxiv.org/abs/2201.11903
- Claude Adaptive thinking: https://platform.claude.com/docs/en/build-with-claude/thinking
- OpenAI Reasoning: https://developers.openai.com/api/docs/guides/reasoning
- Gemini Thinking: https://ai.google.dev/gemini-api/docs/thinking
- "Program of Thoughts" (Chen et al. 2022): https://arxiv.org/abs/2211.12588
