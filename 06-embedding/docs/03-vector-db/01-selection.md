# 向量库选型全景 + 决策树

> 向量库没有"最好"的，只有适不适合你的团队和场景。我的默认思路是：已经有 Postgres 就用 pgvector；想要托管服务就选 Pinecone 或 Qdrant Cloud；要自部署选 Qdrant；本地嵌入式用 Chroma 或 LanceDB。这篇把主流选项摆在一起，给出判断的依据。

---

## 1. 主流向量库分类

| 类型 | 代表 | 特点 |
|------|------|------|
| 托管 SaaS | Pinecone / Qdrant Cloud / Weaviate Cloud / Zilliz Cloud | 不用运维 |
| Self-hosted | Qdrant / Weaviate / Milvus / Vespa | 自建集群 |
| Postgres 扩展 | pgvector / VectorChord（pgvecto.rs 的后继） | 用现有 PG |
| 嵌入式 / 文件 | Chroma / LanceDB / Faiss | 本地用 |
| Elasticsearch 系 | Elasticsearch / OpenSearch | 已有 ES 接 |
| 专用 | Milvus / Vald | 大规模分布式 |

---

## 2. 速选表

| 场景 | 推荐 | 备注 |
|------|------|------|
| 已有 Postgres，量 < 1000 万 | pgvector | 最省事，不加任何组件 |
| 想最快上线（云托管） | Pinecone | Serverless 按量计费，价格看官网 |
| 自部署 + 多功能（payload filter 等） | Qdrant | docker run 即用 |
| 巨量（亿级） + 分布式 | Milvus / Vespa | 复杂但能扛 |
| 笔记本 / 单机演示 | Chroma / LanceDB | embedded |
| 已有 ES 集群 | OpenSearch / ES knn_vector | 复用 |
| 想要 incremental query update | Vespa / Weaviate | 更新友好 |

---

## 3. 多维度对比

| | Pinecone | Qdrant | Weaviate | Milvus | pgvector | Chroma |
|---|---|---|---|---|---|---|
| 部署 | 托管 | 自/云 | 自/云 | 自/云 | 已有 PG | 自/嵌入 |
| 索引算法 | 专有 | HNSW | HNSW / flat | HNSW / IVF / DiskANN | HNSW / IVFFlat | HNSW |
| Metadata filter | 支持 | 支持（强） | 支持 | 支持 | 支持（SQL，强） | 支持 |
| 多向量 / 多模态 | 支持 | 支持 | 支持 | 支持 | 自管 | 有限 |
| Sparse + Dense 混合 | 支持 | 支持 | 支持 | 支持 | 需自拼 | 有限 |
| Geo / Range | 有限 | 支持 | 支持 | 有限 | 支持 | 有限 |
| 上手难度 | 极低 | 低 | 低-中 | 中-高 | 极低（PG 用户） | 极低 |
| 量级（推荐） | 任意 | 千万-亿 | 千万 | 亿+ | 千万内 | 百万内 |
| 价格 | 中-高 | 低 | 中 | 低（开源） | 几乎免费 | 免费 |

---

## 4. 选型决策树

```
你有 Postgres 吗？
├─ 有，量 < 1000 万
│   → pgvector（详见第 7 节和 pgvector 篇）
│
└─ 没有 / 量大

  团队有人能运维吗？
  ├─ 不想运维
  │   ├─ 简单需求 → Pinecone
  │   └─ 要 filter / 多向量 → Qdrant Cloud
  │
  └─ 能运维
      ├─ 量 < 1 亿 → Qdrant self-hosted（详见第 6 节和 Qdrant 篇）
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

详见 [Pinecone：托管向量库](./02-pinecone.md)。

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
hits = client.query_points(
    collection_name="docs",
    query=[...],
    limit=5,
    query_filter=Filter(must=[FieldCondition(key="category", match={"value": "billing"})]),
).points
```

详见 [Qdrant：自托管向量库](./03-qdrant.md)。

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

详见 [pgvector：在 Postgres 里做向量检索](./04-pgvector.md)。

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

详见 [Chroma / LanceDB：嵌入式向量库](./05-chroma-lancedb.md)。

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

下面按 1000 万条、1024 维来看成本由哪些部分构成。各家价格和套餐变动频繁，这里不给具体金额，请用官方定价计算器按自己的 QPS 和数据量去算：

| 方案 | 计费方式 | 成本特点 |
|------|---------|---------|
| Pinecone Serverless | 存储 + 读写单元按量 | 流量小时很省，高 QPS 时要算账 |
| Qdrant Cloud | 按集群规格（内存/磁盘） | 规格固定，可预测 |
| 自部署 Qdrant | 云主机费用 | 1000 万 × 1024 维 float32 原始向量约 40GB，内存按量化方案估 |
| pgvector（RDS 等） | 跟你的 PG 实例走 | 向量和业务数据共用，省组件 |
| Chroma / LanceDB 单机 | 只有磁盘 / 主机 | 几乎免费，但无集群 |

数据量越大，自部署的成本优势通常越明显，前提是把运维人力也算进去。

---

## 11. 容灾 / 备份

| 工具 | 备份 | 副本 |
|------|------|------|
| Pinecone | 自动 | 自动 |
| Qdrant | snapshot API | 集群副本 |
| Milvus | snapshot | 集群副本 |
| pgvector | 跟 PG 备份方案一致 | PG HA |
| Chroma | 文件复制 | 无原生 |

上生产前一定要定好备份策略。

---

## 12. 默认选型

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

各个工具的细节：

- [Pinecone：托管向量库](./02-pinecone.md)
- [Qdrant：自托管向量库](./03-qdrant.md)
- [pgvector：在 Postgres 里做向量检索](./04-pgvector.md)
- [Chroma / LanceDB：嵌入式向量库](./05-chroma-lancedb.md)
- [HNSW / IVF / PQ 索引原理](./06-index-algorithms.md)
- [混合存储：Vector + Metadata + Filter](./07-hybrid-storage.md)
