# PE Technique 01：Zero-shot 与 Few-shot，什么时候需要示例

> Few-shot 常被当成默认配置，但它要花 token，还会让模型照着示例的风格写。我的做法是先跑 zero-shot，效果不够再加示例，加也只加几个精挑的。这篇讲哪些情况必须给示例、示例怎么挑、给几个合适。

---

## 1. 概念

- Zero-shot：只给任务描述和输入，模型直接做。
  ```
  把以下英文翻译成中文：{text}
  ```
- Few-shot：在任务描述里放 1 到 N 个「输入 → 输出」示例，给模型一个参照。
  ```
  把以下英文翻译成中文：
  
  Hello world → 你好世界
  Good morning → 早上好
  
  现在翻译：{text}
  ```

2020 年 GPT-3 那一代模型很依赖示例，few-shot 也是那时流行起来的。现在的大模型（Claude 5.x / GPT-6 / Gemini 3+）zero-shot 已经很强，多数任务不给示例也够用。

---

## 2. 什么时候 zero-shot 就够

| 场景 | 原因 |
|------|------|
| 模型训练时大量见过的任务（翻译、总结、问答） | 先验足够强 |
| 输出格式简单（一个短句、一个标签） | 不需要参照 |
| 输入输出风格自由，不要求特定语气 | 没必要硬性限定 |
| 类别名一看就懂（bug / praise / question） | 类别名本身就是解释 |

先用 zero-shot 跑出 v0 的基线，再决定要不要加示例（见 [从需求到 Prompt v0](../02-process/02-from-spec-to-v0.md)）。

---

## 3. 什么时候必须加 few-shot

| 场景 | 为什么 |
|------|--------|
| 输出格式特殊、不通用 | 文字描述说不清，不如给个例子 |
| 模型对任务先验弱 | 冷门领域、内部黑话 |
| 风格要稳定 | 比如所有标题都要用「主语-动词-数字」句式 |
| 边界判断微妙 | 反讽分类、主观评分标准 |
| 多个输出字段互相关联 | 让模型看到字段之间怎么配合 |

下面这种既有嵌套结构又有风格要求的任务，光靠描述很难讲清：

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

要是用文字逐条解释 title 怎么写得简洁、severity 怎么判、user_frustration 看什么语气，写一大段也未必说清，一个示例就够了。

---

## 4. 示例数量：少即是多

| 数量 | 何时合适 |
|------|----------|
| 0 | 大多数现代任务 |
| 1-2 | 输出格式稍特殊，或需要定风格 |
| 3-5 | 复杂判断、类别多、边界微妙 |
| 5-10 | 很复杂，通常说明任务该拆了 |
| 10+ | 改用 RAG 或微调，别往 prompt 里硬塞 |

示例加到几个之后，收益通常很快饱和，token 成本却随示例数线性增长。Min et al. 2022 还发现，示例里标签的对错影响比想象的小，示例的格式和输入分布更关键，这也说明挑对示例比堆数量重要。

---

## 5. 怎么挑示例

### 5.1 覆盖性 vs 代表性

两种思路：代表性是让示例和真实数据分布一致，大部分是 happy path；覆盖性是每种边界、每个类别都给一个。我推荐以代表性为主，再补 1 到 2 个关键的边界样例：

```
- 3 个最常见 happy path 示例
- 1 个关键 edge case 示例（最容易错的那种）
```

### 5.2 避免常见错误

| 错误 | 后果 |
|------|------|
| 示例和任务对应不上 | 模型困惑，效果反而变差 |
| 示例输出风格不一致 | 模型不知道该学哪个 |
| 示例和真实输入分布差太远 | 评测集上好看，上线就垮 |
| 示例都来自同一个类别 | 模型偏向那个类 |
| 示例输入很短，真实输入很长 | 风格迁移不过去 |

### 5.3 示例放在哪

一般这样排：

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

示例放在 system 末尾，用 `---` 或 XML 标签和其他内容隔开。Anthropic 推荐用 XML：

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
    model="claude-haiku-5-5",
    max_tokens=1024,
    system=SYSTEM,
    messages=[{"role": "user", "content": "客服真差"}],
)
```

### OpenAI

在 Chat Completions 里，常见写法是把 few-shot 写成 messages 数组里的多轮对话：

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
    model="gpt-6-luna",
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

# 示例直接写进 system_instruction
SYSTEM = """把反馈分类。类别: bug, feature, complaint, praise.

示例：
- App 闪退 → bug
- 希望加深色模式 → feature
- 用得真顺，再也不用了 → complaint

返回类别名一个单词。
"""

resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="客服真差",
    config={"system_instruction": SYSTEM},
)
```

---

## 7. Few-shot 的副作用

### 7.1 风格过拟合
示例用了某种句式，模型就会在所有输出里都用这种句式。

```
❌ 示例输出都是"建议 + ?"
   "建议加深色模式？"
   "建议提升 App 稳定性？"
   →  模型对所有反馈都生成疑问句，即使不该
```

对策是让示例风格多样一些，故意混入不同写法。

### 7.2 长度过拟合
所有示例输出都是 30 字左右，模型九成的输出也会是 30 字。对策同样是让示例长短不一。

### 7.3 字段顺序过拟合
示例里的 JSON 都按 `{a, b, c}` 排，模型几乎不会输出 `{b, a, c}`。业务允许的话，可以故意打乱示例里的字段顺序。

---

## 8. Dynamic Few-shot（Few-shot RAG）

示例库有几百条时，可以按输入检索，动态挑最相关的几条：

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

适合这几种情况：任务复杂，一组固定示例覆盖不全；不同分类或业务线需要不同的 prompt；手上已经有人工标注库。

代价是和 prompt caching 冲突：示例每次都变，缓存就命中不了。

---

## 9. 何时不要用 few-shot

| 场景 | 不该用 few-shot 的原因 |
|------|----------------------|
| Zero-shot 通过率已经 ≥ 90% | 加示例只增加成本 |
| 已经用了 structured output API | API 直接约束输出 schema，比示例可靠 |
| 示例是敏感数据 | 别把客户真实数据放进 prompt |
| 每次输出本来就该不一样 | 示例反而限制多样性 |
| 推理任务 | CoT 比 few-shot 有效（见 [Chain-of-Thought（CoT）](./02-cot.md)） |

---

## 10. demo：对比 zero-shot vs few-shot

```python
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
    ("我再也不会推荐给朋友", "complaint"),    # 没有明说哪里不好，隐含抱怨
    ("产品挺棒，但是 logo 太丑了改一下吧", "feature"),  # 多类
]

def run(prompt: str, name: str):
    print(f"\n=== {name} ===")
    right = 0
    for text, expected in TEST_CASES:
        resp = client.messages.create(
            model="claude-haiku-5-5",
            max_tokens=1024,
            system=prompt,
            messages=[{"role": "user", "content": text}],
        )
        try:
            actual = json.loads(resp.content[-1].text)["category"]
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

预期在隐含抱怨和多类样本上，few-shot 会明显好于 zero-shot。

---

## 11. 常见坑

| 坑 | 排查 |
|----|------|
| 一上来就放 5 个示例 | 先从 zero-shot 起步 |
| 示例风格不一致 | 模型不知道学哪个 |
| 某一类示例过多 | 输出偏向那一类；比例大致贴近真实分布即可，别让一类远超它的真实占比 |
| 示例数据没脱敏 | 有合规风险 |
| 改示例时顺手改了任务定义 | 一次改了两个变量，前后结果没法比 |
| 示例和真实分布不一致 | 离线表现好，上线就垮 |

---

## 12. 下一步

- [Chain-of-Thought（CoT）](./02-cot.md)
- [结构化输出（JSON / XML / Schema）](./05-structured-output.md)：用 schema 约束格式，很多时候比 few-shot 描述格式更可靠
- [Few-shot 示例怎么设计](./06-examples-design.md)

## 参考资料

- "Rethinking the Role of Demonstrations" (Min et al. 2022): https://arxiv.org/abs/2202.12837
- Anthropic Use Examples: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multishot-prompting
- OpenAI few-shot best practice: https://platform.openai.com/docs/guides/prompt-engineering
