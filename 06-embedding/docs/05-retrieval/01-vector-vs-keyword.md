# 纯向量 vs 关键词 vs 混合

> **一句话**：纯向量擅长语义相似（"取消订阅" ≈ "停止续费"），关键词擅长精确匹配（产品名、错误码、人名）——**生产上几乎一定是混合**。

---

## 1. 三种检索的本质

| | 纯向量 (Dense) | 关键词 (Sparse / BM25) | 混合 |
|---|---|---|---|
| 匹配啥 | 语义 | 词面 | 两者 |
| 同义词 | ✅ 强 | ❌ 弱 | ✅ |
| 拼写错 | ⚠️ 一般 | ❌ 完全错 | ⚠️ |
| 专有名词 | ❌ 弱 | ✅ 强（精确）| ✅ |
| 错误码 / ID | ❌ 完全错 | ✅ 强 | ✅ |
| 长文档检索 | ✅ | ⚠️ 容易 noise | ✅ |
| 短 query 检索短文档 | ✅ | ✅ | ✅ |
| 实现成本 | 中（要 embed + 向量库） | 低（Lucene / Elastic） | 中-高 |

---

## 2. 啥时候纯向量翻车

### 例 1：错误码

```
Query: "ERR_TIMEOUT_500"

文档：
- "ERR_TIMEOUT_500 表示后端响应超时"  ← 应该召回
- "网络连接很慢" 
- "服务器没响应"

纯向量：可能召回"网络连接很慢"（语义近），漏掉精确匹配
BM25：精准命中 ERR_TIMEOUT_500
```

### 例 2：型号 / 序列号

```
Query: "iPhone 15 Pro Max"

文档：
- "iPhone 15 Pro Max 256GB 规格"  ← 必须召回
- "苹果旗舰手机性能介绍"

纯向量：可能召回模糊文档
BM25：精准命中
```

### 例 3：罕见名词

```
Query: "怎么用 kubectl exec 进入 pod"

文档：
- "kubectl exec -it pod-name -- bash"  ← 必须召回
- "Kubernetes 容器管理基础"

纯向量：可能召回基础文章
BM25：精准命中 kubectl exec
```

---

## 3. 啥时候 BM25 翻车

### 例 1：同义改写

```
Query: "怎么停止自动扣费"

文档：
- "取消订阅的步骤"           ← 应该召回
- "查看历史扣费记录"          ← 应该不召回

BM25：找不到（"停止自动扣费" 跟 "取消订阅" 词面不同）
纯向量：召回成功
```

### 例 2：跨语言

```
Query (中文): "怎么部署到 AWS"

文档（英文）: "Deploy your app to AWS in 5 minutes"

BM25：无法跨语言
纯向量（multilingual）：召回成功
```

---

## 4. BM25 是啥

> Best Matching 25

经典关键词检索算法：

```
score(D, Q) = Σ IDF(qi) × (f(qi,D) × (k1 + 1)) / (f(qi,D) + k1 × (1 - b + b × |D|/avgdl))
```

直觉：

- 包含 query 词的文档得分高
- 罕见词权重大（IDF）
- 文档越长越难得高分（normalize）

Elasticsearch / OpenSearch / Lucene / Tantivy 都内置 BM25。

---

## 5. 实战代码：rank-bm25（Python）

```python
# pip install rank-bm25
from rank_bm25 import BM25Okapi


corpus = [
    "ERR_TIMEOUT_500 表示后端响应超时",
    "如何取消订阅 - 登录后进入设置",
    "kubectl exec -it pod 进入容器",
    "网络连接慢的常见原因",
    "停止自动续费的方法",
]


# tokenize（中文要分词，英文 split() 就行）
def tokenize(text):
    # 简单分词，生产用 jieba
    import jieba
    return list(jieba.cut(text))


tokenized = [tokenize(doc) for doc in corpus]
bm25 = BM25Okapi(tokenized)


query = tokenize("怎么取消订阅")
scores = bm25.get_scores(query)


for doc, score in sorted(zip(corpus, scores), key=lambda x: -x[1])[:3]:
    print(f"  score={score:.4f}  {doc}")
```

---

## 6. 直接两路对比 demo

```python
# demos/retrieval/01_vector_vs_bm25.py
import numpy as np
from openai import OpenAI
from rank_bm25 import BM25Okapi
import jieba


client = OpenAI()


def embed(texts):
    resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return np.array([d.embedding for d in resp.data])


corpus = [
    "ERR_TIMEOUT_500 表示后端响应超时",
    "如何取消订阅 - 登录后进入设置",
    "kubectl exec -it pod 进入容器",
    "网络连接慢的常见原因",
    "停止自动续费的方法",
    "API 调用速率限制说明",
    "用户账户安全设置",
]


# Setup
corpus_vecs = embed(corpus)
tokenized = [list(jieba.cut(doc)) for doc in corpus]
bm25 = BM25Okapi(tokenized)


def search_vector(query, top_k=3):
    q_vec = embed([query])[0]
    scores = corpus_vecs @ q_vec
    top = np.argsort(-scores)[:top_k]
    return [(corpus[i], float(scores[i])) for i in top]


def search_bm25(query, top_k=3):
    q_tokens = list(jieba.cut(query))
    scores = bm25.get_scores(q_tokens)
    top = np.argsort(-scores)[:top_k]
    return [(corpus[i], float(scores[i])) for i in top]


for q in [
    "ERR_TIMEOUT_500 是什么",        # 含错误码 → BM25 强
    "怎么停止扣费",                  # 同义 → vector 强
    "kubectl exec",                  # 命令 → BM25 强
    "为什么访问这么慢",              # 描述 → vector 强
]:
    print(f"\n=== Query: {q} ===")
    print("[Vector]")
    for doc, s in search_vector(q):
        print(f"  {s:.4f}  {doc}")
    print("[BM25]")
    for doc, s in search_bm25(q):
        print(f"  {s:.4f}  {doc}")
```

跑一下能直观看到两种检索的强弱场景。

---

## 7. 评测：单独算 Recall

```python
def recall_at_k(retriever_fn, evalset, k=5):
    hits = 0
    for case in evalset:
        results = retriever_fn(case["query"], top_k=k)
        result_docs = [r[0] for r in results]
        if any(rel_doc in result_docs for rel_doc in case["relevant_docs"]):
            hits += 1
    return hits / len(evalset)


vec_recall = recall_at_k(search_vector, evalset, k=5)
bm25_recall = recall_at_k(search_bm25, evalset, k=5)


print(f"Vector Recall@5: {vec_recall:.3f}")
print(f"BM25   Recall@5: {bm25_recall:.3f}")
```

典型实测（客服 KB 200 条 evalset）：

- 纯向量：~85%
- 纯 BM25：~75%
- 混合：~92%

---

## 8. 啥时候用什么

```
你的 query 包含 ID / 代码 / 错误码 / 型号？
  → 必须有 BM25
  → 不要纯向量

你的 query 是自然语言问句？
  → 向量是基础
  → 加 BM25 进一步提升

你的数据是结构化（产品名 / 标签）？
  → BM25 / Elasticsearch 通常更准

你的数据是非结构化自然语言？
  → 向量 + 混合

跨语言检索？
  → 必须向量（multilingual）
  → BM25 没用
```

**几乎所有生产 RAG**都该用混合。详见 [02-bm25-fusion.md](./02-bm25-fusion.md)。

---

## 9. 混合的两种实现

1. **向量库内置 sparse**（Pinecone / Qdrant / Weaviate）

   ```python
   index.query(vector=dense, sparse_vector=sparse, top_k=5)
   ```

2. **外部 BM25 + 向量召回 → 融合**

   ```python
   vec_hits = vector_db.search(query)
   bm25_hits = bm25_index.search(query)
   merged = rrf_fusion(vec_hits, bm25_hits)
   ```

详见 [02-bm25-fusion.md](./02-bm25-fusion.md)。

---

## 10. 何时不用混合

```
纯专有名词检索（电商搜商品名）→ BM25 / Elasticsearch 就够
全是自然语言问答 + 强 embed → 单纯向量可能够（但加上更好）
原型 / demo → 纯向量先跑起来
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| 中文 BM25 不分词 | 用 jieba / pkuseg / hanlp |
| 直接 raw score 加权 | 尺度不同（vec 0-1, BM25 任意），用 RRF |
| 全靠向量找错误码 | 必加 BM25 |
| BM25 没去停用词 | 英文加 stopwords，中文 jieba 自带 |

---

## 12. 下一步

- 📖 BM25 + Dense 怎么融合 → [02-bm25-fusion.md](./02-bm25-fusion.md)
- 📖 HyDE：用 LLM 生成假设答案 → [03-hyde.md](./03-hyde.md)
- 📖 Multi-query / Sub-query → [04-multi-query.md](./04-multi-query.md)
