# 向量库选型全景 + 决策树

> **一句话**：**已有 Postgres → pgvector，要托管 → Pinecone/Qdrant Cloud，要自部署 → Qdrant，本地嵌入式 → Chroma/LanceDB**。没有"最好"，只有"最适合你团队 / 场景"。

---

## 1. 主流向量库分类

| 类型 | 代表 | 特点 |
|------|------|------|
| **托管 SaaS** | Pinecone / Qdrant Cloud / Weaviate Cloud / Zilliz Cloud | 不用运维 |
| **Self-hosted** | Qdrant / Weaviate / Milvus / Vespa | 自建集群 |
| **Postgres 扩展** | **pgvector** / pgvecto.rs | 用现有 PG |
| **嵌入式 / 文件** | Chroma / LanceDB / Faiss | 本地用 |
| **Elasticsearch 系** | Elasticsearch / OpenSearch | 已有 ES 接 |
| **专用** | Milvus / Vald | 大规模分布式 |

---

## 2. 速选表

| 场景 | 推荐 | 备注 |
|------|------|------|
| 已有 Postgres，量 < 1000 万 | **pgvector** | 最省事，0 额外组件 |
| 想最快上线（云托管） | **Pinecone** | $50/月起 |
| 自部署 + 多功能（payload filter 等） | **Qdrant** | docker run 即用 |
| 巨量（亿级） + 分布式 | **Milvus** / **Vespa** | 复杂但能扛 |
| 笔记本 / 单机 demo | **Chroma** / **LanceDB** | embedded |
| 已有 ES 集群 | **OpenSearch / ES knn_vector** | 复用 |
| 想要 incremental query update | **Vespa** / **Weaviate** | 更新友好 |

---

## 3. 三大维度对比

| | Pinecone | Qdrant | Weaviate | Milvus | pgvector | Chroma |
|---|---|---|---|---|---|---|
| 部署 | 托管 | 自/云 | 自/云 | 自/云 | 已有 PG | 自/嵌入 |
| 索引算法 | 专有 | HNSW / IVF | HNSW | HNSW / IVF / DiskANN | HNSW / IVFFlat | HNSW |
| Metadata filter | ✅ | ✅✅（强） | ✅ | ✅ | ✅✅（SQL） | ✅ |
| 多向量 / 多模态 | ✅ | ✅ | ✅ | ✅ | 自管 | ⚠️ |
| Sparse + Dense 混合 | ✅ | ✅ | ✅ | ✅ | 需自拼 | ⚠️ |
| Geo / Range | ⚠️ | ✅ | ✅ | ⚠️ | ✅ | ⚠️ |
| 上手难度 | 极低 | 低 | 低-中 | 中-高 | 极低（PG 用户） | 极低 |
| 量级（推荐） | 任意 | 千万-亿 | 千万 | 亿+ | 千万内 | 百万内 |
| 价格 | 中-高 | 低 | 中 | 低（开源） | 几乎免费 | 免费 |

---

## 4. 选型决策树

```
你有 Postgres 吗？
├─ 有，量 < 1000 万
│   → pgvector（详见 [04-pgvector.md](./04-pgvector.md)）
│
└─ 没有 / 量大

  团队有人能运维吗？
  ├─ 不想运维
  │   ├─ 简单需求 → Pinecone
  │   └─ 要 filter / 多向量 → Qdrant Cloud
  │
  └─ 能运维
      ├─ 量 < 1 亿 → Qdrant self-hosted（详见 [03-qdrant.md](./03-qdrant.md)）
      ├─ 量 > 1 亿 → Milvus / Vespa
      └─ 已有 ES → ES knn_vector
```

---

## 5. Pinecone：托管最省心

```python
from pinecone import Pinecone, ServerlessSpec


pc = Pinecone(api_key="...")


pc.create_index(
    name="my-index",
    dimension=1536,
    metric="cosine",
    spec=ServerlessSpec(cloud="aws", region="us-east-1"),
)


index = pc.Index("my-index")


# 写
index.upsert(vectors=[
    {"id": "1", "values": [...], "metadata": {"category": "billing"}},
    {"id": "2", "values": [...], "metadata": {"category": "support"}},
])


# 查
results = index.query(
    vector=[...],
    top_k=5,
    filter={"category": "billing"},
    include_metadata=True,
)
```

详见 [02-pinecone.md](./02-pinecone.md)。

---

## 6. Qdrant：自托管首选

```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition


client = QdrantClient(url="http://localhost:6333")


client.create_collection(
    collection_name="docs",
    vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
)


# 写
client.upsert(collection_name="docs", points=[
    PointStruct(id=1, vector=[...], payload={"category": "billing"}),
    PointStruct(id=2, vector=[...], payload={"category": "support"}),
])


# 查
hits = client.search(
    collection_name="docs",
    query_vector=[...],
    limit=5,
    query_filter=Filter(must=[FieldCondition(key="category", match={"value": "billing"})]),
)
```

详见 [03-qdrant.md](./03-qdrant.md)。

---

## 7. pgvector：已有 PG 最划算

```sql
CREATE EXTENSION vector;

CREATE TABLE docs (
    id SERIAL PRIMARY KEY,
    content TEXT,
    category TEXT,
    embedding vector(1024)
);

CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops);
```

```python
# pgvector + psycopg
from pgvector.psycopg import register_vector
import psycopg


conn = psycopg.connect("postgresql://...")
register_vector(conn)


with conn.cursor() as cur:
    cur.execute(
        "INSERT INTO docs (content, category, embedding) VALUES (%s, %s, %s)",
        ("文档内容", "billing", [0.1, 0.2, ...]),
    )
    conn.commit()

    cur.execute(
        "SELECT id, content FROM docs WHERE category = %s ORDER BY embedding <=> %s LIMIT 5",
        ("billing", [0.1, 0.2, ...]),
    )
    for row in cur.fetchall():
        print(row)
```

详见 [04-pgvector.md](./04-pgvector.md)。

---

## 8. Chroma：开发 / 笔记本

```python
import chromadb


client = chromadb.PersistentClient(path="./chroma_db")


collection = client.get_or_create_collection(name="docs")


collection.add(
    documents=["doc1 text", "doc2 text"],
    metadatas=[{"category": "billing"}, {"category": "support"}],
    ids=["1", "2"],
    embeddings=[[...], [...]],  # 或不传，让 Chroma 用默认 embed 函数
)


results = collection.query(
    query_embeddings=[[...]],
    n_results=5,
    where={"category": "billing"},
)
```

详见 [05-chroma-lancedb.md](./05-chroma-lancedb.md)。

---

## 9. 数据量级 vs 工具

```
< 10 万   → 任何工具，单机够
10万-100万 → Chroma / pgvector / Qdrant 都行
100万-1000万 → Qdrant / Pinecone / pgvector(注意调优)
1000万-1亿 → Qdrant / Pinecone / Milvus
> 1亿     → Milvus / Vespa / 大型 ES 集群
```

---

## 10. 成本估算

实际成本（1000 万条 × 1024 维）：

| 方案 | 月 cost |
|------|---------|
| Pinecone Serverless | ~$200-400 |
| Qdrant Cloud | ~$300-500 |
| 自部署 Qdrant (1 台 32G/8C) | ~$80（云主机）|
| 自部署 Qdrant (3 节点) | ~$240 |
| pgvector on RDS db.m5.xlarge | ~$300（含 PG 数据）|
| Chroma（单机） | 几乎免费 |

随着量变大，**自部署成本优势显著**。

---

## 11. 容灾 / 备份

| 工具 | 备份 | 副本 |
|------|------|------|
| Pinecone | 自动 | 自动 |
| Qdrant | snapshot API | 集群副本 |
| Milvus | snapshot | 集群副本 |
| pgvector | 跟 PG 备份方案一致 | PG HA |
| Chroma | 文件复制 | 无原生 |

生产必须有备份策略。

---

## 12. 总结：默认选型

```
入门 / 学习 → Chroma
小项目（< 10 万 doc）→ Chroma 或 pgvector
中等项目（< 1000 万）→ Qdrant 自部署 / Pinecone
大项目 → Qdrant 集群 / Milvus / Pinecone
已有 PG → 试 pgvector
团队不想运维 → Pinecone
合规要求自部署 → Qdrant / Milvus
```

---

## 13. 下一步

具体工具详见：

- 📖 Pinecone → [02-pinecone.md](./02-pinecone.md)
- 📖 Qdrant → [03-qdrant.md](./03-qdrant.md)
- 📖 pgvector → [04-pgvector.md](./04-pgvector.md)
- 📖 Chroma / LanceDB → [05-chroma-lancedb.md](./05-chroma-lancedb.md)
- 📖 索引算法原理 → [06-index-algorithms.md](./06-index-algorithms.md)
- 📖 混合存储 → [07-hybrid-storage.md](./07-hybrid-storage.md)
