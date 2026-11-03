# 检索评测指标：Recall@k / MRR / nDCG

> 评测检索质量最常用的是三个指标：Recall@k（正确文档有没有进 top-k）、MRR（第一个正确答案排在第几）、nDCG（把整体排序和相关程度都算进去）。改 prompt、换模型、加 rerank 之后效果是变好还是变差，只有跑一遍这些指标才说得清。

---

## 1. 评测的输入

需要一个 evalset：

```jsonl
{"query_id": "q1", "query": "如何取消订阅", "relevant_docs": ["d1", "d3"]}
{"query_id": "q2", "query": "退款政策", "relevant_docs": ["d2"]}
{"query_id": "q3", "query": "ERR_500", "relevant_docs": ["d5", "d6", "d7"]}
```

每条记录包含一个 query，以及一组"正确"文档的 ID。evalset 怎么造，见 [建评测集：Query → Expected Docs](./02-build-evalset.md)。

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

实际项目里最常看 Recall@1 / 5 / 10。

另一种常见口径是 Hit Rate@k：只要有一个相关文档进了 top-k 就记 1，否则记 0。query 只有一个相关文档时两者相同；有多个时 Hit Rate 更宽松。本手册有些示例代码（比如[MTEB 怎么看 + 选型决策树](../02-models/06-mteb-selection.md)第 10 节）为了简单用的是 Hit Rate，报数字时要写清楚是哪种口径。

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

RAG 场景下 Recall 比 Precision 重要，因为只要答案文档进了 top-k，LLM 就能用上。搜索场景则相反，用户希望前几条都是准的，Precision 更重要。

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

适合只需要一个最对答案的场景，比如 QA、FAQ。

---

## 5. nDCG (normalized Discounted Cumulative Gain)

nDCG 同时考虑排序质量和多级相关性（0/1/2/3 分级）：

```
DCG@k = Σ (2^rel_i - 1) / log2(i + 1)
nDCG@k = DCG@k / IDCG@k   (ideal DCG)
```

它的计算逻辑是：高相关的文档出现得越早，得分越高；位置靠后的贡献按 log 折减；最后归一化到 0-1。

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

`relevance_dict` 的例子：

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
| RAG（找文档给 LLM） | Recall@5 / 10 |
| 搜索引擎 | Precision@10 + nDCG |
| QA（找一个答案）| MRR + Recall@1 |
| 推荐系统 | nDCG + MRR |

做 RAG 的话 Recall@5 一定要看，条件允许再跑 nDCG，看看排序好不好。

---

## 7. 完整 demo

```python
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

如果 evalset 标了多级相关性（0=无关，1=部分相关，2=相关，3=完美）：

```jsonl
{"query": "...", "relevance": {"d1": 3, "d2": 2, "d5": 1}}
```

这时用 nDCG 评测，比只有 0/1 的指标更细。

---

## 9. 跨方法对比

同一份 evalset，同时跑 N 个方法：

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

输出形如下表（示意数字，和[重排 Pipeline](../05-retrieval/05-rerank-pipeline.md)第 11 节的示意表同一口径）：

```
Vector only (3-small)         Recall@5 = 0.85
Vector only (3-large)         Recall@5 = 0.88
BM25 only                     Recall@5 = 0.75
Hybrid RRF                    Recall@5 = 0.92
Hybrid + Rerank               Recall@5 = 0.97
```

选型就按这张表来，不靠感觉。

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
# {"recall@5": 1.0, "mrr": 1.0, "ndcg@5": 0.96}
# q1 的 d2 排在第 3 位：nDCG@5 = (1 + 1/log2(4)) / (1 + 1/log2(3)) ≈ 0.92，q2 是 1.0，平均约 0.96
```

`ranx` 支持几十种 IR 指标，还内置了多个 run 的对比和显著性检验（`ranx.compare`）。

以 `ranx 0.3.21` 为准，返回值是 `numpy.float64`，打印出来会带 `np.float64(...)`。这个库更新不频繁，遇到兼容问题可以换 `ir-measures` 或 `pytrec_eval`，也可以照本篇前面几节用二十来行代码自己实现 Recall@k / MRR / nDCG。BEIR 主要用来跑公开数据集，评测自己的 evalset 用不上。

---

## 11. 评测时 Top-K 选多少

```
Recall@1：严苛，只看第 1 条
Recall@5：RAG 标准
Recall@10：宽松
Recall@20：召回上限
```

我一般以 Recall@5 为主，Recall@10 作参考。

---

## 12. 评测不能告诉你什么

检索指标回答不了下面三个问题：

- 答案对不对：这属于端到端 RAG 评测，见 [端到端 RAG 评测（含 LLM 答案）](./03-end-to-end.md)
- 延迟和成本：它们和检索质量之间要做取舍
- 真实用户的感受：evalset 不一定覆盖了所有场景

不过检索评测是基础，检索这一步做差了，后面的环节都补救不回来。

---

## 13. 下一步

- [建评测集：Query → Expected Docs](./02-build-evalset.md)
- [端到端 RAG 评测（含 LLM 答案）](./03-end-to-end.md)
- [持续评测 + 回归测试](./04-continuous.md)
