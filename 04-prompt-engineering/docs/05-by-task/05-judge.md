# PE By-Task 05：LLM-as-Judge

> 输出的主观质量（写得好不好、清不清楚）没法靠字符串比对来评，通常要让另一个 LLM 来打分。这个办法好用，但有几个固定的偏差：偏爱自己写的内容、偏爱排在前面的选项、给分普遍偏松。这篇讲怎么写一个可靠的 judge prompt，以及怎么校准它。

---

## 1. Judge 的常见任务

| 类型 | 例子 |
|------|------|
| 打分 | 给 1-5 分 |
| 二选一 | A 和 B 哪个更好 |
| 分维度评估 | 准确 / 简洁 / 风格 / 安全 各打分 |
| 是否合格 | 是 / 否 + 原因 |
| 找问题 | 列出输出的问题清单 |

---

## 2. 通用 judge prompt 模板

```python
JUDGE_SYSTEM = """你是严格的<领域>评审专家。

任务：评估给定输出的质量。

评估维度（1-5 分，5 最好）：
1. accuracy: 内容是否忠实于输入 / 事实
2. completeness: 是否覆盖关键信息
3. clarity: 是否清晰易懂
4. <task-specific>: <...>

打分原则：
- 严格按上述维度独立评分
- 不要因为输出"看起来不错"就高分
- 4-5 分应保留给"明显优秀"，普通输出 3 分
- 给出具体改进建议

输出 JSON:
{
  "scores": {"accuracy": 1-5, "completeness": 1-5, "clarity": 1-5, ...},
  "overall": 1-5,
  "key_issues": ["...", "..."],
  "reasoning": "<不超过 100 字>"
}
"""
```

---

## 3. 自我偏好（self-preference bias）

研究发现，模型会偏爱自己生成的内容。用 GPT 当 judge 去评 GPT 的输出，分数会比让 Claude 来评更高。

应对办法：

- judge 和 generator 用不同厂商的模型
- 多个 judge 投票，比如 GPT、Claude、Gemini 三家取平均
- 评测集去掉能看出是哪个模型写的痕迹

---

## 4. 位置偏差（position bias）

二选一时，模型倾向于选先出现的那个：

```
A: ...
B: ...
哪个更好？

→ 两份质量相当时，模型选 A 的次数明显多于 B（示意）
```

应对办法：按 A/B 和 B/A 两种顺序各跑一次；在 prompt 里写明"顺序不应影响判断"；或者随机打乱顺序。

```python
def fair_pairwise_judge(a, b):
    # judge_compare 返回胜出的那份内容本身（不是 "first"/"second" 这样的位置）
    s1 = judge_compare(a, b)   # A 在前
    s2 = judge_compare(b, a)   # B 在前
    # 一致才信
    return s1 if s1 == s2 else "tie"
```

---

## 5. 给分偏松（over-leniency）

LLM 默认很客气，什么输出都容易给 4-5 分。

应对办法：在 prompt 里规定"3 分是中位数，4-5 分只给优秀的"；让它对照一个优秀基准来比；再加几个校准样例，告诉它 1 分和 5 分分别长什么样。

---

## 6. 用样例校准打分尺度

给 judge 看几个锚点样例，让它学会这把尺子：

```
打分参考（1-5 分）:

5 分例子：
输入: ...
输出: ...
为什么: 完美覆盖、零事实错、风格地道

3 分例子（typical）：
输入: ...
输出: ...
为什么: 主要内容对，但漏 1 个 key point

1 分例子：
输入: ...
输出: ...
为什么: 含明显事实错 / 完全偏题

现在评估下面的输出：
```

加了锚点之后，分数分布会明显拉开，区分度变好。

---

## 7. Judge 的输出要尽量稳定

同一个输入两次打分不一样，这个评测就没有意义。

- 能设采样参数的模型（Qwen / 多数开源模型）：`temperature=0`
- Claude 新模型（4.7+）已不接受 `temperature`，Gemini 3+ 建议保持默认值。这时改用下面几种办法压低波动：
  1. 结构化输出（固定 JSON schema，每个维度用枚举或整数）
  2. 把评分细则写死（见上一节的锚点样例），3 分是普通合格
  3. 同一输入评 3 次，取中位数或多数票，并把"重复评分一致率"当作 judge 自身的质量指标
  4. 固定 judge 模型的具体版本，升级模型就重跑一遍 judge 校准集

```python
# ❌ 给 Claude 新模型传采样参数 → 400
# judge_resp = client.messages.create(temperature=0, ...)

# ✅ 结构化输出 + 多次评分取中位数
scores = [judge_once(sample) for _ in range(3)]
```

---

## 8. Pairwise vs 直接打分

| 方式 | 优点 | 缺点 |
|------|------|------|
| 直接打分（1-5） | 简单，单次调用 | 绝对分数不准，容易漂移 |
| Pairwise（A vs B） | 相对判断更准、更稳 | 要调用 2 次，有位置偏差 |
| Ranking（rank N） | 一次比多个 | 候选多时成本高 |

我的用法是：迭代时判断"v_n 是否比 v_{n-1} 好"用 pairwise，做线上的绝对质量监控用直接打分。

---

## 9. Critic-Refine 流水线

judge 除了打分，还能给改进方向：

```python
def judge_then_refine(input, output, max_iter=3):
    for _ in range(max_iter):
        judgement = judge(input, output)
        if judgement["overall"] >= 4:
            return output
        # 让 generator 根据 critique 改
        output = refine(input, output, judgement["key_issues"])
    return output
```

这样就形成了生成、评审、修改、再评审的循环。

---

## 10. 用人工标注校准 judge

每周抽 20-50 条，人工和 judge 各标一遍：

```python
# 人工标 ground truth: pass/fail
# Judge 标: pass/fail

agreement_rate = sum(human[i] == judge[i] for i in ...) / N
```

- 一致率 > 85%：judge 可以信
- 一致率 < 70%：改 judge prompt，或者换更强的模型

---

## 11. 完整 demo

```python
import json
from pydantic import BaseModel, Field
from openai import OpenAI

client = OpenAI()


class JudgeResult(BaseModel):
    accuracy: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    overall: int = Field(ge=1, le=5)
    key_issues: list[str]
    reasoning: str


JUDGE_SYS = """你是严格的总结评审专家。

评估维度（1-5 分）：
- accuracy: 是否事实准确（无幻觉、数字对）
- completeness: 是否覆盖关键信息
- clarity: 是否清晰易懂、无废话

打分原则：
- 3 分是"普通合格"，4-5 留给"明显优秀"
- 数字错 / 关键信息漏 = 直接 ≤ 2
- key_issues 必须具体（引用片段）

输出 JSON。
"""


def judge_summary(original: str, summary: str) -> JudgeResult:
    resp = client.chat.completions.parse(
        model="gpt-6.1-terra",   # judge 用更强模型
        temperature=0,   # GPT-6 是否接受自定义 temperature 以模型页为准；不接受就删掉这行
        response_format=JudgeResult,
        messages=[
            {"role": "system", "content": JUDGE_SYS},
            {"role": "user", "content": f"<original>\n{original}\n</original>\n\n<summary>\n{summary}\n</summary>"},
        ],
    )
    return resp.choices[0].message.parsed


if __name__ == "__main__":
    ORIG = "公司 Q1 营收 12 亿，同比 +18%。SaaS 占 65%。"
    
    # 不同 summary 评估
    SUMMARIES = [
        "Q1 营收 12 亿，SaaS 占 65%。",                  # 合格
        "公司 Q1 增长强劲。",                            # 太空
        "Q1 营收 15 亿，同比 +25%。",                    # 数字错（幻觉）
    ]
    
    for s in SUMMARIES:
        r = judge_summary(ORIG, s)
        print(f"\nSummary: {s}")
        print(f"  overall={r.overall}, acc={r.accuracy}, comp={r.completeness}, clar={r.clarity}")
        print(f"  issues: {r.key_issues}")
```

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| judge 和 generator 是同一个模型 | 有自我偏好，换一家 |
| judge 输出不稳定 | 能设就 temperature=0；Claude 新模型用结构化输出 + 多次评分取中位数 |
| 不分维度，只给总分 | 一个总分会掩盖具体问题 |
| 没有校准锚点 | 分数全挤在 4-5，没区分度 |
| 不做人工校准 | judge 不准你也不会知道 |
| 位置偏差 | pairwise 要交换顺序各跑一次 |
| judge prompt 模糊 | 写清具体维度和评分标准 |

---

## 13. 本章回顾

| 篇 | 主题 |
|---|------|
| 01 | 分类器 |
| 02 | 抽取器 |
| 03 | 生成器 |
| 04 | 总结器 |
| 05 | 判官（本篇） |

---

## 14. 下一步

- 模型差异：[06-models/](../06-models/)
- 生产化：[07-production/](../07-production/)
- 实战：[从需求到一个分类器（完整闭环）](../08-practice/01-build-classifier.md)

## 参考资料

- "Judging LLM-as-a-Judge" (Zheng et al. 2023): https://arxiv.org/abs/2306.05685
- LangSmith Evaluators: https://docs.smith.langchain.com/evaluation
- Promptfoo Model-Graded Eval: https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/
