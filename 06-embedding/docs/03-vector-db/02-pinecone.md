# Pinecone：托管向量库

> Pinecone 是知名度最高的托管向量库：不用运维，按量计费，`pip install pinecone` 之后就能接生产。不想自己搭集群的团队，可以先看它。

版本：以 `pinecone 10.x`（2026 年 11 月）为准，`create_index` / `Index` / `ServerlessSpec` 用法不变；运行需要 Pinecone 账号。

---

## 1. 注册并拿到 key

去 https://www.pinecone.io/ 注册（免费档开发够用），拿到：

- `PINECONE_API_KEY`

```bash
pip install pinecone      # 包名是 pinecone；老包 pinecone-client 已改名停更，不要再装
```

---

## 2. Serverless vs Pod

Serverless 是起步时的推荐选项：按 query 和存储计费，自动伸缩，冷启动延迟稍高，适合流量不稳定的中小项目。

Pod-based 已经是 legacy：包月固定 pod，延迟稳定。官方文档写明，2025-08-18 起新注册的 Standard / Enterprise 客户不能再创建 pod 索引；对齐 API 版本 2026-07 的 SDK 也不再支持创建 pod 索引（SDK 里 `PodSpec` 类还在，是为了管理存量）。新项目直接用 serverless。

```python
from pinecone import Pinecone, ServerlessSpec


pc = Pinecone(api_key="...")


# Serverless（唯一推荐）
pc.create_index(
    name="my-index",
    dimension=1536,
    metric="cosine",
    spec=ServerlessSpec(cloud="aws", region="us-east-1"),
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
            "created_at": 1778889600,         # 2026-05-16 的 unix 时间戳
            "tags": ["refund", "subscription"],
        },
    },
    # ...
])
```

ID 是字符串，最长 512 字符；metadata 可以是任意 JSON，但上限 40KB。

批量写入：

```python
def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


for batch in chunked(my_vectors, 100):
    index.upsert(vectors=batch)
```

batch 建议 100-200 条，太大容易超时，太小又慢。

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
        "created_at": {"$gte": 1777593600},             # range：2026-05-01 之后
    },
    include_metadata=True,
)
```

支持的操作符：

- `$eq` / `$ne`
- `$gt` / `$gte` / `$lt` / `$lte`
- `$in` / `$nin`
- `$and` / `$or`

写法和 MongoDB 差不多。

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

namespace 常见的用法有三种：多租户应用里每个用户或团队一个；区分数据版本，比如 v1 / v2；按领域分开，比如 billing / support / sales。

---

## 7. 删除

```python
index.delete(ids=["doc_1", "doc_2"])
index.delete(delete_all=True, namespace="user_42")  # 清空 namespace

# 按 metadata filter 删除：早期只有 pod 索引支持，serverless 是否支持请查当前官方文档；
# 不支持时先按 filter 查出 ID 再按 ID 删
index.delete(filter={"category": "deleted"})
```

---

## 8. Sparse + Dense（混合检索）

Pinecone 支持稀疏向量（类似 BM25）：

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

详见 [BM25 + Dense 融合：RRF / Weighted](../05-retrieval/02-bm25-fusion.md)。

---

## 9. 查看索引统计

```python
stats = index.describe_index_stats()
print(stats.total_vector_count)
print(stats.dimension)
print(stats.namespaces)  # 各 namespace 的 vector 数
```

做监控时会用到。

---

## 10. Pinecone Inference（托管 embedding）

Pinecone 也提供托管 embedding（integrated embedding，用 `pc.create_index_for_model(...)` 建索引）。官方文档现在把这套 Records API 叫作 legacy integrated index，又推出了新的 document index（声明带 `embed` 的 string 字段，配合 Documents API）。细节还在变，下面只演示 records 写法的思路：

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

流程上相当于把"先调 embedding API 再写入"合成一步，少一次 API 往返；检索质量取决于你选的托管模型，要和自己现有的 embedding 在 evalset 上比一下。

---

## 11. 性能 / 限流

免费的 Starter 档有存储容量和每月读写单元的配额，用来学习够了；生产至少要 Standard 档，按存储和读写单元计费，各档的配额和限流数字以官方定价页和 limits 文档为准。高 QPS 场景先在 serverless 上压测，评估后再看官方的专用读节点等选项（以官方文档为准）。遇到异常，先去看 Pinecone 的 status page。

---

## 12. 完整示例

```python
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
| Serverless 冷启动 latency 高 | 定期发 query 预热；pod 索引已是 legacy，不推荐 |
| Metadata 超 40KB | 别把全文塞进 metadata，只放 ID 和关键字段 |
| 频繁 update 同一个 id | upsert 会整体替换 vector 和 metadata |
| 跨 region 访问 | 延迟高，把索引部署在离用户近的 region |

---

## 14. 下一步

- 自托管方案：[Qdrant：自托管向量库](./03-qdrant.md)
- [pgvector：在 Postgres 里做向量检索](./04-pgvector.md)
- 索引原理：[HNSW / IVF / PQ 索引原理](./06-index-algorithms.md)
