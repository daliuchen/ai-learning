# BM25 + Dense 融合：RRF / Weighted

> BM25 和向量检索各召回一批结果之后，要合成一个列表。最常用的办法是 Reciprocal Rank Fusion（RRF）：只看排名、不用调权重，通常能稳定提升召回，也是混合检索事实上的默认做法。

---

## 1. 融合的两种思路

### 思路 A：分数加权

```python
final_score = α × vec_score + (1 - α) × bm25_score
```

这个办法有三个麻烦：两路分数尺度不同（vec 0-1，BM25 可以到 50 以上），必须先归一化；α 很难调，不同场景最佳值不一样；换一个 query，最优权重又会变。

### 思路 B：RRF (Reciprocal Rank Fusion)

```python
final_score = Σ 1 / (k + rank_i)
```

RRF 只看排名，不看分数，所以既稳定又不用调参。**实战里先用 RRF。**

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

按这个公式，两路都排得靠前的文档分最高；只在一路排第 1 的拿中等分；两路都靠后的分很低。

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
print(fused)  # ['d1', 'd2', 'd5', 'd3']
```

---

## 4. 完整混合检索 demo

```python
# 注：rank-bm25 最后一次发版是 2022 年（0.2.2），仍可用但已停止维护，规模大时可换 bm25s 或直接用 ES/Qdrant sparse；
# 这里 embedding 用 OpenAI text-embedding-3-small，换成千问 text-embedding-v4 的 client 写法见 02-models/01-openai.md 第 5 节
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

如果某一路明显更可信，可以给它加权：

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

多数情况下用不着，纯 RRF 已经够好。

---

## 6. 在向量库里做混合

### 6.1 Qdrant Hybrid（推荐）

Qdrant 1.10+ 的 Query API 支持 dense + sparse 一次查完：

```python
from qdrant_client.models import (
    Prefetch, FusionQuery, Fusion,
    SparseVector,
)   # NamedVector / NamedSparseVector 在新版 qdrant-client 里已移除，用 using="名字" 指定


# 假设 collection 同时有 dense 和 sparse vector
results = client.query_points(
    collection_name="hybrid_docs",
    prefetch=[
        Prefetch(query=dense_vec, using="dense", limit=20),
        Prefetch(query=SparseVector(indices=[...], values=[...]), using="sparse", limit=20),
    ],
    query=FusionQuery(fusion=Fusion.RRF),
    limit=5,
).points   # query_points 返回 QueryResponse，命中在 .points 里
```

### 6.2 Pinecone

```python
# Pinecone 的 query 本身没有 alpha 参数：先自己按权重缩放 dense / sparse 向量，再查询
alpha = 0.5   # 1=只 dense, 0=只 sparse
dense_scaled = [v * alpha for v in dense_vec]
sparse_scaled = {"indices": [...], "values": [v * (1 - alpha) for v in sparse_values]}
results = index.query(
    vector=dense_scaled,
    sparse_vector=sparse_scaled,
    top_k=5,
)   # 需要 dotproduct 度量的索引
```

### 6.3 Elasticsearch / OpenSearch

Elasticsearch 8.14 起用 retriever 写法做 RRF（早期的 `"rank": {"rrf": {}}` 写法已被取代）：

```json
{
  "retriever": {
    "rrf": {
      "retrievers": [
        {"standard": {"query": {"match": {"content": "用户的 query"}}}},
        {"knn": {"field": "embedding", "query_vector": [0.1, 0.2], "k": 20, "num_candidates": 100}}
      ]
    }
  }
}
```

OpenSearch 的做法不同：用 `hybrid` 查询，再在 search pipeline 里配归一化或 RRF 处理器，写法以 OpenSearch 文档为准。

---

## 7. Sparse vector 怎么来

有三种来源。

### 7.1 BM25 → sparse vector

```python
# 文本转 sparse vector：词表里每个词一个 index，这里只放词频（TF）。
# IDF 交给向量库算：Qdrant 给 sparse 向量配置 modifier=Modifier.IDF 后，会在服务端按语料统计 IDF
def bm25_to_sparse(text, bm25, vocab):
    tokens = list(jieba.cut(text))
    sparse = {}
    for token in tokens:
        if token in vocab:
            idx = vocab[token]
            sparse[idx] = sparse.get(idx, 0) + 1
    return sparse
```

自己算比较麻烦，一般用向量库提供的工具，或者直接用它托管的编码器（如 Pinecone 的 hosted sparse encoder）。

### 7.2 BGE-M3 自带

```python
from FlagEmbedding import BGEM3FlagModel


model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)


output = model.encode(
    sentences=[query],
    return_sparse=True,
)


sparse_dict = output["lexical_weights"][0]
# key 是 tokenizer 里的 token id（字符串），形如 {"6": 0.21, "15013": 0.35, ...}
# 想看对应的词：model.convert_id_to_token(output["lexical_weights"])
```

转成向量库的格式：token id 本身就是整数下标，不需要另建词表：

```python
# Qdrant
from qdrant_client.models import SparseVector

sparse = SparseVector(
    indices=[int(tid) for tid in sparse_dict],
    values=[float(w) for w in sparse_dict.values()],
)
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

SPLADE 会做词扩展，比 BM25 聪明一些，效果通常更好，代价是更慢。

---

## 8. RRF 能提升多少（示意）

下表是示意数据（设定为 200 条客服 evalset，没有固定到某个模型），和[重排 Pipeline](05-rerank-pipeline.md)第 11 节的示意表用同一组数：

| Retriever | Recall@5 |
|-----------|----------|
| Vector only | 85% |
| BM25 only | 75% |
| Hybrid (RRF) | 92% |
| Hybrid + rerank | 97% |

混合比最好的单路（向量）高 7 个点，比纯 BM25 高 17 个点；BM25 本身很便宜，多出来的成本几乎可以忽略。

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
| 分数不归一化直接相加 | 用 RRF |
| BM25 没分词或分错 | 中文用 jieba，英文 split |
| 纠结 RRF 的 k 要不要调 | k=60 一般不用动，要调也在 30-100 之间 |
| 每路召回太少（top_k=5） | 每路召回 20-50 条，融合后再取 top-5 |
| Sparse 和 Dense 维度不同 | 不能直接 concat，用向量库的 hybrid API |

---

## 11. 下一步

- 用 LLM 改写 query：[HyDE：用 LLM 生成假设答案](./03-hyde.md)
- 拆分复杂问题：[Multi-query / Sub-query 拆解](./04-multi-query.md)
- 混合召回之后再精排：[重排 Pipeline：召回 → 精排](./05-rerank-pipeline.md)
