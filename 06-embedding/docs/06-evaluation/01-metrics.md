# 检索评测指标：Recall@k / MRR / nDCG

> **一句话**：评测检索好坏的三大主指标是 **Recall@k**（召回率）/ **MRR**（首个正确答案的位置）/ **nDCG**（考虑排序）——不评测就不知道改 prompt / 换模型 / 加 rerank 是好是坏。

---

## 1. 评测的输入

需要一个 evalset：

```jsonl
{"query_id": "q1", "query": "如何取消订阅", "relevant_docs": ["d1", "d3"]}
{"query_id": "q2", "query": "退款政策", "relevant_docs": ["d2"]}
{"query_id": "q3", "query": "ERR_500", "relevant_docs": ["d5", "d6", "d7"]}
```

每条：

- 一个 query
- 一组"正确"的文档 ID

详见 [02-build-evalset.md](./02-build-evalset.md) 怎么造 evalset。

---

## 2. Recall@k

```
Recall@k = (检索到的相关文档数) / (总相关文档数)
```

例：

```
query: "如何取消订阅"
relevant: [d1, d3]

retriever 返回 top-5: [d1, d2, d5, d7, d9]

→ 相关的 d1 在 top-5 里，d3 没找到
→ Recall@5 = 1/2 = 0.5
```

实战上常用 **Recall@1 / 5 / 10**。

```python
def recall_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    top_k = set(retrieved[:k])
    rel = set(relevant)
    if not rel:
        return 1.0
    return len(top_k & rel) / len(rel)
```

---

## 3. Precision@k

```
Precision@k = (检索到的相关文档数) / k
```

```
top-5 里有 2 个相关 → Precision@5 = 2/5 = 0.4
```

**RAG 场景 Recall 比 Precision 重要**——只要"答案文档在 top-k 里"就行。
但**搜索场景** Precision 重要（用户希望前几条都准）。

```python
def precision_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    top_k = retrieved[:k]
    rel = set(relevant)
    if not top_k:
        return 0.0
    return sum(1 for d in top_k if d in rel) / len(top_k)
```

---

## 4. MRR (Mean Reciprocal Rank)

```
RR(q) = 1 / rank of first relevant doc
MRR = mean over all queries
```

例：

```
query 1: 第 1 个就是对的 → RR = 1/1 = 1.0
query 2: 第 3 个才对 → RR = 1/3 = 0.33
query 3: top-10 都没找到 → RR = 0

MRR = (1.0 + 0.33 + 0) / 3 = 0.44
```

```python
def mrr(retrieved: list[str], relevant: list[str]) -> float:
    rel = set(relevant)
    for rank, doc in enumerate(retrieved, 1):
        if doc in rel:
            return 1 / rank
    return 0.0
```

适合"我只要一个最对的答案"的场景（QA / FAQ）。

---

## 5. nDCG (normalized Discounted Cumulative Gain)

考虑排序质量 + 多级相关性（0/1/2/3 分级）：

```
DCG@k = Σ (2^rel_i - 1) / log2(i + 1)
nDCG@k = DCG@k / IDCG@k   (ideal DCG)
```

直觉：

- 越早出现高相关，得分越高
- 用 log 折减（位置越后影响越小）
- normalize 到 0-1

```python
import math


def dcg_at_k(retrieved: list[str], relevance_dict: dict[str, int], k: int) -> float:
    dcg = 0
    for i, doc in enumerate(retrieved[:k], 1):
        rel = relevance_dict.get(doc, 0)
        dcg += (2 ** rel - 1) / math.log2(i + 1)
    return dcg


def ndcg_at_k(retrieved: list[str], relevance_dict: dict[str, int], k: int) -> float:
    dcg = dcg_at_k(retrieved, relevance_dict, k)
    ideal_order = sorted(relevance_dict, key=relevance_dict.get, reverse=True)
    idcg = dcg_at_k(ideal_order, relevance_dict, k)
    return dcg / idcg if idcg > 0 else 0
```

**relevance_dict** 例：

```python
{
    "d1": 3,   # 完美匹配
    "d2": 2,   # 高相关
    "d3": 1,   # 有点相关
    "d4": 0,   # 无关
}
```

---

## 6. 指标选哪个

| 场景 | 主指标 |
|------|--------|
| RAG（找文档给 LLM） | **Recall@5 / 10** |
| 搜索引擎 | **Precision@10 + nDCG** |
| QA（找一个答案）| **MRR + Recall@1** |
| 推荐系统 | **nDCG + MRR** |

实战 RAG **必看 Recall@5**，能跑就跑 nDCG 看排序好不好。

---

## 7. 完整 demo

```python
# demos/evaluation/01_metrics.py
import math
import numpy as np
from openai import OpenAI


client = OpenAI()


# evalset
EVALSET = [
    {"query": "如何取消订阅", "relevant": [0, 2]},
    {"query": "退款政策", "relevant": [4]},
    {"query": "登录失败", "relevant": [1]},
]


CORPUS = [
    "如何关闭自动续费的步骤",     # d0
    "如何处理登录失败错误",        # d1
    "停止订阅完整教程",            # d2
    "如何修改密码",                # d3
    "退款政策：7 天内全额",        # d4
    "支付方式说明",                # d5
]


corpus_vecs = np.array([
    d.embedding for d in client.embeddings.create(
        model="text-embedding-3-small", input=CORPUS
    ).data
])


def search(query, top_k=5):
    q_vec = client.embeddings.create(model="text-embedding-3-small", input=[query]).data[0].embedding
    sims = corpus_vecs @ np.array(q_vec)
    return np.argsort(-sims)[:top_k].tolist()


def recall_at_k(retrieved, relevant, k):
    return len(set(retrieved[:k]) & set(relevant)) / len(relevant) if relevant else 1


def mrr(retrieved, relevant):
    rel = set(relevant)
    for i, d in enumerate(retrieved, 1):
        if d in rel:
            return 1 / i
    return 0


# 跑评测
recalls = []
mrrs = []

for case in EVALSET:
    retrieved = search(case["query"], top_k=5)
    r5 = recall_at_k(retrieved, case["relevant"], 5)
    r1 = recall_at_k(retrieved, case["relevant"], 1)
    rr = mrr(retrieved, case["relevant"])
    recalls.append(r5)
    mrrs.append(rr)
    print(f"  '{case['query']}': R@1={r1:.2f}  R@5={r5:.2f}  RR={rr:.2f}")


print(f"\nRecall@5 平均: {np.mean(recalls):.3f}")
print(f"MRR 平均:      {np.mean(mrrs):.3f}")
```

---

## 8. 多级相关性

如果 evalset 标了多级（0=无关, 1=部分, 2=相关, 3=完美）：

```jsonl
{"query": "...", "relevance": {"d1": 3, "d2": 2, "d5": 1}}
```

用 nDCG 评测，比单 0/1 更精细。

---

## 9. 跨方法对比

跑一份 evalset，对比 N 个方法：

```python
methods = {
    "Vector only (3-small)": lambda q: search_vector(q),
    "Vector only (3-large)": lambda q: search_vector(q, model="text-embedding-3-large"),
    "BM25 only": search_bm25,
    "Hybrid RRF": search_hybrid,
    "Hybrid + Rerank": search_hybrid_rerank,
}


for name, search_fn in methods.items():
    recalls = []
    for case in EVALSET:
        retrieved = search_fn(case["query"])
        r5 = recall_at_k(retrieved, case["relevant"], 5)
        recalls.append(r5)
    print(f"{name:<30}  Recall@5 = {np.mean(recalls):.3f}")
```

输出对比表：

```
Vector only (3-small)         Recall@5 = 0.85
Vector only (3-large)         Recall@5 = 0.88
BM25 only                     Recall@5 = 0.75
Hybrid RRF                    Recall@5 = 0.92
Hybrid + Rerank               Recall@5 = 0.96
```

数据驱动选型。

---

## 10. ranx：专业评测库

```bash
pip install ranx
```

```python
from ranx import Qrels, Run, evaluate


# Ground truth
qrels = Qrels({
    "q1": {"d1": 1, "d2": 1},
    "q2": {"d3": 1},
})


# 你的检索结果
run = Run({
    "q1": {"d1": 0.9, "d5": 0.7, "d2": 0.6},
    "q2": {"d3": 0.95, "d4": 0.5},
})


metrics = evaluate(qrels, run, metrics=["recall@5", "mrr", "ndcg@5"])
print(metrics)
# {"recall@5": 1.0, "mrr": 1.0, "ndcg@5": 1.0}
```

`ranx` 支持几十种 IR 指标，工业级。

---

## 11. 评测时 Top-K 选多少

```
Recall@1：严苛，只看第 1 条
Recall@5：RAG 标准
Recall@10：宽松
Recall@20：召回上限
```

实战：**主要看 Recall@5，辅助看 Recall@10**。

---

## 12. 评测不能告诉你什么

- **答案对不对** —— 这是端到端 RAG 评测，详见 [03-end-to-end.md](./03-end-to-end.md)
- **延迟 / 成本** —— 跟检索质量是 trade-off
- **真实用户感受** —— evalset 不一定 cover 所有场景

但**检索评测是基础**——检索拉跨，后续都救不回来。

---

## 13. 下一步

- 📖 怎么造 evalset → [02-build-evalset.md](./02-build-evalset.md)
- 📖 端到端 RAG 评测 → [03-end-to-end.md](./03-end-to-end.md)
- 📖 持续评测 + 回归 → [04-continuous.md](./04-continuous.md)
