# 混合存储：Vector + Metadata + Filter

> 生产环境的 RAG，几乎都要按用户、时间、标签或权限做过滤。embedding 要和 metadata 存在一起，再给常用的过滤字段建好 payload index，大数据集上的检索才能又快又准。这篇讲 metadata 怎么设计，以及在几个主流向量库里怎么存、怎么过滤。

---

## 1. 为什么 metadata 这么重要

```
搜 "退款" 应该召回什么？
  ❌ 全库找 → 召回老政策（过期）+ 别用户的工单 + 内部草稿
  ✅ filter: lang=zh AND status=published AND created_at > now() - 1 year
           AND (visibility='public' OR owner=current_user)
```

不带 filter 的向量搜索，只能算演示，上不了生产。

---

## 2. metadata 设计原则

### 2.1 必备字段

```python
{
    "doc_id": "doc_12345",              # 业务 ID（跟主库一致）
    "text": "原文（短摘要 / 不要全文）",   # 用于显示和 rerank
    "source": "kb_article",             # 来源类型
    "lang": "zh",                       # 语言
    "category": "billing",              # 业务分类
    "tags": ["refund", "subscription"], # 标签
    "created_at": 1778889600,           # 时间（unix 秒，这里是 2026-05-16）
    "version": 3,                        # 版本号
    "visibility": "public",             # 权限
}
```

### 2.2 哪些东西别放进 metadata

| 别放 | 理由 |
|------|------|
| 原文全文 | 太长，序列化慢，网络传输重 |
| 二进制数据（图片） | 存对象存储，metadata 里只放 URL |
| 频繁变化的字段（如 view_count）| 每次变化都要重新写索引 |
| 嵌套很深的 JSON | 建不了 filter 索引 |

### 2.3 命名规范

- 用 snake_case
- 时间统一用 unix timestamp（秒）
- 类别和标签用枚举值，不要用自由文本
- 加前缀避免歧义，比如用 `doc_id` 而不是单独一个 `id`

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
                "created_at": 1778889600,
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
        FieldCondition(key="created_at", range=Range(gte=1777593600)),
    ],
)


hits = client.query_points(
    collection_name="docs",
    query=q_emb,
    query_filter=filt,
    limit=5,
).points
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
            "created_at": 1778889600,
        },
    },
])


results = index.query(
    vector=q_emb,
    top_k=5,
    filter={
        "category": "billing",
        "tags": {"$in": ["refund"]},
        "created_at": {"$gte": 1777593600},
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

Qdrant、Pinecone、Milvus 这些主流向量库做的是带 filter 的 ANN，在图上搜索的同时过滤，算是两种策略的折中。

不过要搜得快，前提是给过滤字段建了 payload index（见 [Qdrant：自托管向量库](./03-qdrant.md) 第 6 节）：

```python
client.create_payload_index(
    collection_name="docs",
    field_name="category",
    field_schema=PayloadSchemaType.KEYWORD,
)
```

---

## 5. 多租户隔离方案

### 方案 A：filter（适合中小规模）

```python
client.upsert(
    collection_name="docs",
    points=[PointStruct(
        id=...,
        vector=...,
        payload={"tenant_id": "tenant_42", ...},
    )],
)

hits = client.query_points(
    collection_name="docs",
    query=q,
    query_filter=Filter(must=[
        FieldCondition(key="tenant_id", match=MatchValue(value="tenant_42")),
    ]),
).points
```

做法简单，但隔离完全依赖 filter，任何一次查询忘了加都会串数据。

### 方案 B：每个租户一个 collection

```python
client.create_collection(collection_name="tenant_42", ...)
client.upsert(collection_name="tenant_42", ...)
```

这是物理隔离，但租户一多（超过 1000），collection 数量会失控。

### 方案 C：每个租户一个 namespace（Pinecone）

```python
index.upsert(vectors=[...], namespace="tenant_42")
index.query(vector=[...], namespace="tenant_42")
```

Pinecone 特有，效果接近方案 B，但更轻量。

### 怎么选

```
< 100 租户：A（filter）就行
100-10K：C（Pinecone namespace）或 A + tenant_id 严格 enforce
> 10K + 真隔离要求：B（独立 collection）+ 按需创建
```

---

## 6. 软删除

不要直接物理删除，而是加一个 `deleted_at` 字段：

```python
client.set_payload(
    collection_name="docs",
    payload={"deleted_at": int(time.time())},
    points=[point_id],   # Qdrant 的点 ID（int 或 UUID），不是业务 doc_id
)


# 查询排除
filt = Filter(must_not=[
    FieldCondition(key="deleted_at", range=Range(gte=0)),
])
```

再定期做物理清理：

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

换模型或换策略时，不要在原地覆盖：

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

v2 跑稳以后，再下线 v1。

---

## 8. metadata 和向量分数联合排序

```python
# Qdrant 1.14 起可以用 FormulaQuery 在库内按公式重算分数（含时间衰减函数）；
# 这里演示通用做法：取 top-N 后在应用层自己算
hits = client.query_points(
    collection_name="docs",
    query=q,
    limit=20,
).points

# 自定义 score
def final_score(hit, query):
    vec_score = hit.score   # 0-1
    age = time.time() - hit.payload["created_at"]
    freshness = max(0, 1 - age / (365 * 86400))   # 一年内线性衰减到 0
    return vec_score * 0.7 + freshness * 0.3


ranked = sorted(hits, key=lambda h: -final_score(h, query))[:5]
```

也可以接一个 rerank 模型（见 [Rerank 模型：召回之后的精排](../02-models/05-rerank.md)）。

---

## 9. 完整示例

```python
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


hits = client.query_points(
    collection_name="docs",
    query=q_emb,
    query_filter=Filter(must=[
        FieldCondition(key="category", match=MatchValue(value="billing")),
        FieldCondition(key="lang", match=MatchValue(value="zh")),
        FieldCondition(key="visibility", match=MatchValue(value="public")),
        FieldCondition(key="created_at", range=Range(gte=now - 60*86400)),
    ]),
    limit=3,
).points


for h in hits:
    print(f"  score={h.score:.4f}  {h.payload['text']}")
```

---

## 10. 常见坑

| 坑 | 解 |
|----|----|
| 没建 payload index | filter 会很慢，给过滤字段建索引 |
| metadata 里塞全文 | 序列化和网络都重，只存 ID 和关键字段 |
| 多租户查询漏了 filter | 会泄漏数据，必须在代码层强制加上 |
| metadata 频繁更新 | 动态字段单独存一张表，避免不必要的重写 |
| 时间存成字符串 | 改成 int timestamp，才能做 range 过滤 |

---

## 11. 向量库这一章回顾

读到这里，你应该能做到：

- 按场景选向量库
- 跑通 Pinecone、Qdrant、pgvector、Chroma
- 理解 HNSW 和 IVF 的工作方式
- 用 metadata 加 filter 做精确检索

---

## 12. 下一步

- 切分策略，这一步对效果影响很大：[为什么 Chunking 决定 RAG 的上限](../04-chunking/01-why-chunking.md)
- 混合检索：[BM25 + Dense 融合：RRF / Weighted](../05-retrieval/02-bm25-fusion.md)
- 增量索引：[增量索引：文档新增 / 修改 / 删除](../07-production/01-incremental.md)
