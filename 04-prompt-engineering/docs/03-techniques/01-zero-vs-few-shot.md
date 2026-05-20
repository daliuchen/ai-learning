# PE Technique 01：Zero-shot vs Few-shot —— 什么时候真的需要示例

> **一句话**：Few-shot 不是"必须用的标配"——它有 token 成本、有"过拟合到示例风格"的副作用。**先 zero-shot，跑不动再加 few-shot；加 few-shot 也要少而精**。本篇讲什么时候必须用、怎么挑示例、几个示例够用。

---

## 1. 概念

- **Zero-shot**：只给任务描述 + 输入，模型直接做。
  ```
  把以下英文翻译成中文：{text}
  ```
- **Few-shot**：在任务描述里塞 1-N 个"输入 → 输出"示例，让模型 anchor。
  ```
  把以下英文翻译成中文：
  
  Hello world → 你好世界
  Good morning → 早上好
  
  现在翻译：{text}
  ```

模型 2018 年还需要大量 few-shot；现代大模型（Claude 4.x / GPT-5 / Gemini 2.0）的 zero-shot 能力已经非常强，多数情况 zero-shot 够用。

---

## 2. 什么时候 zero-shot 就够

| 场景 | 原因 |
|------|------|
| 任务模型训练数据见过的（翻译、总结、问答） | 先验强 |
| 输出格式简单（一句话、一个标签） | 不需要 anchor |
| 输入输出**风格自由**（不强求特定语气） | 没必要硬限定 |
| 类别 enum 一目了然（bug / praise / question） | 类别名自解释 |

**铁律**：先用 zero-shot 跑 v0 看基线（详 [02-process/02](../02-process/02-from-spec-to-v0.md)）。

---

## 3. 什么时候**必须**加 few-shot

| 场景 | 为什么 |
|------|--------|
| 输出格式特殊 / 非通用 | 用文字描述说不清，给个例子 |
| 任务模型先验弱 | 罕见 niche / 内部 jargon |
| 风格要稳定 | 比如所有标题都要"主语-动词-数字"句式 |
| 边界判断微妙 | 比如反讽分类 / 主观评分标准 |
| 多输出维度有关联 | 让模型看到字段间的协调 |

举个例子，下面这种"嵌套 + 风格"任务，纯描述很难讲清：

```
任务：把用户反馈转成结构化 issue。

期望输出（看个示例就懂）：

输入: "App 一打开就闪退，每次都这样，我都用了三个月了"
输出:
{
  "title": "App 打开后立即闪退",
  "severity": "critical",
  "duration": "持续 3 个月",
  "user_frustration": "high",
  "reproducible": "always"
}
```

用文字解释 "title 要言简意赅、severity 怎么判、user_frustration 看语气..." 很啰嗦——给一个示例胜过 100 字。

---

## 4. 示例数量：少即是多

| 数量 | 何时合适 |
|------|----------|
| 0 | 大多数现代任务 |
| 1-2 | 输出格式稍特殊；风格 anchor |
| 3-5 | 复杂判断 / 多 enum / 边界微妙 |
| 5-10 | 极复杂；通常意味着任务该拆分 |
| 10+ | 改用 RAG / 微调，别 stuff prompt |

**研究结论**（Min et al. 2022）：3 个示例 vs 10 个示例，准确率差异通常 < 2%——但 token 成本 3 倍。

---

## 5. 怎么挑示例

### 5.1 覆盖性 vs 代表性

- **代表性**（推荐）：示例和真实数据分布一致——大部分是 happy path
- **覆盖性**：每种边界 / 类别都给一个示例

实战推荐 **代表性 + 1-2 个关键 edge**：

```
- 3 个最常见 happy path 示例
- 1 个关键 edge case 示例（最容易错的那种）
```

### 5.2 避免常见错误

| 错误 | 后果 |
|------|------|
| 示例和任务对应不上 | 模型困惑、效果反而差 |
| 示例输出风格不一致 | 模型选哪个学？ |
| 示例和真实输入分布差远 | 在 evalset 上看着好，上线垮 |
| 示例都来自一个类别 | 模型偏 anchor 到那个类 |
| 示例输入太短，真实输入很长 | 风格不可迁移 |

### 5.3 示例的"位置"

示例放哪里：

```
[System / Instruction]
你是 ...
任务 ...
约束 ...

示例：

输入：A
输出：a

输入：B
输出：b

---

[User]
实际输入
```

把示例放 system 末尾、用 `---` 或 XML 标签明确隔开。Anthropic 推荐 XML：

```python
SYSTEM = """你是分类师。

任务：把反馈分到 bug/feature/complaint/praise。

<examples>
<example>
<input>App 闪退</input>
<output>{"category": "bug"}</output>
</example>
<example>
<input>希望加深色模式</input>
<output>{"category": "feature"}</output>
</example>
</examples>
"""
```

---

## 6. 三家代码

### Anthropic

```python
import anthropic
client = anthropic.Anthropic()

SYSTEM = """把反馈分类。类别: bug, feature, complaint, praise.

<examples>
<example>
<input>App 闪退</input>
<output>bug</output>
</example>
<example>
<input>希望加深色模式</input>
<output>feature</output>
</example>
<example>
<input>用得真顺，再也不用了</input>
<output>complaint</output>
</example>
</examples>

返回类别名一个单词。
"""

resp = client.messages.create(
    model="claude-haiku-4-5-20251001",
    max_tokens=20,
    system=SYSTEM,
    messages=[{"role": "user", "content": "客服真差"}],
)
```

### OpenAI

OpenAI 推荐用 **messages 数组**塞 few-shot（多轮对话语义最强）：

```python
from openai import OpenAI
client = OpenAI()

EXAMPLES = [
    {"role": "user", "content": "App 闪退"},
    {"role": "assistant", "content": "bug"},
    {"role": "user", "content": "希望加深色模式"},
    {"role": "assistant", "content": "feature"},
    {"role": "user", "content": "用得真顺，再也不用了"},
    {"role": "assistant", "content": "complaint"},
]

resp = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": "把反馈分类。类别: bug, feature, complaint, praise. 返回类别名一个单词。"},
        *EXAMPLES,
        {"role": "user", "content": "客服真差"},
    ],
)
```

### Gemini

```python
from google import genai
client = genai.Client()

# Gemini 用 "Few-shot in single instruction"
SYSTEM = """把反馈分类。类别: bug, feature, complaint, praise.

示例：
- App 闪退 → bug
- 希望加深色模式 → feature
- 用得真顺，再也不用了 → complaint

返回类别名一个单词。
"""

resp = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="客服真差",
    config={"system_instruction": SYSTEM},
)
```

---

## 7. Few-shot 的副作用

### 7.1 风格过拟合
示例用了某种句式 → 模型对**所有**输出都用这种句式。

```
❌ 示例输出都是"建议 + ?"
   "建议加深色模式？"
   "建议提升 App 稳定性？"
   →  模型对所有反馈都生成疑问句，即使不该
```

对策：示例风格要多样，故意混入不同风格防 anchor。

### 7.2 长度过拟合
所有示例输出都是 30 字 → 模型 90% 输出都是 30 字。
对策：示例长度多样化。

### 7.3 字段顺序过拟合
JSON 字段顺序：示例都按 `{a, b, c}` 顺序 → 模型几乎不会出 `{b, a, c}`。
对策：故意打乱示例字段顺序（如果业务允许）。

---

## 8. Dynamic Few-shot（Few-shot RAG）

任务示例库巨大（几百条）时，用检索动态挑相关示例：

```python
def dynamic_few_shot(query: str, top_k: int = 3) -> list[dict]:
    """从示例库检索最相关的 k 个"""
    similar = vector_store.search(query, top_k=top_k)
    return [{"input": ex["input"], "output": ex["output"]} for ex in similar]


def classify_with_dynamic_examples(user_input: str):
    examples = dynamic_few_shot(user_input, top_k=3)
    system = format_with_examples(BASE_SYSTEM, examples)
    return call_llm(system, user_input)
```

适合：
- 任务复杂、单一组示例覆盖不全
- 各分类 / 各业务线 prompt 不同
- 有现成的人工标注库

注意：和"prompt caching"有冲突——动态示例每次变，缓存失效。

---

## 9. 何时不要用 few-shot

| 场景 | 不该用 few-shot 的原因 |
|------|----------------------|
| Zero-shot 已 ≥ 90% 通过 | 加示例只增成本 |
| 用了 structured output API | API 强约束输出 schema，比示例更可靠 |
| 示例数据是敏感数据 | 不要把客户真实数据放 prompt |
| 任务输出每次都不一样 | 示例 anchor 反而限制多样性 |
| 推理任务 | CoT 比 few-shot 更有效（详 [02-cot.md](./02-cot.md)） |

---

## 10. demo：对比 zero-shot vs few-shot

```python
# demos/techniques/01_zero_vs_few_shot.py
import json
import anthropic

client = anthropic.Anthropic()

ZERO_SHOT = """把反馈分类: bug, feature, complaint, praise. 返回 JSON {category}."""

FEW_SHOT = ZERO_SHOT + """

示例：
输入: "App 闪退"          → {"category": "bug"}
输入: "请加深色模式"       → {"category": "feature"}
输入: "用得真顺，再也不用了" → {"category": "complaint"}
"""

TEST_CASES = [
    ("客服态度真好", "praise"),
    ("点击搜索就崩溃", "bug"),
    ("能加暗色主题吗", "feature"),
    ("我再也不会推荐给朋友", "complaint"),    # 反讽难判
    ("产品挺棒，但是 logo 太丑了改一下吧", "feature"),  # 多类
]

def run(prompt: str, name: str):
    print(f"\n=== {name} ===")
    right = 0
    for text, expected in TEST_CASES:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            temperature=0,
            system=prompt,
            messages=[{"role": "user", "content": text}],
        )
        try:
            actual = json.loads(resp.content[0].text)["category"]
        except Exception:
            actual = "_parse_error"
        ok = actual == expected
        right += ok
        print(f"  {'✅' if ok else '❌'} {expected:10s} → {actual:10s} | {text}")
    print(f"  通过: {right}/{len(TEST_CASES)}")


if __name__ == "__main__":
    run(ZERO_SHOT, "zero-shot")
    run(FEW_SHOT, "few-shot")
```

预期：在反讽 / 多类样本上 few-shot 优势明显。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| **一上来 5 个 few-shot** | 先 zero-shot 起步 |
| **示例风格不一致** | 模型不知道学哪个 |
| **示例覆盖某类过多** | anchor 偏向；保持均衡 |
| **示例数据敏感不脱敏** | 数据合规风险 |
| **改示例顺便改任务定义** | 评测变量改了，前后不可比 |
| **示例和真实分布不一致** | 离线表现好，上线垮 |

---

## 12. 下一步

- 📖 CoT 思维链 → [02-cot.md](./02-cot.md)
- 📖 结构化输出（替代 few-shot 描述格式） → [05-structured-output.md](./05-structured-output.md)
- 📖 好 few-shot 设计 → [06-examples-design.md](./06-examples-design.md)

## 参考资料

- "Rethinking the Role of Demonstrations" (Min et al. 2022): https://arxiv.org/abs/2202.12837
- Anthropic Use Examples: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting
- OpenAI few-shot best practice: https://platform.openai.com/docs/guides/prompt-engineering
