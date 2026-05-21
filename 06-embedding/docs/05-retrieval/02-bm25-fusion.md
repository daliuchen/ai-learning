# BM25 + Dense 融合：RRF / Weighted

> **一句话**：把 BM25 和 dense 两路召回结果用 **Reciprocal Rank Fusion (RRF)** 合并——不用调权重、稳定提分 5-10%，是混合检索的事实标准。

---

## 1. 融合的两种思路

### 思路 A：分数加权

```python
final_score = α × vec_score + (1 - α) × bm25_score
```

问题：

- 两个 score 尺度不同（vec 0-1，BM25 0-50+），需要归一化
- α 难调（不同场景最佳值不同）
- 跨 query 不稳

### 思路 B：RRF (Reciprocal Rank Fusion)

```python
final_score = Σ 1 / (k + rank_i)
```

只看排名，不看分数。稳、不用调，**实战首选**。

---

## 2. RRF 数学

```
对每个文档 d：
  RRF(d) = Σ over retrievers: 1 / (k + rank(d))

其中：
- rank(d) = 文档 d 在某 retriever 里的排名（1 = top1）
- 如果某 retriever 没召回 d, 该项为 0
- k 常量，默认 60
```

直觉：

- 同时在两个 retriever 都靠前 → 高分
- 只在一个 retriever 排第 1 → 中等分
- 在两个都很后 → 低分

---

## 3. RRF 实现

```python
from collections import defaultdict


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60, top_k: int = 10):
    """
    rankings: 多个 retriever 的结果列表，每个是 doc_id 列表（按排名）
    返回融合后的 doc_id 列表
    """
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += 1 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)[:top_k]


# 用法
vec_hits = ["d3", "d1", "d5", "d2"]      # vector 召回排名
bm25_hits = ["d1", "d2", "d4", "d5"]     # BM25 召回排名


fused = reciprocal_rank_fusion([vec_hits, bm25_hits], top_k=4)
print(fused)  # ['d1', 'd5', 'd2', 'd3', ...]
```

---

## 4. 完整混合检索 demo

```python
# demos/retrieval/02_rrf.py
import numpy as np
from collections import defaultdict
from openai import OpenAI
from rank_bm25 import BM25Okapi
import jieba


client = OpenAI()


corpus = [
    {"id": "d1", "text": "如何取消订阅 - 登录后进入设置取消"},
    {"id": "d2", "text": "ERR_TIMEOUT_500 表示后端响应超时"},
    {"id": "d3", "text": "停止自动续费的方法"},
    {"id": "d4", "text": "kubectl exec -it pod 进入容器"},
    {"id": "d5", "text": "网络连接慢的常见原因"},
    {"id": "d6", "text": "API 调用速率限制说明"},
    {"id": "d7", "text": "用户账户安全设置"},
    {"id": "d8", "text": "怎么退款 - 7 天内全额"},
]


# 准备
texts = [d["text"] for d in corpus]
ids = [d["id"] for d in corpus]


# Vector
vecs = np.array([
    d.embedding for d in client.embeddings.create(
        model="text-embedding-3-small", input=texts
    ).data
])

# BM25
tokenized = [list(jieba.cut(t)) for t in texts]
bm25 = BM25Okapi(tokenized)


def vector_search(query, top_k=5):
    qv = np.array(client.embeddings.create(model="text-embedding-3-small", input=[query]).data[0].embedding)
    sims = vecs @ qv
    order = np.argsort(-sims)[:top_k]
    return [ids[i] for i in order]


def bm25_search(query, top_k=5):
    scores = bm25.get_scores(list(jieba.cut(query)))
    order = np.argsort(-scores)[:top_k]
    return [ids[i] for i in order]


def rrf(rankings, k=60, top_k=5):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += 1 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)[:top_k]


def hybrid_search(query, top_k=5):
    v = vector_search(query, top_k=10)
    b = bm25_search(query, top_k=10)
    return rrf([v, b], top_k=top_k)


for q in ["怎么取消订阅", "ERR_TIMEOUT_500", "停止扣费"]:
    print(f"\n=== {q} ===")
    print(f"Vector: {vector_search(q, 3)}")
    print(f"BM25:   {bm25_search(q, 3)}")
    print(f"Hybrid: {hybrid_search(q, 3)}")
```

---

## 5. Weighted RRF（带权重）

如果一路明显更可信，可以加权：

```python
def weighted_rrf(rankings_with_weight, k=60, top_k=10):
    scores = defaultdict(float)
    for ranking, weight in rankings_with_weight:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += weight / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)[:top_k]


fused = weighted_rrf([
    (vec_hits, 0.7),
    (bm25_hits, 0.3),
])
```

**通常不需要**——纯 RRF 已经够好。

---

## 6. 在向量库里做混合

### 6.1 Qdrant Hybrid（推荐）

Qdrant 1.10+ 支持 dense + sparse 一次查：

```python
from qdrant_client.models import (
    Prefetch, FusionQuery, Fusion,
    NamedSparseVector, NamedVector,
    SparseVector,
)


# 假设 collection 同时有 dense 和 sparse vector
results = client.query_points(
    collection_name="hybrid_docs",
    prefetch=[
        Prefetch(query=dense_vec, using="dense", limit=20),
        Prefetch(query=SparseVector(indices=[...], values=[...]), using="sparse", limit=20),
    ],
    query=FusionQuery(fusion=Fusion.RRF),
    limit=5,
)
```

### 6.2 Pinecone

```python
results = index.query(
    vector=dense_vec,
    sparse_vector={"indices": [...], "values": [...]},
    top_k=5,
    alpha=0.5,   # 0=只 sparse, 1=只 dense
)
```

### 6.3 Elasticsearch / OpenSearch

```python
{
  "knn": {
    "field": "embedding",
    "query_vector": [...],
    "k": 20
  },
  "query": {
    "match": {"content": "用户的 query"}
  },
  "rank": {"rrf": {}}
}
```

---

## 7. Sparse vector 怎么来

3 种方式：

### 7.1 BM25 → sparse vector

```python
# BM25 weights 转 sparse vector
# 词表里每个词一个 index
def bm25_to_sparse(text, bm25, vocab):
    tokens = list(jieba.cut(text))
    sparse = {}
    for token in tokens:
        if token in vocab:
            idx = vocab[token]
            sparse[idx] = sparse.get(idx, 0) + 1
    return sparse
```

实战麻烦，向量库提供工具或者自带（如 Pinecone 的 hosted sparse encoder）。

### 7.2 BGE-M3 自带

```python
from FlagEmbedding import BGEM3FlagModel


model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)


output = model.encode(
    sentences=[query],
    return_sparse=True,
)


sparse_dict = output["lexical_weights"][0]
# {"如何": 0.34, "取消": 0.52, "订阅": 0.48, ...}
```

转向量库格式：

```python
# Qdrant
indices = []
values = []
for token, weight in sparse_dict.items():
    # 需要把 token 映射到 int index（vocab）
    idx = vocab[token]
    indices.append(idx)
    values.append(weight)
```

### 7.3 SPLADE

> Sparse Lexical and Expansion Model

```python
from transformers import AutoModelForMaskedLM, AutoTokenizer
import torch


model = AutoModelForMaskedLM.from_pretrained("naver/splade-cocondenser-ensembledistil")
tokenizer = AutoTokenizer.from_pretrained("naver/splade-cocondenser-ensembledistil")


def get_sparse(text):
    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        out = model(**inputs).logits
    # SPLADE：max-pool over tokens, log activation
    sparse = torch.max(torch.log1p(torch.relu(out)) * inputs.attention_mask.unsqueeze(-1), dim=1)[0]
    return sparse[0].numpy()
```

SPLADE 比 BM25 更"智能"，效果通常更好但更慢。

---

## 8. RRF 提升实测

200 条客服 evalset：

| Retriever | Recall@5 |
|-----------|----------|
| Vector only | 85% |
| BM25 only | 75% |
| Hybrid (RRF) | 92% |
| Hybrid + rerank | 96% |

提 5-10%，几乎免费午餐。

---

## 9. 何时不需要 BM25

```
你的 query 全是自然语言闲聊：
  → vector 够

你的数据全是产品描述 / 内容文章：
  → vector 够

你的数据有大量代码 / ID / 错误码：
  → 必须 BM25

跨语言检索：
  → BM25 没用
```

---

## 10. 常见坑

| 坑 | 解 |
|----|----|
| 直接加分（不归一化）| 用 RRF |
| BM25 分词没做 / 错 | 中文用 jieba，英文 split |
| RRF k=60 不变 | 通常不用调，调也只 30-100 |
| 召回数太少（top_k=5） | 每路召回 20-50，再融合取 top-5 |
| Sparse + Dense 维度不同 | 不能直接 concat，用向量库的 hybrid API |

---

## 11. 下一步

- 📖 HyDE：用 LLM 改写 query → [03-hyde.md](./03-hyde.md)
- 📖 Multi-query / Sub-query → [04-multi-query.md](./04-multi-query.md)
- 📖 Rerank pipeline（混合后再精排）→ [05-rerank-pipeline.md](./05-rerank-pipeline.md)
