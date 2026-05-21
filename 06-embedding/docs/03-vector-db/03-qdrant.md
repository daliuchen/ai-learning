# Qdrant：自托管向量库首选

> **一句话**：Qdrant 用 Rust 写的、API 直观、payload filter 强、单机 / 集群 / 嵌入式都能跑——是自托管场景默认推荐。

---

## 1. 跑起来

### 1.1 Docker（最快）

```bash
docker run -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

- 6333: REST API
- 6334: gRPC（更快）
- 数据持久化到 `qdrant_storage/`

打开 http://localhost:6333/dashboard 是内置 Web UI。

### 1.2 Python 嵌入式（无服务）

```python
from qdrant_client import QdrantClient


# In-memory（demo / 测试）
client = QdrantClient(":memory:")


# 本地文件
client = QdrantClient(path="./qdrant_local")
```

适合：单元测试、笔记本、单机小项目。

---

## 2. 创建 Collection

```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams


client = QdrantClient(url="http://localhost:6333")


client.create_collection(
    collection_name="docs",
    vectors_config=VectorParams(
        size=1024,
        distance=Distance.COSINE,
    ),
)
```

支持的距离：

- `Distance.COSINE`（最常用）
- `Distance.DOT`
- `Distance.EUCLID`
- `Distance.MANHATTAN`

---

## 3. 写入 Points

```python
from qdrant_client.models import PointStruct


client.upsert(
    collection_name="docs",
    points=[
        PointStruct(
            id=1,
            vector=[0.1, 0.2, ..., 0.9],
            payload={
                "text": "如何取消订阅",
                "category": "billing",
                "lang": "zh",
                "tags": ["refund", "subscription"],
                "created_at": 1715817600,
            },
        ),
        # ...
    ],
)
```

- ID 可以是 int 或 UUID 字符串
- payload 任意 JSON

批量：

```python
batch_points = []
for i, (text, embedding) in enumerate(zip(texts, embeddings)):
    batch_points.append(PointStruct(
        id=i,
        vector=embedding,
        payload={"text": text},
    ))

# 一次最多几百到几千
client.upsert(collection_name="docs", points=batch_points)
```

---

## 4. 查询

```python
hits = client.search(
    collection_name="docs",
    query_vector=[0.1, 0.2, ..., 0.9],
    limit=5,
    with_payload=True,
    with_vectors=False,
)


for hit in hits:
    print(f"id={hit.id}  score={hit.score:.4f}  text={hit.payload['text']}")
```

---

## 5. Payload Filter（Qdrant 强项）

```python
from qdrant_client.models import Filter, FieldCondition, MatchValue, Range


# 简单 equal
filt = Filter(must=[
    FieldCondition(key="category", match=MatchValue(value="billing")),
])


# 多条件 AND
filt = Filter(must=[
    FieldCondition(key="category", match=MatchValue(value="billing")),
    FieldCondition(key="lang", match=MatchValue(value="zh")),
])


# OR
filt = Filter(should=[
    FieldCondition(key="category", match=MatchValue(value="billing")),
    FieldCondition(key="category", match=MatchValue(value="support")),
])


# Range
filt = Filter(must=[
    FieldCondition(key="created_at", range=Range(gte=1715000000)),
])


# 取反
filt = Filter(must_not=[
    FieldCondition(key="deleted", match=MatchValue(value=True)),
])


# 在 search 里
hits = client.search(
    collection_name="docs",
    query_vector=[...],
    query_filter=filt,
    limit=5,
)
```

---

## 6. Payload Index（加速 filter）

filter 字段建议建索引：

```python
from qdrant_client.models import PayloadSchemaType


client.create_payload_index(
    collection_name="docs",
    field_name="category",
    field_schema=PayloadSchemaType.KEYWORD,
)


client.create_payload_index(
    collection_name="docs",
    field_name="created_at",
    field_schema=PayloadSchemaType.INTEGER,
)
```

类型：

- `KEYWORD`：精确 match
- `INTEGER` / `FLOAT`：range
- `BOOL`
- `GEO`：地理坐标
- `TEXT`：全文搜（也支持 BM25）
- `DATETIME`

---

## 7. 多 Vector / Named Vectors

一个 Point 可以有多组 vector：

```python
from qdrant_client.models import VectorParams


client.create_collection(
    collection_name="multi",
    vectors_config={
        "text": VectorParams(size=1024, distance=Distance.COSINE),
        "image": VectorParams(size=512, distance=Distance.COSINE),
    },
)


client.upsert(
    collection_name="multi",
    points=[
        PointStruct(
            id=1,
            vector={
                "text": [...],   # 1024 维
                "image": [...],  # 512 维
            },
            payload={"name": "product 1"},
        ),
    ],
)


# 按 text vector 查
hits = client.search(
    collection_name="multi",
    query_vector=("text", [...]),
    limit=5,
)
```

适合：同一物品有多个 embedding 视角（文 / 图 / 标题 / 描述）。

---

## 8. Sparse + Dense 混合

```python
from qdrant_client.models import SparseVector, SparseVectorParams


client.create_collection(
    collection_name="hybrid",
    vectors_config={
        "dense": VectorParams(size=1024, distance=Distance.COSINE),
    },
    sparse_vectors_config={
        "sparse": SparseVectorParams(),
    },
)


client.upsert(
    collection_name="hybrid",
    points=[
        PointStruct(
            id=1,
            vector={
                "dense": [...],
                "sparse": SparseVector(
                    indices=[10, 45, 67],
                    values=[0.5, 0.3, 0.8],
                ),
            },
            payload={"text": "..."},
        ),
    ],
)
```

详见 [05-retrieval/02-bm25-fusion.md](../05-retrieval/02-bm25-fusion.md)。

---

## 9. Quantization（量化省内存）

```python
from qdrant_client.models import ScalarQuantization, ScalarType


client.create_collection(
    collection_name="quant",
    vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
    quantization_config=ScalarQuantization(
        scalar={"type": ScalarType.INT8, "quantile": 0.99, "always_ram": True},
    ),
)
```

- 内存占用 × 0.25
- 召回率掉 1-3%
- 速度提升

---

## 10. 集群部署

```yaml
# qdrant-cluster.yaml (k8s)
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: qdrant
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: qdrant
        image: qdrant/qdrant:v1.10.0
        env:
        - name: QDRANT__CLUSTER__ENABLED
          value: "true"
        - name: QDRANT__CLUSTER__P2P__PORT
          value: "6335"
        ports:
        - containerPort: 6333
        - containerPort: 6335
```

Qdrant 集群内置 Raft，副本 + 分片。

---

## 11. Snapshot 备份

```python
# 创建 snapshot
snapshot = client.create_snapshot(collection_name="docs")
print(snapshot.name)


# 列出
client.list_snapshots(collection_name="docs")


# 恢复（新 collection）
client.recover_snapshot(
    collection_name="docs_restored",
    location=f"http://qdrant-source:6333/collections/docs/snapshots/{snapshot.name}",
)
```

---

## 12. 完整 demo

```python
# demos/vector_db/03_qdrant.py
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue,
)
from openai import OpenAI


client = QdrantClient(":memory:")  # in-memory for demo
oai = OpenAI()


CN = "docs"
client.create_collection(
    collection_name=CN,
    vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
)


docs = [
    ("如何关闭自动续费", "billing"),
    ("如何登录账号", "auth"),
    ("停止订阅的方法", "billing"),
    ("重置密码教程", "auth"),
    ("退款流程", "billing"),
]


texts = [d[0] for d in docs]
embs = [d.embedding for d in oai.embeddings.create(model="text-embedding-3-small", input=texts).data]


client.upsert(
    collection_name=CN,
    points=[
        PointStruct(id=i, vector=emb, payload={"text": text, "category": cat})
        for i, ((text, cat), emb) in enumerate(zip(docs, embs))
    ],
)


# 只在 billing 类别里搜
q = "如何取消订阅"
q_emb = oai.embeddings.create(model="text-embedding-3-small", input=[q]).data[0].embedding


hits = client.search(
    collection_name=CN,
    query_vector=q_emb,
    query_filter=Filter(must=[
        FieldCondition(key="category", match=MatchValue(value="billing")),
    ]),
    limit=3,
)

for h in hits:
    print(f"  score={h.score:.4f}  {h.payload['text']}")
```

---

## 13. 常见坑

| 坑 | 解 |
|----|----|
| `:memory:` 重启丢数据 | 用 `path=...` 持久化 |
| filter 慢 | 给 filter 字段建 payload index |
| 内存吃紧 | 上 quantization |
| 多副本 写入慢 | 调 `write_consistency_factor` |

---

## 14. 下一步

- 📖 pgvector → [04-pgvector.md](./04-pgvector.md)
- 📖 Chroma / LanceDB → [05-chroma-lancedb.md](./05-chroma-lancedb.md)
- 📖 索引原理 → [06-index-algorithms.md](./06-index-algorithms.md)
