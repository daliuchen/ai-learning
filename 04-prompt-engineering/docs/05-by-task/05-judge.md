# PE By-Task 05：LLM-as-Judge —— 让 LLM 当评测员

> **一句话**：LLM-as-judge 是评测 LLM 输出**主观质量**的核心工具——但有陷阱（自我偏好、位置偏差、过度宽容）。本篇讲怎么设计可靠的 judge prompt + 校准方法。

---

## 1. Judge 的常见任务

| 类型 | 例子 |
|------|------|
| **打分** | 给 1-5 分 |
| **二选一** | A 和 B 哪个更好 |
| **维度评估** | 准确 / 简洁 / 风格 / 安全 各打分 |
| **是否合格** | 是 / 否 + 原因 |
| **找问题** | 列出输出的问题清单 |

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

## 3. 避免自我偏好（self-preference bias）

研究发现：**模型偏爱自己生成的内容**。用 GPT-4o 当 judge 评 GPT-4o 输出，分数会比让 Claude 评偏高。

对策：
- judge 模型**不同家**于 generator
- 多 judge 投票（GPT + Claude + Gemini 三家平均）
- 公开 evalset（去掉模型 fingerprint）

---

## 4. 避免位置偏差（position bias）

二选一时，模型倾向选**先出现**的那个：

```
A: ...
B: ...
哪个更好？

→ 模型 60% 选 A，30% 选 B，10% 平
```

对策：
- 跑两次：A/B 顺序 + B/A 顺序——平均
- 让 judge 明确"位置不应影响判断"
- 用 randomization

```python
def fair_pairwise_judge(a, b):
    s1 = judge_compare(a, b)   # A 在前
    s2 = judge_compare(b, a)   # B 在前
    # 一致才信
    return s1 if s1 == s2 else "tie"
```

---

## 5. 避免过度宽容（over-leniency）

LLM 默认"礼貌"——容易给所有输出 4-5 分。

对策：
- prompt 强制"3 分是中位数，4-5 留给优秀"
- 显式比较 vs 优秀基准
- 加 calibration examples（给几个 "1 分什么样、5 分什么样"）

---

## 6. Calibration with examples

让 judge 看 anchor 例子学打分尺度：

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

加 anchor → 分数分布拉开，区分度变好。

---

## 7. Judge 必须 temperature=0

**铁律**。否则同一输入两次评分不同，评测无意义。

```python
# ❌ 错
judge_resp = client.messages.create(temperature=0.7, ...)

# ✅ 对
judge_resp = client.messages.create(temperature=0, ...)
```

---

## 8. Pairwise vs 直接打分

| 方式 | 优点 | 缺点 |
|------|------|------|
| **直接打分**（1-5） | 简单 / 单次 | 模型打分不准 / 漂移 |
| **Pairwise**（A vs B） | 相对准、稳定 | 2 次调用 / 位置偏差 |
| **Ranking** (rank N) | 一次比多个 | N 个候选时 cost 高 |

实战：**Pairwise** 适合迭代时"v_n 比 v_{n-1} 好吗"；**打分** 适合绝对质量监控。

---

## 9. Critic-Refine pipeline

judge 不只是评——也指导改进：

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

形成生成 → 评 → 改 → 评的闭环。

---

## 10. 校准 judge：和人工 ground truth 比

每周抽 20-50 条做"人工 + judge 双打"：

```python
# 人工标 ground truth: pass/fail
# Judge 标: pass/fail

agreement_rate = sum(human[i] == judge[i] for i in ...) / N
```

- agreement > 85% → judge 可信
- agreement < 70% → judge prompt 要改 / 换更强模型

---

## 11. 完整 demo

```python
# demos/by_task/05_judge.py
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
    resp = client.beta.chat.completions.parse(
        model="gpt-4o",   # judge 用更强模型
        temperature=0,
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
| **judge 同 generator 同模型** | 自我偏好；换家 |
| **judge temperature ≠ 0** | 不可重复 |
| **判断没维度分开** | 单总分掩盖问题 |
| **没 calibration anchor** | 分数都 4-5，区分度差 |
| **不做人工校准** | judge 不准你不知道 |
| **位置偏差** | pairwise 要交换顺序双跑 |
| **判官 prompt 模糊** | 给具体维度 + 评分标准 |

---

## 13. 05-by-task 章总结

| 篇 | 主题 |
|---|------|
| 01 | 分类器 |
| 02 | 抽取器 |
| 03 | 生成器 |
| 04 | 总结器 |
| 05 | 判官（本篇） |

---

## 14. 下一步

- 📖 模型差异 → [06-models/](../06-models/)
- 📖 生产化 → [07-production/](../07-production/)
- 📖 实战 → [08-practice/01-build-classifier.md](../08-practice/01-build-classifier.md)

## 参考资料

- "Judging LLM-as-a-Judge" (Zheng et al. 2023): https://arxiv.org/abs/2306.05685
- LangSmith Evaluators: https://docs.smith.langchain.com/evaluation
- Promptfoo Model-Graded Eval: https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/
