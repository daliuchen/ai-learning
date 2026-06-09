# EKB 34：混合检索——用 RRF 融合两路结果

> **一句话**：向量召回一批、BM25 召回一批，怎么合成一个排序？最简单可靠的方法是 **RRF（Reciprocal Rank Fusion，倒数排名融合）**——只看每条结果在各路里的**排名**，不用纠结两路分数不可比的问题。本篇实现混合检索。

---

## 1. 融合的难点：两路分数不可比

```
向量检索：用余弦距离，范围 0~2，越小越好
BM25：    用 BM25 分，范围 0~几十，越大越好
```

两个分数**量纲完全不同**，没法直接相加。强行归一化又很脆弱（分布不稳定）。RRF 的聪明之处：**完全不用分数，只用排名**。

---

## 2. RRF 怎么算

每条结果在某一路里排第 `r` 名，贡献分数 `1/(k + r)`（k 是平滑常数，常取 60）。一条结果的总分 = 它在所有路里的贡献之和：

```
RRF(d) = Σ  1 / (k + rank_i(d))
        各路 i
```

排名越靠前（r 小），贡献越大；同时出现在多路的结果，分数叠加，自然排到前面——**这正是我们想要的：两路都认可的，最可信。**

```python
# retrieve/hybrid.py
def rrf_fuse(vector_hits: list[dict], bm25_hits: list[dict], k: int = 60) -> list[dict]:
    scores, by_id = {}, {}
    for rank, hit in enumerate(vector_hits):
        scores[hit["id"]] = scores.get(hit["id"], 0) + 1 / (k + rank + 1)
        by_id[hit["id"]] = hit
    for rank, hit in enumerate(bm25_hits):
        scores[hit["id"]] = scores.get(hit["id"], 0) + 1 / (k + rank + 1)
        by_id[hit["id"]] = hit
    ranked_ids = sorted(scores, key=lambda i: -scores[i])
    return [{**by_id[i], "rrf_score": scores[i]} for i in ranked_ids]
```

---

## 3. 完整的混合检索

把向量、BM25、融合串起来：

```python
from retrieve.vector import vector_search
from retrieve.bm25 import bm25_search

def hybrid_search(question: str, recall_k: int = 20, final_k: int = 10) -> list[dict]:
    v_hits = vector_search(question, k=recall_k)   # 召回宽
    b_hits = bm25_search(question, k=recall_k)
    fused = rrf_fuse(v_hits, b_hits)
    return fused[:final_k]
```

注意 `recall_k=20`（两路各召回 20，召回要宽），融合后取 `final_k`（这一步还没 rerank，先取 10 交给下一步精排）。

---

## 4. 为什么 RRF 比「加权求和」好

另一种融合是 `α·向量分 + (1-α)·BM25分`，但它有两个麻烦：

| 加权求和 | RRF |
|----------|-----|
| 要先归一化两路分数（脆弱） | 不碰分数，只用排名 |
| 要调权重 α（又一个超参） | 几乎无需调参（k=60 通用） |
| 分数分布变了就失效 | 排名稳定 |

RRF **几乎零调参、鲁棒**，是工业界混合检索的默认选择。起步无脑用 RRF，等评估发现某一路明显更重要时，再考虑加权变体。

---

## 5. 评估融合效果

加混合检索后跑评估，和 baseline + 单路对比：

```
                 recall@5    口语类    专名类
MVP 单路向量       0.66       0.55      0.50
仅 BM25           0.60       0.45      0.80
混合(RRF)         0.79       0.68      0.78   ← 两路优势都吃到
```

典型现象：**混合的整体 recall 高于任一单路**，尤其在「专名类」吃到 BM25 的好、「口语类」吃到向量的好。如果混合反而比单路差，检查融合实现或某一路是否出了 bug。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 直接相加两路分数 | 量纲不可比，乱 | 用 RRF（只看排名） |
| 召回 k 设太小 | 融合候选不足 | 两路各召回 20+ |
| 融合后不去重 | 同 chunk 重复 | 按 id 聚合 |
| RRF 还纠结调 α | 白费功夫 | RRF 几乎不用调参 |
| 不和单路对比就上 | 不知是否真有提升 | 评估对比三者 |

---

## 下一步

融合后还有噪声，用 rerank 做最后的精排：

→ [04-rerank](./04-rerank.md)
