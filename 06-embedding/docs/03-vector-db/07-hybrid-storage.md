# 混合存储：Vector + Metadata + Filter

> **一句话**：生产 RAG 几乎一定要"按用户 / 时间 / 标签 / 权限"过滤——把 embedding 跟 metadata 一起存、合理建 payload index，才能在大数据集里高效精准检索。

---

## 1. 为什么 metadata 这么重要

```
搜 "退款" 应该召回什么？
  ❌ 全库找 → 召回老政策（过期）+ 别用户的工单 + 内部草稿
  ✅ filter: lang=zh AND status=published AND created_at > now() - 1 year
           AND (visibility='public' OR owner=current_user)
```

**没有 filter 的向量搜索基本是 demo，不是生产**。

---

## 2. metadata 设计原则

### 2.1 必备字段

```python
{
    "id": "doc_12345",                  # 业务 ID（跟主库一致）
    "text": "原文（短摘要 / 不要全文）",   # 用于显示和 rerank
    "source": "kb_article",             # 来源类型
    "lang": "zh",                       # 语言
    "category": "billing",              # 业务分类
    "tags": ["refund", "subscription"], # 标签
    "created_at": 1715817600,           # 时间
    "version": 3,                        # 版本号
    "visibility": "public",             # 权限
}
```

### 2.2 别在 metadata 里放啥

| 别放 | 理由 |
|------|------|
| 原文全文 | 长 → 序列化慢 / 网络重 |
| binary（图片） | 用 URL 引用，存对象存储 |
| 频繁变的字段（如 view_count）| 每次都要 reindex |
| 嵌套很深的 JSON | filter 索引建不上 |

### 2.3 命名规范

- 用 snake_case
- 时间一律 unix timestamp（秒）
- 类别 / 标签用枚举值（不要自由文本）
- 加 prefix 防混淆：`doc_id` 而不是单 `id`

---

## 3. 在各向量库里的存法

### 3.1 Qdrant

```python
from qdrant_client.models import PointStruct


client.upsert(
    collection_name="docs",
    points=[
        PointStruct(
            id=1,
            vector=embedding,
            payload={
                "doc_id": "kb_42",
                "text": "如何取消订阅...",
                "category": "billing",
                "tags": ["refund"],
                "created_at": 1715817600,
                "visibility": "public",
            },
        ),
    ],
)


# filter
from qdrant_client.models import Filter, FieldCondition, MatchValue, Range


filt = Filter(
    must=[
        FieldCondition(key="category", match=MatchValue(value="billing")),
        FieldCondition(key="visibility", match=MatchValue(value="public")),
        FieldCondition(key="created_at", range=Range(gte=1715000000)),
    ],
)


hits = client.search(
    collection_name="docs",
    query_vector=q_emb,
    query_filter=filt,
    limit=5,
)
```

### 3.2 Pinecone

```python
index.upsert(vectors=[
    {
        "id": "kb_42",
        "values": embedding,
        "metadata": {
            "doc_id": "kb_42",
            "text": "...",
            "category": "billing",
            "tags": ["refund"],
            "created_at": 1715817600,
        },
    },
])


results = index.query(
    vector=q_emb,
    top_k=5,
    filter={
        "category": "billing",
        "tags": {"$in": ["refund"]},
        "created_at": {"$gte": 1715000000},
    },
)
```

### 3.3 pgvector

```sql
CREATE TABLE docs (
    id BIGSERIAL PRIMARY KEY,
    doc_id TEXT UNIQUE NOT NULL,
    content TEXT,
    category TEXT,
    tags TEXT[],                       -- PG 数组
    created_at TIMESTAMPTZ,
    visibility TEXT,
    embedding vector(1536)
);

-- vector 索引
CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops);

-- metadata 索引（B-tree / GIN）
CREATE INDEX ON docs (category);
CREATE INDEX ON docs (created_at);
CREATE INDEX ON docs USING gin (tags);   -- 数组用 GIN
```

```sql
SELECT doc_id, content
FROM docs
WHERE category = 'billing'
  AND 'refund' = ANY (tags)
  AND created_at > NOW() - INTERVAL '1 year'
  AND visibility = 'public'
ORDER BY embedding <=> $1
LIMIT 5;
```

---

## 4. Pre-filter vs Post-filter

```
pre-filter: 先按 metadata 排除，再在剩下里做向量搜
  优点：精确，召回率高
  缺点：filter 字段没索引时慢
  
post-filter: 先向量搜 top-N，再 metadata 过滤
  优点：向量索引高效
  缺点：可能"前 N 全被过滤掉"，召回率掉
```

主流向量库（Qdrant / Pinecone / Milvus）做的是**带 filter 的 ANN**——边搜边过滤，混合策略。

但需要 **payload index** 才能高效（详见 [03-qdrant.md](./03-qdrant.md) #6）：

```python
client.create_payload_index(
    collection_name="docs",
    field_name="category",
    field_schema=PayloadSchemaType.KEYWORD,
)
```

---

## 5. 多租户隔离方案

### 方案 A：filter（推荐小中规模）

```python
client.upsert(
    collection_name="docs",
    points=[PointStruct(
        id=...,
        vector=...,
        payload={"tenant_id": "tenant_42", ...},
    )],
)

hits = client.search(
    collection_name="docs",
    query_vector=q,
    query_filter=Filter(must=[
        FieldCondition(key="tenant_id", match=MatchValue(value="tenant_42")),
    ]),
)
```

简单，但**安全完全靠 filter**——别忘加。

### 方案 B：每租户一个 collection

```python
client.create_collection(collection_name="tenant_42", ...)
client.upsert(collection_name="tenant_42", ...)
```

- 真隔离
- 但租户多（> 1000）会爆 collection 数

### 方案 C：每租户一个 namespace（Pinecone）

```python
index.upsert(vectors=[...], namespace="tenant_42")
index.query(vector=[...], namespace="tenant_42")
```

Pinecone 专属，类似 B 但更轻量。

### 决策

```
< 100 租户：A（filter）就行
100-10K：C（Pinecone namespace）或 A + tenant_id 严格 enforce
> 10K + 真隔离要求：B（独立 collection）+ 按需创建
```

---

## 6. 软删除

不要物理 delete，加个 `deleted_at`：

```python
client.set_payload(
    collection_name="docs",
    payload={"deleted_at": int(time.time())},
    points=[doc_id],
)


# 查询排除
filt = Filter(must_not=[
    FieldCondition(key="deleted_at", range=Range(gte=0)),
])
```

定期物理清理：

```python
client.delete(
    collection_name="docs",
    points_selector=Filter(must=[
        FieldCondition(key="deleted_at", range=Range(gte=0, lte=int(time.time()) - 30*86400)),
    ]),
)
```

---

## 7. 多版本共存

新模型 / 新策略上线时不要 in-place 覆盖：

```python
# 旧
collection_v1 (1536 维, text-embedding-3-small)
collection_v2 (3072 维, text-embedding-3-large)  ← 同时存在

# 灰度
def search(query):
    if user.in_experiment("v2"):
        return search_in(collection_v2, query)
    return search_in(collection_v1, query)
```

跑稳后下 v1。

---

## 8. metadata × vector 联合排序

```python
# Qdrant 不直接支持，但可以拿 top-N 后自己 rerank
hits = client.search(
    collection_name="docs",
    query_vector=q,
    limit=20,
)

# 自定义 score
def final_score(hit, query):
    vec_score = hit.score   # 0-1
    freshness = max(0, 1 - (now() - hit.payload["created_at"]) / 365*86400)
    return vec_score * 0.7 + freshness * 0.3


ranked = sorted(hits, key=lambda h: -final_score(h, query))[:5]
```

或加 rerank 模型（详见 [02-models/05-rerank.md](../02-models/05-rerank.md)）。

---

## 9. 完整 demo

```python
# demos/vector_db/07_hybrid.py
import time
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, Range,
    PayloadSchemaType,
)
from openai import OpenAI


client = QdrantClient(":memory:")
oai = OpenAI()


client.create_collection(
    collection_name="docs",
    vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
)


# 建 payload index
for field in ["category", "lang", "visibility"]:
    client.create_payload_index("docs", field_name=field, field_schema=PayloadSchemaType.KEYWORD)


client.create_payload_index("docs", field_name="created_at", field_schema=PayloadSchemaType.INTEGER)


# 上传
now = int(time.time())
docs = [
    ("如何关闭自动续费", "billing", "zh", "public", now),
    ("How to login", "auth", "en", "public", now - 365*86400),
    ("停止订阅的方法", "billing", "zh", "public", now),
    ("Internal note", "billing", "zh", "internal", now),
    ("退款流程", "billing", "zh", "public", now - 30*86400),
]


texts = [d[0] for d in docs]
embs = [d.embedding for d in oai.embeddings.create(model="text-embedding-3-small", input=texts).data]


client.upsert(
    collection_name="docs",
    points=[
        PointStruct(
            id=i,
            vector=emb,
            payload={
                "text": text,
                "category": cat,
                "lang": lang,
                "visibility": vis,
                "created_at": ts,
            },
        )
        for i, ((text, cat, lang, vis, ts), emb) in enumerate(zip(docs, embs))
    ],
)


# 查询：只在 zh / billing / public 里搜，过去 60 天
q = "如何取消订阅"
q_emb = oai.embeddings.create(model="text-embedding-3-small", input=[q]).data[0].embedding


hits = client.search(
    collection_name="docs",
    query_vector=q_emb,
    query_filter=Filter(must=[
        FieldCondition(key="category", match=MatchValue(value="billing")),
        FieldCondition(key="lang", match=MatchValue(value="zh")),
        FieldCondition(key="visibility", match=MatchValue(value="public")),
        FieldCondition(key="created_at", range=Range(gte=now - 60*86400)),
    ]),
    limit=3,
)


for h in hits:
    print(f"  score={h.score:.4f}  {h.payload['text']}")
```

---

## 10. 常见坑

| 坑 | 解 |
|----|----|
| 没建 payload index | filter 慢 |
| metadata 塞全文 | 序列化 / 网络重，只存 ID + 关键字段 |
| 跨租户没 filter | 数据泄漏，必须 enforce |
| metadata 频繁更新 | 重 embedding 不必要的话，用单独表存动态字段 |
| 时间用字符串 | 改用 int timestamp，filter 才能 range |

---

## 11. 章节小结

03-vector-db 完结。你现在应该会：

- 按场景选向量库
- 跑 Pinecone / Qdrant / pgvector / Chroma
- 理解 HNSW / IVF 怎么工作的
- 用 metadata + filter 做精确搜索

---

## 12. 下一步

- 📖 chunking 策略（很重要）→ [04-chunking/01-why-chunking.md](../04-chunking/01-why-chunking.md)
- 📖 混合检索 → [05-retrieval/02-bm25-fusion.md](../05-retrieval/02-bm25-fusion.md)
- 📖 增量索引 → [07-production/01-incremental.md](../07-production/01-incremental.md)
