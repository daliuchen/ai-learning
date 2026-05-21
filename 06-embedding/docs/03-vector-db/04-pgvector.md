# pgvector：已有 Postgres 最划算

> **一句话**：pgvector 是 PostgreSQL 扩展——`CREATE EXTENSION vector` 就有了向量类型 + HNSW 索引，**已经用 PG 的团队不要再上额外向量库**。

---

## 1. 装一下

### 1.1 PG 自带（Docker）

```bash
docker run -d --name pg \
  -e POSTGRES_PASSWORD=pass \
  -p 5432:5432 \
  pgvector/pgvector:pg16
```

镜像 `pgvector/pgvector:pg16` 已经装好扩展。

### 1.2 已有 PG，加扩展

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

需要 PG 13+。在 RDS / Cloud SQL 上需要 admin 权限。

---

## 2. 建表

```sql
CREATE TABLE docs (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT,
    lang TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    embedding vector(1024)   -- ★ pgvector 类型
);
```

dimension（1024）是固定的，所有行必须同维。

---

## 3. 建索引

### 3.1 HNSW（推荐）

```sql
CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops);

-- 可选参数（默认值 m=16, ef_construction=64）
CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

距离操作符：

- `vector_cosine_ops`（cosine 距离）
- `vector_l2_ops`（L2）
- `vector_ip_ops`（inner product，已归一化向量首选，最快）

### 3.2 IVFFlat（老 / 小数据）

```sql
CREATE INDEX ON docs USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);    -- 一般 sqrt(N)
```

HNSW 出来后 IVFFlat 用得少了。

---

## 4. 插入

```python
# pip install pgvector psycopg
import psycopg
from pgvector.psycopg import register_vector


conn = psycopg.connect("postgresql://postgres:pass@localhost:5432/postgres")
register_vector(conn)


with conn.cursor() as cur:
    cur.execute(
        """INSERT INTO docs (content, category, embedding)
           VALUES (%s, %s, %s)""",
        ("如何关闭自动续费", "billing", [0.1, 0.2, ..., 0.9]),  # list 自动转 vector
    )
    conn.commit()
```

批量：

```python
from psycopg.types.json import Jsonb


with conn.cursor() as cur:
    cur.executemany(
        "INSERT INTO docs (content, category, embedding) VALUES (%s, %s, %s)",
        [
            (text, cat, emb)
            for text, cat, emb in zip(texts, cats, embs)
        ],
    )
    conn.commit()
```

或用 `COPY`（百万级速度起飞）：

```python
import io


buf = io.StringIO()
for text, cat, emb in zip(texts, cats, embs):
    emb_str = "[" + ",".join(map(str, emb)) + "]"
    buf.write(f"{text}\t{cat}\t{emb_str}\n")
buf.seek(0)


with conn.cursor() as cur:
    cur.copy("COPY docs (content, category, embedding) FROM STDIN", buf)
    conn.commit()
```

---

## 5. 查询

### 5.1 找最相似的

```python
q_emb = embed("如何取消订阅")

with conn.cursor() as cur:
    cur.execute(
        """SELECT id, content, category, embedding <=> %s AS distance
           FROM docs
           ORDER BY embedding <=> %s
           LIMIT 5""",
        (q_emb, q_emb),
    )
    for row in cur.fetchall():
        print(row)
```

距离操作符：

- `<=>` ：cosine 距离（小=近）
- `<->` ：L2 距离
- `<#>` ：负 inner product（已归一化用这个最快）

---

## 6. SQL filter（pgvector 强项）

直接用熟悉的 SQL where：

```python
cur.execute(
    """SELECT id, content
       FROM docs
       WHERE category = %s
         AND lang = 'zh'
         AND created_at > NOW() - INTERVAL '30 days'
       ORDER BY embedding <=> %s
       LIMIT 5""",
    ("billing", q_emb),
)
```

跟向量库的 metadata filter 等价，但**这是真正的 SQL**——能 JOIN、子查询、UNION 等。

---

## 7. 优化：ef_search 与 hnsw.iterative_scan

查询时 trade off recall vs speed：

```sql
-- 默认 ef_search=40
SET hnsw.ef_search = 100;   -- 提高召回率，慢些

-- 极端高召回（pgvector 0.8+）
SET hnsw.iterative_scan = strict_order;
```

或在 query 里 set local：

```sql
BEGIN;
SET LOCAL hnsw.ef_search = 100;
SELECT ... FROM docs ORDER BY embedding <=> %s LIMIT 5;
COMMIT;
```

---

## 8. 性能实测

(单机 8 核 16G，HNSW 默认参数)

| 数据量 | Index 大小 | Query latency P95 |
|--------|-----------|--------------------|
| 10 万 × 1024 维 | ~600 MB | 5 ms |
| 100 万 × 1024 维 | ~6 GB | 10 ms |
| 1000 万 × 1024 维 | ~60 GB | 30 ms |

⚠️ 索引必须能装内存里。100 万 / 1024 维大约 6GB，要预留。

---

## 9. 维度大 / 量大优化

### 9.1 量化（pgvector 0.7+）

```sql
CREATE INDEX ON docs USING hnsw ((embedding::halfvec(1024)) halfvec_cosine_ops);
```

`halfvec`（半精度 float16）省一半空间。

### 9.2 截维 (Matryoshka)

```sql
ALTER TABLE docs ADD COLUMN embedding_512 vector(512)
  GENERATED ALWAYS AS (subvector(embedding, 1, 512)::vector) STORED;

CREATE INDEX ON docs USING hnsw (embedding_512 vector_cosine_ops);
```

用 512 维索引召回，原 1024 维 rerank。

---

## 10. ACID + 事务

pgvector 完全继承 PG 的事务能力：

```python
with conn.cursor() as cur:
    cur.execute("BEGIN")
    cur.execute("INSERT INTO docs ...")
    cur.execute("UPDATE other_table ...")
    cur.execute("COMMIT")
```

向量库通常没这个能力——pgvector 的杀手锏。

---

## 11. 增量更新很方便

```sql
-- 单条更新
UPDATE docs SET embedding = $1 WHERE id = 42;

-- 批量重 embed
UPDATE docs SET embedding = new_embedding
FROM (SELECT id, new_embedding FROM staging) s
WHERE docs.id = s.id;

-- 删
DELETE FROM docs WHERE id = 42;

-- 软删
ALTER TABLE docs ADD COLUMN deleted_at TIMESTAMPTZ;
UPDATE docs SET deleted_at = NOW() WHERE id = 42;

-- 查询时排除
SELECT ... WHERE deleted_at IS NULL ORDER BY embedding <=> %s LIMIT 5;
```

详见 [07-production/01-incremental.md](../07-production/01-incremental.md)。

---

## 12. 完整 demo

```python
# demos/vector_db/04_pgvector.py
import psycopg
from pgvector.psycopg import register_vector
from openai import OpenAI


oai = OpenAI()
conn = psycopg.connect("postgresql://postgres:pass@localhost:5432/postgres")
register_vector(conn)


# 1. 建表
with conn.cursor() as cur:
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute("DROP TABLE IF EXISTS docs")
    cur.execute("""
        CREATE TABLE docs (
            id SERIAL PRIMARY KEY,
            content TEXT,
            category TEXT,
            embedding vector(1536)
        )
    """)
    cur.execute("CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops)")
    conn.commit()


# 2. 插入
docs = [
    ("如何关闭自动续费", "billing"),
    ("如何登录账号", "auth"),
    ("停止订阅的方法", "billing"),
    ("重置密码教程", "auth"),
    ("退款流程", "billing"),
]

texts = [d[0] for d in docs]
embs = [d.embedding for d in oai.embeddings.create(model="text-embedding-3-small", input=texts).data]


with conn.cursor() as cur:
    cur.executemany(
        "INSERT INTO docs (content, category, embedding) VALUES (%s, %s, %s)",
        [(t, c, e) for (t, c), e in zip(docs, embs)],
    )
    conn.commit()


# 3. 查询
q = "如何取消订阅"
q_emb = oai.embeddings.create(model="text-embedding-3-small", input=[q]).data[0].embedding


with conn.cursor() as cur:
    cur.execute(
        """SELECT id, content, category, embedding <=> %s AS distance
           FROM docs
           WHERE category = 'billing'
           ORDER BY embedding <=> %s
           LIMIT 3""",
        (q_emb, q_emb),
    )
    for row in cur.fetchall():
        print(f"  dist={row[3]:.4f}  {row[1]}")
```

---

## 13. 跟其它向量库对比

| | pgvector | Pinecone | Qdrant |
|---|---|---|---|
| SQL filter | ✅✅（最强）| ⚠️ | ✅ |
| 事务 / ACID | ✅✅ | ❌ | ❌ |
| JOIN 业务表 | ✅✅ | ❌ | ❌ |
| 极致召回 / 速度 | ⚠️（PG 通用引擎） | ✅✅ | ✅✅ |
| 量级 > 1 亿 | 难 | ✅ | ✅ |
| 运维成本 | 低（已有 PG） | 0（托管） | 中 |

**核心理由用 pgvector**：

- 已经有 PG
- 业务数据跟向量在一起方便（JOIN / 事务）
- 量级 < 1000 万
- 团队 PG 熟

---

## 14. 常见坑

| 坑 | 解 |
|----|----|
| 索引大于内存 | 加 RAM 或上 quantization |
| 慢查询 | `EXPLAIN ANALYZE` 看是否走 HNSW；调 `ef_search` |
| filter 慢 | 给 filter 字段建 B-tree 索引 |
| ALTER TABLE 改维度 | 不支持，要重建表 |

---

## 15. 下一步

- 📖 Chroma / LanceDB（嵌入式）→ [05-chroma-lancedb.md](./05-chroma-lancedb.md)
- 📖 HNSW 原理 → [06-index-algorithms.md](./06-index-algorithms.md)
- 📖 增量更新 → [07-production/01-incremental.md](../07-production/01-incremental.md)
