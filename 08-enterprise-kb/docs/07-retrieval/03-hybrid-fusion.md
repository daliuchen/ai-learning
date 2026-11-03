# EKB 34：混合检索，用 RRF 融合两路结果

> 向量召回一批，BM25 召回一批，怎么合成一个排序？最简单也最可靠的办法是 RRF（Reciprocal Rank Fusion，倒数排名融合）。它只看每条结果在各路里排第几，两路分数不可比的问题就绕开了。这篇实现混合检索。

---

## 1. 融合难在两路分数不可比

```
向量检索：用余弦距离，范围 0~2，越小越好
BM25：    用 BM25 分，范围 0~几十，越大越好
```

两个分数的量纲完全不同，没法直接相加；硬做归一化又很脆弱，分布一变就不准。RRF 干脆不用分数，只用排名。

---

## 2. RRF 怎么算

一条结果在某一路里排第 `r` 名，就贡献 `1/(k + r)` 分（k 是平滑常数，常取 60）。总分是它在所有路里的贡献之和：

```
RRF(d) = Σ  1 / (k + rank_i(d))
        各路 i
```

排名越靠前（r 越小），贡献越大。同时出现在多路里的结果分数会叠加，自然排到前面。两路都认可的结果最可信，融合要的就是这个效果。

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

把向量检索、BM25 和融合串起来：

```python
from retrieve.vector import vector_search
from retrieve.bm25 import bm25_search

def hybrid_search(question: str, recall_k: int = 20, final_k: int = 10) -> list[dict]:
    v_hits = vector_search(question, k=recall_k)   # 召回宽
    b_hits = bm25_search(question, k=recall_k)
    fused = rrf_fuse(v_hits, b_hits)
    return fused[:final_k]
```

`recall_k=20` 表示两路各召回 20 条，召回要宽。融合后取 `final_k` 条，这一步还没有 rerank，先取 10 条交给下一步精排。

---

## 4. RRF 和加权求和比

另一种融合方式是 `α·向量分 + (1-α)·BM25分`，它有几处麻烦：

| 加权求和 | RRF |
|----------|-----|
| 要先归一化两路分数，比较脆弱 | 不碰分数，只用排名 |
| 要调权重 α，多一个超参 | 基本不用调（k=60 通用） |
| 分数分布变了就失效 | 排名稳定 |

RRF 基本不用调参，结果也稳，是工业界做混合检索的默认选择。起步直接用 RRF，等评估发现某一路明显更重要，再考虑加权的变体。

---

## 5. 评估融合效果

加上混合检索后跑评估，和 baseline 及单路结果对比。下表为示意数据，不是实测值：

```
                 recall@5    口语类    专名类
MVP 单路向量       0.66       0.55      0.50
仅 BM25           0.60       0.45      0.80
混合(RRF)         0.79       0.68      0.78   ← 两路优势都吃到
```

正常情况下，混合检索的整体 recall 高于任何一路：专名类问题沾了 BM25 的光，口语类问题沾了向量的光。如果混合反而比单路差，去查融合的实现，或者看看是不是某一路出了 bug。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 直接相加两路分数 | 量纲不同，排序混乱 | 用 RRF，只看排名 |
| 召回 k 设得太小 | 融合的候选不够 | 两路各召回 20 条以上 |
| 融合后不去重 | 同一个 chunk 重复出现 | 按 id 聚合 |
| 用了 RRF 还在调 α | 白费功夫 | RRF 基本不用调参 |
| 不和单路对比就上线 | 不知道有没有真的提升 | 三者一起评估对比 |

---

## 下一步

融合后的结果里还有噪声，最后用 rerank 精排：

→ [Rerank 精排](./04-rerank.md)
