# EKB 35：Rerank 精排——召回宽，入 prompt 精

> **一句话**：混合检索召回了 10-20 条，但里面仍有噪声，且排序未必最优。Rerank 用一个更精确（也更贵）的模型，对召回结果重新打分，选出真正最相关的 top 3-5 塞进 prompt。这是「召回宽、精排窄」策略的最后一环，往往是 recall→答案质量提升最明显的一步。

---

## 1. 为什么召回之后还要 rerank

向量/BM25 检索快，但**精度有限**——它们用的是「轻量相似度」（向量点积、词频），能快速从几千 chunk 里捞出候选，但排序粗糙：

```
混合检索 top-10：第 1 条其实只是沾边，真正最相关的排在第 6
→ 如果直接取 top-3 进 prompt，最相关的那条被漏掉了
```

Rerank 模型（cross-encoder）把「问题 + 每个 chunk」**成对**喂进去深度比较，精度高得多，但慢——所以不能用它检索全库，只能用它给召回的少量候选重新排序。

```
全库几千 chunk → [快速检索] → 召回 20 条 → [rerank 精排] → top 3-5 → prompt
                  recall 优先              precision 优先
```

---

## 2. 接入 rerank

用 cross-encoder 类 rerank 模型（如 bge-reranker），或商用 rerank API：

```python
# retrieve/rerank.py
from sentence_transformers import CrossEncoder

reranker = CrossEncoder("BAAI/bge-reranker-large")

def rerank(question: str, candidates: list[dict], top_k: int = 4) -> list[dict]:
    pairs = [(question, c["content"]) for c in candidates]
    scores = reranker.predict(pairs)          # 每个候选一个相关度分
    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)
    ranked = sorted(candidates, key=lambda c: -c["rerank_score"])
    return ranked[:top_k]
```

接进检索流水线：

```python
from retrieve.hybrid import hybrid_search
from retrieve.rerank import rerank

def retrieve(question: str, k: int = 4) -> list[dict]:
    candidates = hybrid_search(question, recall_k=20, final_k=20)  # 召回宽
    return rerank(question, candidates, top_k=k)                   # 精排窄
```

注意 `final_k=20`（把混合检索的候选全给 rerank），rerank 精排到 `k=4`。

---

## 3. rerank 同时收紧「答不出」判断

rerank 分数比检索距离更能反映真实相关度，所以也是更好的**兜底信号**：

```python
RERANK_THRESHOLD = 0.3    # 评估集校准

def retrieve_with_gate(question, k=4):
    ranked = retrieve(question, k=k * 2)
    relevant = [c for c in ranked if c["rerank_score"] > RERANK_THRESHOLD]
    return relevant[:k]    # 全部低于阈值 → 返回空 → 走兜底
```

用 rerank 分数当阈值，比用向量距离更准——因为 cross-encoder 对「相关/不相关」的判断更可靠。这强化了第 30 篇的兜底防线一。

---

## 4. rerank 的成本权衡

rerank 不是免费的，要权衡：

| 维度 | 影响 |
|------|------|
| 延迟 | cross-encoder 对 20 个候选打分，增加几十~几百 ms |
| 成本 | 本地模型吃算力；商用 rerank API 按次计费 |
| 候选数 | 给 rerank 的候选越多越准，但越慢 |

经验：召回 20 条给 rerank 是性价比不错的点。候选给到 50+ 提升有限但明显变慢。延迟敏感时，召回数和 rerank 候选数要一起调（详见 [10-production/02-cost-latency](../10-production/02-cost-latency.md)）。

---

## 5. 评估 rerank 的提升

加 rerank 后跑评估，重点看 **recall@3**（精排后头部质量）和**引用准确率**：

```
                 recall@5    recall@3    引用准确率
混合(无rerank)    0.79        0.68        0.74
混合 + rerank     0.81        0.83        0.86   ← recall@3 和引用准确率大涨
```

典型现象：rerank 对 recall@5 提升有限（候选本就在召回里），但 **recall@3 和引用准确率明显提升**——因为它把最相关的顶到了最前面，进 prompt 的片段质量更高，模型答得更准、引得更对。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 不 rerank 直接取检索 top-3 | 最相关的可能被漏 | 召回宽 → rerank → 取窄 |
| 给 rerank 的候选太少 | 精排空间小 | 召回 20 条给它 |
| 用 rerank 检索全库 | 极慢 | rerank 只排候选 |
| 只看 recall@5 评估 rerank | 看不出它的价值 | 看 recall@3 和引用准确率 |
| 忽略 rerank 延迟 | 在线超时 | 候选数与延迟一起权衡 |

---

## 下一步

检索侧增强差最后一项——在源头改写口语问题：

→ [05-query-rewrite](./05-query-rewrite.md)
