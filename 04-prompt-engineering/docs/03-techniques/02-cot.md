# PE Technique 02：Chain-of-Thought（CoT）

> **一句话**：CoT = 让模型在给最终答案前"先思考"。最简单的写法是"let's think step by step"，更稳的是"让模型把推理写到 `<thinking>` 里"。CoT 对推理任务能涨 10-30 分，但**对简单任务是浪费**——本篇讲怎么决定什么时候用、怎么用得有效。

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

CoT 把"一次性出答案"改成"先列推理步骤再总结答案"，让模型用更多 token 算出来。

---

## 2. CoT 的三种写法

### 2.1 最简（口诀型）
在 prompt 末尾加：

```
请一步步思考。
Let's think step by step.
```

适合：简单加入，零改动；不在乎输出多了一段推理。

### 2.2 结构化（推荐）
让模型把思考放进特定容器：

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

好处：
- 你能**程序提取** answer
- 推理过程对调试有用，不行时回看
- 给思考"显式空间"，比无结构 step by step 效果好

### 2.3 模型内置 thinking
Claude 4.x 的 **extended thinking** 模式、GPT-5 的 **reasoning** 模式——模型在 API 层自带 thinking token，输出里不会泄漏（或单独分开）。

```python
# Claude extended thinking
resp = client.messages.create(
    model="claude-sonnet-4-6",
    thinking={"type": "enabled", "budget_tokens": 5000},
    max_tokens=1000,
    messages=[{"role": "user", "content": "..."}],
)
# resp.content 包含 thinking 块（如果你想看）+ text 块
```

```python
# GPT-5 reasoning
resp = client.chat.completions.create(
    model="gpt-5",
    reasoning_effort="medium",
    messages=[...],
)
```

这种"内置 thinking"比手写 CoT prompt 通常**更好**——专门训练过，效率更高。

---

## 3. 什么时候用 CoT

| 任务 | 用 CoT？ |
|------|---------|
| 多步算术 / 数学 | ✅ 强推荐（或 Program of Thoughts） |
| 多步推理（逻辑、规划） | ✅ |
| 复杂多类别分类（10+ 类） | ⚠️ 边界判断时有帮助 |
| 长文档总结 / 提取 | ⚠️ 看复杂度 |
| 简单分类（< 5 类） | ❌ 浪费 token |
| 翻译 / 改写 | ❌ |
| 单词填空 / 简短问答 | ❌ |
| 创意生成 | ❌（CoT 让输出僵化） |

判断标准：**人类做这件事会不会"先想想"**？需要 → CoT 有用；不需要 → 直接答更省。

---

## 4. CoT 的代价

| 代价 | 量级 |
|------|------|
| Token 成本 | 通常 +200-2000 tokens (3-10x 简单回答) |
| 延迟 | 推理 token 也要生成，慢 1-5x |
| 输出处理 | 要程序提取答案部分 |
| Streaming 体验差 | 用户看到一大段 thinking 才看到答案 |

**节流技巧**：

- 用 Claude extended thinking / GPT-5 reasoning（thinking 不计费 / 折扣）
- 让 CoT 走 system，最终答案走 structured output（用 tool / response_format 强制 schema）
- 高频任务先 zero-shot 试，跑得动就不上 CoT

---

## 5. CoT 进阶变体

### 5.1 Self-consistency CoT
跑 N 次 CoT，投票选最常见答案。详 [09-self-consistency.md](./09-self-consistency.md)。

### 5.2 Tree of Thoughts (ToT)
不是一条思路走到底，而是分叉、评估、回溯。详 [04-advanced/03](../04-advanced/03-rag-prompting.md)（在 advanced 章用）。

### 5.3 Program of Thoughts (PoT)
让模型不写"自然语言推理"，写**代码**——交给 Python 执行算。
对数学 / 算术任务，**比 CoT 还好**：

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
明确分两步：先列计划，再按计划执行。

```
# 第一步: 不解决问题，先列计划
"列出解决这个问题的步骤计划。"

# 第二步: 按计划执行
"按上述计划逐步解决，给出最终答案。"
```

适合长链条任务。

---

## 6. CoT 的常见坑

### 6.1 CoT 也可能错
模型胡乱推理一通然后给个错答案——你以为有 CoT 就稳了，其实没有。

对策：CoT + self-consistency + 人工抽检。

### 6.2 CoT 在简单任务上反而变差
模型"用力过猛"，把简单事想复杂了。研究称为"overthinking"。

```
问题：1 + 1 = ?
不带 CoT: 2
带 CoT: "首先我需要理解什么是 1 + 1...在十进制中...也许在二进制下...答案可能是 2 也可能是 10..."
```

对策：仅复杂任务用 CoT；简单任务别加。

### 6.3 CoT 让推理"显式化"反而被劫持
prompt injection 看到 `<thinking>` 标签可以注入"忽略指令"。

对策：thinking 块视为不可信，结构化输出 + tool call 强约束最终行为。

### 6.4 模型内置 thinking 不要再加 "step by step"
Claude extended thinking 已经会推理——再叠加 prompt 里的 "let's think step by step"，反而干扰内置 thinking 行为。

---

## 7. 实战 demo

```python
# demos/techniques/02_cot_compare.py
"""对比 zero-shot / simple CoT / structured CoT / extended thinking"""
import re
import anthropic

client = anthropic.Anthropic()
PROBLEM = "一个班 30 人，男生比女生多 4 个，男女各几个？只返回最终数字 'M=X Y=Y' 不要别的。"

def zero_shot():
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=50,
        messages=[{"role": "user", "content": PROBLEM}],
    )
    return resp.content[0].text

def simple_cot():
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        messages=[{"role": "user", "content": PROBLEM + "\n\n请一步步思考。"}],
    )
    return resp.content[0].text

def structured_cot():
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=500,
        system="""回答数学题时，先在 <thinking> 里推理，然后在 <answer> 里只放最终答案。""",
        messages=[{"role": "user", "content": PROBLEM}],
    )
    return resp.content[0].text

def extended_thinking():
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        thinking={"type": "enabled", "budget_tokens": 2000},
        max_tokens=1000,
        messages=[{"role": "user", "content": PROBLEM}],
    )
    for block in resp.content:
        if block.type == "text":
            return block.text
    return ""


for name, fn in [("zero-shot", zero_shot), ("simple-cot", simple_cot),
                  ("structured-cot", structured_cot), ("extended-thinking", extended_thinking)]:
    print(f"\n=== {name} ===")
    print(fn())
```

观察：复杂数学题上后三个明显更稳。

---

## 8. 三家对比

| 维度 | Anthropic | OpenAI | Gemini |
|------|-----------|--------|--------|
| 简单 CoT | "请一步步推理" | 同 | 同 |
| 结构化 CoT | 推荐 `<thinking>` `<answer>` XML | 推荐 markdown 标题分段 | 同 OpenAI |
| 内置 thinking | extended thinking (Sonnet 4+) | reasoning effort (GPT-5+) | thinking_config (2.5+) |
| Thinking 计费 | 计 token；缓存折扣 | 推理 token 单独计费 | 计 token |

---

## 9. 常见坑

| 坑 | 排查 |
|----|------|
| **简单任务也加 CoT** | 浪费 + overthinking |
| **不程序化提取 answer** | thinking 当 answer 用，下游解析挂 |
| **CoT 没用 self-consistency** | 单次 CoT 也会错 |
| **`<thinking>` 内容被泄漏到用户** | UI 层过滤 |
| **同时用 extended thinking + prompt CoT** | 冲突，效果反而差 |
| **CoT 长度爆炸超 token 限** | thinking 也吃 max_tokens |

---

## 10. 下一步

- 📖 角色 / 边界 → [03-role-prompting.md](./03-role-prompting.md)
- 📖 任务拆解（CoT 的"工程化"版本） → [04-decomposition.md](./04-decomposition.md)
- 📖 self-consistency → [09-self-consistency.md](./09-self-consistency.md)
- 📖 Tool use（Program of Thoughts 的扩展） → [04-advanced/02-tool-use.md](../04-advanced/02-tool-use.md)

## 参考资料

- "Chain-of-Thought Prompting Elicits Reasoning" (Wei et al. 2022): https://arxiv.org/abs/2201.11903
- Claude Extended Thinking: https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking
- OpenAI Reasoning Models: https://platform.openai.com/docs/guides/reasoning
- "Program of Thoughts" (Chen et al. 2022): https://arxiv.org/abs/2211.12588
