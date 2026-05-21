# Pinecone：托管向量库代表

> **一句话**：Pinecone 是行业最知名的托管向量库——零运维、按量计费、`pip install pinecone` 就能跑生产，适合不想自建集群的团队。

---

## 1. 注册 + 拿 key

去 https://www.pinecone.io/ 注册（免费 tier 够开发用），拿到：

- `PINECONE_API_KEY`

```bash
pip install pinecone
```

---

## 2. Serverless vs Pod

**Serverless**（推荐起步）：

- 按 query / storage 计费
- 自动伸缩
- 冷启动延迟稍高
- 适合：流量不稳 / 中小项目

**Pod-based**：

- 包月固定 pod
- 延迟稳定
- 适合：流量稳 / 大项目

```python
from pinecone import Pinecone, ServerlessSpec, PodSpec


pc = Pinecone(api_key="...")


# Serverless
pc.create_index(
    name="my-index",
    dimension=1536,
    metric="cosine",
    spec=ServerlessSpec(cloud="aws", region="us-east-1"),
)


# Pod-based
pc.create_index(
    name="my-index-pod",
    dimension=1536,
    metric="cosine",
    spec=PodSpec(environment="us-east-1-aws", pod_type="s1.x1"),
)
```

---

## 3. 写入 (upsert)

```python
index = pc.Index("my-index")


index.upsert(vectors=[
    {
        "id": "doc_1",
        "values": [0.1, 0.2, ..., 0.9],     # 长度 = dimension
        "metadata": {
            "category": "billing",
            "lang": "zh",
            "created_at": 1715817600,
            "tags": ["refund", "subscription"],
        },
    },
    # ...
])
```

- ID 是字符串，最长 512 字符
- Metadata 任意 JSON（但有大小限制 40KB）

批量：

```python
def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


for batch in chunked(my_vectors, 100):
    index.upsert(vectors=batch)
```

**推荐 batch 100-200**（太大 timeout，太小慢）。

---

## 4. 查询 (query)

```python
results = index.query(
    vector=[0.1, 0.2, ..., 0.9],
    top_k=5,
    include_metadata=True,
    include_values=False,    # 是否返回 vector 本身（一般不要）
)


for match in results.matches:
    print(f"id={match.id}  score={match.score:.4f}  meta={match.metadata}")
```

---

## 5. Metadata Filter

```python
results = index.query(
    vector=[...],
    top_k=5,
    filter={
        "category": "billing",                          # equal
        "lang": {"$in": ["zh", "en"]},                  # in
        "created_at": {"$gte": 1715000000},             # range
    },
    include_metadata=True,
)
```

支持操作符：

- `$eq` / `$ne`
- `$gt` / `$gte` / `$lt` / `$lte`
- `$in` / `$nin`
- `$and` / `$or`

类似 MongoDB。

---

## 6. Namespace（多租户）

```python
# 不同 namespace 隔离
index.upsert(vectors=[...], namespace="user_42")
index.upsert(vectors=[...], namespace="user_99")


results = index.query(
    vector=[...],
    top_k=5,
    namespace="user_42",  # 只在这个 namespace 找
)
```

**用法**：

- 多租户应用：每个用户 / 团队一个 namespace
- 多版本数据：v1 / v2
- 多领域：billing / support / sales

---

## 7. 删除

```python
index.delete(ids=["doc_1", "doc_2"])
index.delete(delete_all=True, namespace="user_42")  # 清空 namespace

# 按 filter 删除（仅 Pod-based）
index.delete(filter={"category": "deleted"})
```

---

## 8. Sparse + Dense（混合检索）

Pinecone 支持稀疏向量（BM25-like）：

```python
results = index.query(
    vector=dense_vec,            # dense embedding
    sparse_vector={
        "indices": [10, 45, 67],
        "values": [0.5, 0.3, 0.8],
    },
    top_k=5,
)
```

详见 [05-retrieval/02-bm25-fusion.md](../05-retrieval/02-bm25-fusion.md)。

---

## 9. 拿 stats

```python
stats = index.describe_index_stats()
print(stats.total_vector_count)
print(stats.dimension)
print(stats.namespaces)  # 各 namespace 的 vector 数
```

监控用得到。

---

## 10. Pinecone Inference（hosted embedding）

Pinecone 2024 起也提供托管 embedding：

```python
# 直接用 Pinecone 的 hosted model embed + 索引
records = [
    {"id": "1", "text": "我的内容", "metadata": {...}},
    {"id": "2", "text": "另一段", "metadata": {...}},
]

index.upsert_records(
    namespace="default",
    records=records,
    # Pinecone 自动 embed 这些 text
)
```

跟 OpenAI Embeddings 没本质区别，但少一个 API hop。

---

## 11. 性能 / 限流

| Tier | 单实例 QPS | Storage |
|------|------------|---------|
| Starter (free) | ~10 | ~100K vec |
| Standard | ~100s | 任意 |
| Enterprise | 自定义 | 任意 |

**实战**：

- Free tier 学习够，生产至少 Standard
- 高 QPS 用 Pod-based（latency 稳）
- 异常 → Pinecone status page

---

## 12. 完整 demo

```python
# demos/vector_db/02_pinecone.py
import os
import time
from pinecone import Pinecone, ServerlessSpec
from openai import OpenAI


pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
oai = OpenAI()


INDEX_NAME = "demo-rag"


# 1. 创建索引（首次）
if INDEX_NAME not in [i.name for i in pc.list_indexes()]:
    pc.create_index(
        name=INDEX_NAME,
        dimension=1536,
        metric="cosine",
        spec=ServerlessSpec(cloud="aws", region="us-east-1"),
    )
    while not pc.describe_index(INDEX_NAME).status["ready"]:
        time.sleep(1)


index = pc.Index(INDEX_NAME)


# 2. embed + upsert
docs = [
    ("doc_1", "如何关闭自动续费", {"category": "billing"}),
    ("doc_2", "如何登录账号", {"category": "auth"}),
    ("doc_3", "停止订阅的方法", {"category": "billing"}),
    ("doc_4", "重置密码教程", {"category": "auth"}),
]


texts = [d[1] for d in docs]
embs = [d.embedding for d in oai.embeddings.create(model="text-embedding-3-small", input=texts).data]


vectors = [
    {"id": doc_id, "values": emb, "metadata": {**meta, "text": text}}
    for (doc_id, text, meta), emb in zip(docs, embs)
]

index.upsert(vectors=vectors)


# 3. query
q = "如何取消订阅"
q_emb = oai.embeddings.create(model="text-embedding-3-small", input=[q]).data[0].embedding


results = index.query(
    vector=q_emb,
    top_k=3,
    include_metadata=True,
    filter={"category": "billing"},
)

for m in results.matches:
    print(f"  score={m.score:.4f}  text={m.metadata['text']}")
```

---

## 13. 常见坑

| 坑 | 解 |
|----|----|
| Serverless 冷启动 latency 高 | 预热（定期 query） / Pod-based |
| Metadata 超 40KB | 不要在 metadata 里塞全文，只放 ID + 关键字段 |
| 频繁 update 同 id | upsert 替换整个 vec + metadata |
| 跨 region | latency 高，部署在用户近的 region |

---

## 14. 下一步

- 📖 Qdrant 自托管 → [03-qdrant.md](./03-qdrant.md)
- 📖 pgvector → [04-pgvector.md](./04-pgvector.md)
- 📖 索引原理 → [06-index-algorithms.md](./06-index-algorithms.md)
