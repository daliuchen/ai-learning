# CE 08-04：如何评测上下文质量

> 评估上下文工程，不能只看最终答案对不对，还要看为了答对用了多少上下文。两个系统都答对了，一个塞了 20K token，一个只用 3K，后者才是更好的工程。所以这里的评测有两个维度：答案质量和上下文效率。往下细分，要量化检索进来的内容有多少有用（precision/recall），用消融实验确认每块上下文都有贡献，再用 LLM-as-judge 检查答案是不是基于给定的上下文，而不是模型自己发挥的。

---

## 1. 评什么：质量 + 效率两个轴

| 轴 | 指标 | 回答什么 |
|----|------|----------|
| 答案质量 | 正确性、grounding（有据）、完整性 | 任务做没做对 |
| 上下文效率 | token/任务、有效信息占比、检索 precision | 做对这件事花了多少上下文预算 |

只看质量，会鼓励大家拼命往窗口里塞东西。塞得多确实更容易蒙对，但贵、慢，还有 context rot 的风险。把效率一起纳入评测，才能把系统推向「最少必要上下文」（见[最少必要上下文原则](../01-foundations/06-minimal-context.md)）。实际用的时候可以这样综合看：答案质量不下降的前提下，token/任务越低越好。

---

## 2. Context Precision / Recall：进上下文的内容有多少有用

这里借用 RAG 评测的两个主要指标，但关注的是上下文装载这一环：

- Context Recall（召回）：回答这个问题所需的事实，有多少进了上下文？低了就是漏召回，有幻觉风险。
- Context Precision（精度）：进了上下文的内容里，有多少是相关的？低了就是噪声多，带来 context rot、稀释和 token 浪费。

两者此消彼长：一味加大检索 k 会提高 recall，同时压低 precision。评测要找的是平衡点，在 recall 够用的前提下，precision 尽量高。

```python
from dataclasses import dataclass

@dataclass
class ContextEval:
    needed_facts: set[str]      # 标注：回答本题必需的事实 id
    retrieved_chunks: list[str] # 实际进上下文的片段
    relevant_chunks: set[int]   # 标注/judge：哪些 chunk 真相关

    def recall(self) -> float:
        covered = {f for f in self.needed_facts
                   if any(f in c for c in self.retrieved_chunks)}
        return len(covered) / max(len(self.needed_facts), 1)

    def precision(self) -> float:
        return len(self.relevant_chunks) / max(len(self.retrieved_chunks), 1)
```

哪些 chunk 相关，可以人工标注，也可以用 LLM-as-judge 自动判断（见第 4 节）。要大规模跑，只能靠后者。

---

## 3. 消融实验：去掉某块上下文看效果

在上下文评测里，消融实验投入小、收获大。每次拿掉上下文的一个部分，看答案质量掉了多少。掉得多，说明这块在起作用；几乎不掉，说明它只是在占预算，可以砍掉。

```python
from statistics import mean

PARTS = ["system_rules", "tools", "retrieved", "history", "few_shot"]

def build_context(parts: list[str], case) -> list[dict]:
    """按给定的 part 集合拼上下文（缺的部分留空）。"""
    ...

def ablation(cases, judge) -> dict:
    results = {}
    # 全量基线
    base = mean(judge(case, run(build_context(PARTS, case))) for case in cases)
    results["FULL"] = base
    # 每次去掉一块
    for drop in PARTS:
        kept = [p for p in PARTS if p != drop]
        score = mean(judge(case, run(build_context(kept, case))) for case in cases)
        results[f"-{drop}"] = score
        delta = base - score
        toks_saved = mean(tokens_of(drop, case) for case in cases)
        print(f"去掉 {drop:<12} 质量 {score:.2f} (Δ{-delta:+.2f}) 省 token≈{toks_saved:.0f}")
    return results
```

输出大概是这样（示意数据，省略了 system_rules 那一行），可以这样解读：

```
去掉 retrieved    质量 0.41 (Δ-0.52) 省 token≈1400   ← 命脉，绝不能砍
去掉 history      质量 0.86 (Δ-0.07) 省 token≈8100   ← 掉分很少，却省 8K
去掉 few_shot     质量 0.83 (Δ-0.10) 省 token≈600    ← 有用但占地小，保留
去掉 tools        质量 0.55 (Δ-0.38) 省 token≈6000   ← 该任务确实需要工具
```

结论很清楚：history 占了 8K token，却只贡献 0.07 分，是首先要优化的对象，压缩或者截短都行。有了消融数据，砍哪块就不用靠拍脑袋，结果可以直接交给上一篇的成本优化去用。

---

## 4. LLM-as-judge 评 grounding

答对了还不够，还要确认答案确实基于给定的上下文，而不是模型从参数记忆里编出来的。就算碰巧编对了，换个问题也会出错。做法是让一个 judge 模型逐条核对，每个论断能否在上下文里找到依据。

```python
import anthropic
client = anthropic.Anthropic()

GROUNDING_JUDGE = """你是严格的事实核查员。判断【回答】中的每个事实性论断，
是否都能在【上下文】中找到直接依据。

【上下文】
{context}

【回答】
{answer}

只输出 JSON：
{{"grounded": true/false, "unsupported_claims": ["...无据论断..."], "score": 0.0-1.0}}
score = 有据论断数 / 总论断数。无据论断越多分越低。"""

def judge_grounding(context: str, answer: str) -> dict:
    import json
    resp = client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=2048,
        messages=[{"role": "user",
                   "content": GROUNDING_JUDGE.format(context=context, answer=answer)}],
    )
    return json.loads(next(b.text for b in resp.content if b.type == "text"))
```

grounding 分低、答案看起来却是对的，这是最危险的信号。它说明系统在靠运气，检索并没有支撑答案，数据分布一变就会出问题。

---

## 5. 把评测接进 CI：回归用例

每修好一个[故障排查](./03-debugging.md)里那类问题，就把它固化成一条用例，防止回归。

```python
import statistics

def evaluate_suite(cases, system) -> dict:
    quality, tokens, recalls = [], [], []
    for c in cases:
        ctx = system.build_context(c)
        ans = system.run(ctx)
        g = judge_grounding(render(ctx), ans)
        quality.append(g["score"])
        tokens.append(tokens_of_all(ctx))
        recalls.append(ContextEval(...).recall())
    return {
        "grounding": statistics.mean(quality),
        "avg_tokens": statistics.mean(tokens),   # 效率轴：越低越好
        "context_recall": statistics.mean(recalls),
    }

# CI 门禁：质量不许跌，token 不许悄悄涨
res = evaluate_suite(REGRESSION_CASES, my_system)
assert res["grounding"] >= 0.85, "grounding 回退"
assert res["avg_tokens"] <= 4500, "上下文膨胀了"
```

`avg_tokens` 这条断言很有用。为了多过几个 case 就往上下文里不断加东西，这种退化平时不容易察觉，这条断言能把它拦住。

---

## 6. 和 PE / Embedding 手册的评测衔接

上下文评测建立在两套已有方法之上：

- 答案质量和 LLM-as-judge 的方法，沿用 Prompt Engineering 手册（评估准则、judge prompt 设计、避免 judge 偏置）。
- 检索的 precision/recall 和向量召回质量，沿用 Embedding 手册（召回评测、reranking 评估、k 的选择）。
- 这本手册新增的部分，是把两套方法接起来，再加上上下文效率（token/任务）和消融这两个角度。

| 评测层 | 主要参考的手册 | 本手册关心的切面 |
|--------|----------|----------------|
| 答案对不对 / grounding | Prompt Engineering | judge 是否核对了上下文依据 |
| 检索召回质量 | Embedding | 召回的内容进上下文后的 precision/recall |
| 上下文效率 + 消融 | Context Engineering（本篇） | token/任务、每块上下文的边际价值 |

---

## 7. 落地清单

- 评测分两个维度：答案质量和 token/任务一起看
- 量化 context precision / recall，别只凭感觉说召回挺全
- 用消融实验找出白占预算的上下文块，交给降本去处理
- LLM-as-judge 重点检查 grounding，找出碰巧答对的情况
- 故障转成回归用例，CI 里同时守住质量下限和 token 上限
- 别只优化质量分，否则系统会被推向把窗口塞满

---

## 下一步

- 评测的输入来自 dump，先做可观测：[上下文可观测性](./01-observability.md)
- 用消融结论去做降本：[上下文成本优化实战](./02-cost-optimization.md)
- 从故障到回归用例：[上下文相关故障排查](./03-debugging.md)
- [最少必要上下文原则](../01-foundations/06-minimal-context.md)
- LLM-as-judge 的方法（Prompt Engineering 手册）：[LLM-as-Judge](../../../04-prompt-engineering/docs/05-by-task/05-judge.md)
- 检索召回评测（Embedding 手册）：[检索评测指标：Recall@k / MRR / nDCG](../../../06-embedding/docs/06-evaluation/01-metrics.md)

## 参考资料

- Ragas（RAG 评测框架，含 context precision/recall）：https://docs.ragas.io/
- Anthropic, "Effective context engineering for AI agents"：https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
