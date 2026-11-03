# Qdrant：自托管向量库

> Qdrant 用 Rust 写成，API 直观，payload filter 能力强，单机、集群、嵌入式三种形态都能跑。自托管场景下，我默认推荐它。

以 `qdrant-client 1.20` 为准。`client.search()` 已移除，统一用 `client.query_points(collection_name=..., query=向量, using="向量名", limit=...)`，命中列表在返回值的 `.points` 里；命名向量改用 `using=`，`NamedVector` / `NamedSparseVector` 已移除。

---

## 1. 跑起来

### 1.1 Docker（最快）

```bash
docker run -p 6333:6333 -p 6334:6334 \
  -v $(pwd)/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

- 6333：REST API
- 6334：gRPC（更快）
- 数据持久化到 `qdrant_storage/`

打开 http://localhost:6333/dashboard 能看到内置的 Web UI。

### 1.2 Python 嵌入式（不起服务）

```python
from qdrant_client import QdrantClient


# In-memory（demo / 测试）
client = QdrantClient(":memory:")


# 本地文件
client = QdrantClient(path="./qdrant_local")
```

适合单元测试、notebook 和单机小项目。

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
                "created_at": 1778889600,   # 2026-05-16 的 unix 时间戳
            },
        ),
        # ...
    ],
)
```

ID 可以是 int 或 UUID 字符串，payload 可以是任意 JSON。

批量写入：

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
hits = client.query_points(
    collection_name="docs",
    query=[0.1, 0.2, ..., 0.9],
    limit=5,
    with_payload=True,
    with_vectors=False,
).points


for hit in hits:
    print(f"id={hit.id}  score={hit.score:.4f}  text={hit.payload['text']}")
```

---

## 5. Payload Filter（Qdrant 的强项）

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
    FieldCondition(key="created_at", range=Range(gte=1777593600)),   # 2026-05-01 之后
])


# 取反
filt = Filter(must_not=[
    FieldCondition(key="deleted", match=MatchValue(value=True)),
])


# 在 search 里
hits = client.query_points(
    collection_name="docs",
    query=[...],
    query_filter=filt,
    limit=5,
).points
```

---

## 6. Payload Index（加速 filter）

经常用来过滤的字段，建议建索引：

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

索引类型：

- `KEYWORD`：精确匹配
- `INTEGER` / `FLOAT`：范围查询
- `BOOL`
- `GEO`：地理坐标
- `TEXT`：全文匹配，只用于过滤、不打分（要 BM25 打分，用 sparse vector 配合 IDF modifier，见第 8 节）
- `DATETIME`

---

## 7. 多 Vector / Named Vectors

一个 Point 可以带多组 vector：

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
hits = client.query_points(
    collection_name="multi",
    query=[...], using="text",
    limit=5,
).points
```

同一个物品有多个 embedding 视角（文字、图片、标题、描述）时，用这种方式存。

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

详见 [BM25 + Dense 融合：RRF / Weighted](../05-retrieval/02-bm25-fusion.md)。

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

INT8 标量量化后，向量的内存占用降到原来的 1/4，查询速度通常还会更快；召回损失一般很小，查询时开 `rescore`（用原始向量重算候选的分数）可以再补回一部分。

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
        image: qdrant/qdrant:v1.16.0   # 示例，换成当前稳定 tag
        env:
        - name: QDRANT__CLUSTER__ENABLED
          value: "true"
        - name: QDRANT__CLUSTER__P2P__PORT
          value: "6335"
        ports:
        - containerPort: 6333
        - containerPort: 6335
```

Qdrant 集群内置 Raft，支持副本和分片。

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

## 12. 完整示例

```python
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


hits = client.query_points(
    collection_name=CN,
    query=q_emb,
    query_filter=Filter(must=[
        FieldCondition(key="category", match=MatchValue(value="billing")),
    ]),
    limit=3,
).points

for h in hits:
    print(f"  score={h.score:.4f}  {h.payload['text']}")
```

---

## 13. 常见坑

| 坑 | 解 |
|----|----|
| `:memory:` 重启后数据丢了 | 用 `path=...` 持久化 |
| filter 慢 | 给 filter 字段建 payload index |
| 内存吃紧 | 开 quantization |
| 多副本时写入慢 | 调 `write_consistency_factor` |

---

## 14. 下一步

- [pgvector：在 Postgres 里做向量检索](./04-pgvector.md)
- [Chroma / LanceDB：嵌入式向量库](./05-chroma-lancedb.md)
- 索引原理：[HNSW / IVF / PQ 索引原理](./06-index-algorithms.md)
