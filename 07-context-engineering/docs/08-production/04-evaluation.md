# CE 08-04：评测——上下文质量怎么衡量

> **一句话**：评 Context Engineering 不只看「最终答案对不对」，还要看「为了答对花了多少上下文」。两个系统都答对，一个塞 20K token、一个塞 3K，后者才是好工程。所以 CE 的评测是二维的：**答案质量 × 上下文效率**。再往里拆，要量化「检索进上下文的内容有多少真有用」（precision/recall）、要靠消融实验确认每一块上下文都在创造价值、要用 LLM-as-judge 验证答案确实 grounding 在给定上下文上而非凭空发挥。

---

## 1. 评什么：质量 + 效率两个轴

| 轴 | 指标 | 回答什么 |
|----|------|----------|
| 答案质量 | 正确性、grounding（有据）、完整性 | 任务做没做对 |
| 上下文效率 | token/任务、有效信息占比、检索 precision | 做对这件事花了多少上下文预算 |

只看质量会鼓励「往窗口里疯狂塞」——塞得多确实更容易蒙对，但贵、慢、还有 context rot 风险。**把效率一起评，才逼出「最少必要上下文」这个 CE 核心原则**（见 [../01-foundations/06-minimal-context.md](../01-foundations/06-minimal-context.md)）。一个实用的复合视角：在「答案质量不掉」的前提下，token/任务越低越好。

---

## 2. Context Precision / Recall：进上下文的料有多少真有用

借用 RAG 评测的两个核心指标，但聚焦「上下文装载」：

- **Context Recall（召回）**：回答这个问题**需要**的事实，有多少真的进了上下文？低 → 漏召回 → 幻觉风险。
- **Context Precision（精度）**：进了上下文的内容里，有多少**真的相关**？低 → 噪声多 → context rot、稀释、浪费 token。

二者是权衡：盲目加大检索 k 会拉高 recall 但砸低 precision。评测的意义就是找到那个甜点——recall 够用的前提下，precision 尽量高。

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

「哪些 chunk 真相关」既可人工标，也可用 LLM-as-judge 自动判（见第 4 节），后者才能规模化跑。

---

## 3. 消融实验：去掉某块上下文看效果

这是 CE 评测最有性价比的工具。逐一拿掉上下文的某个部分，看答案质量掉多少——**掉得多说明这块在干活，几乎不掉说明它在白占预算可以砍掉**。

```python
import itertools

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

读法示例：

```
去掉 retrieved    质量 0.41 (Δ-0.52) 省 token≈1400   ← 命脉，绝不能砍
去掉 history      质量 0.86 (Δ-0.07) 省 token≈8100   ← 砍掉几乎不掉分，省 8K！
去掉 few_shot     质量 0.83 (Δ-0.10) 省 token≈600    ← 有用但占地小，保留
去掉 tools        质量 0.55 (Δ-0.38) 省 token≈6000   ← 该任务确实需要工具
```

结论一目了然：**history 占了 8K token 却只值 0.07 分，是头号优化对象**（压缩或砍短）。消融把「该砍哪块」从拍脑袋变成数据驱动，直接喂给上一篇的成本优化。

---

## 4. LLM-as-judge 评 grounding

「答得对」还不够，要确认答案**真的基于给定上下文**，而不是模型从参数记忆里编的（哪怕碰巧编对了，换个问题就翻车）。让一个 judge 模型核对每个论断能否在上下文里找到依据。

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
        model="claude-sonnet-4-5",
        max_tokens=512,
        messages=[{"role": "user",
                   "content": GROUNDING_JUDGE.format(context=context, answer=answer)}],
    )
    return json.loads(resp.content[0].text)
```

grounding 低但答案「看起来对」是最危险的信号——说明系统靠运气，检索其实没真正支撑答案，换个分布就崩。

---

## 5. 把评测接进 CI：回归用例

每修一个 [03-debugging.md](./03-debugging.md) 里的故障，就把它固化成一条用例，防止回归。

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

`avg_tokens` 这条断言很关键——它拦住「为了多过几个 case 就疯狂往上下文塞东西」的隐性退化。

---

## 6. 和 PE / Embedding 手册的评测衔接

CE 评测不是孤岛，它站在两套已有方法论之上：

- **答案质量 / LLM-as-judge 的方法论**沿用 Prompt Engineering 手册那套（评估准则、judge prompt 设计、避免 judge 偏置）。
- **检索 precision/recall、向量召回质量**沿用 Embedding 手册那套（召回评测、reranking 评估、k 的选择）。
- CE 的**增量**是把这两套缝起来，再加上「上下文效率（token/任务）」和「消融」这两个 CE 独有的视角。

| 评测层 | 主战手册 | CE 关心的切面 |
|--------|----------|----------------|
| 答案对不对 / grounding | Prompt Engineering | judge 是否核对了上下文依据 |
| 检索召回质量 | Embedding | 召回的料进上下文后 precision/recall |
| 上下文效率 + 消融 | Context Engineering（本篇） | token/任务、每块上下文的边际价值 |

---

## 7. 落地清单

- ✅ 评测二维化：答案质量 **和** token/任务一起看
- ✅ 量化 context precision / recall，别只信「感觉召回挺全」
- ✅ 用消融实验找出「白占预算」的上下文块，喂给降本
- ✅ LLM-as-judge 重点验 grounding，揪出「碰巧答对」
- ✅ 故障转回归用例，CI 里同时守住质量和 token 上限
- ❌ 别只优化质量分——会把系统推向「无脑塞满窗口」

---

## 下一步

- 📖 评测的输入来自 dump，先做可观测 → [01-observability.md](./01-observability.md)
- 📖 消融结论直接驱动降本 → [02-cost-optimization.md](./02-cost-optimization.md)
- 📖 故障 → 回归用例的闭环 → [03-debugging.md](./03-debugging.md)
- 📖 最少必要上下文原则 → [../01-foundations/06-minimal-context.md](../01-foundations/06-minimal-context.md)
- 📖 LLM-as-judge 方法论（PE 手册） → [../../../04-prompt-engineering/docs/05-by-task/05-judge.md](../../../04-prompt-engineering/docs/05-by-task/05-judge.md)
- 📖 检索召回评测（Embedding 手册） → [../../../06-embedding/docs/06-evaluation/01-metrics.md](../../../06-embedding/docs/06-evaluation/01-metrics.md)

## 参考资料

- Ragas（RAG 评测框架，含 context precision/recall）：https://docs.ragas.io/
- Anthropic, "Effective context engineering for AI agents"：https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
