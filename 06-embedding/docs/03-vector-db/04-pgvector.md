# pgvector：在 Postgres 里做向量检索

> pgvector 是 PostgreSQL 的一个扩展，执行一句 `CREATE EXTENSION vector`，数据库里就有了向量类型和 HNSW 索引。如果团队已经在用 PG，我的建议是先别引入额外的向量库，用它就够了。

以 pgvector 0.8.7、Python 包 `pgvector 0.5.x`、`psycopg 3.3` 为准，Docker 镜像统一用 `pgvector/pgvector:pg17`。

---

## 1. 安装

### 1.1 用自带扩展的镜像（Docker）

```bash
docker run -d --name pg \
  -e POSTGRES_PASSWORD=pass \
  -p 5432:5432 \
  pgvector/pgvector:pg17
```

`pgvector/pgvector:pg17` 镜像已经装好了扩展。

### 1.2 在已有 PG 上加扩展

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

要求 PG 13 及以上。在 RDS、Cloud SQL 上执行需要 admin 权限。

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

维度（这里是 1024）在建表时就固定了，所有行必须一致。

有一个限制要特别注意：`vector` 类型的 HNSW / IVFFlat 索引最多支持 2000 维，`halfvec` 最多 4000 维。所以 text-embedding-3-large 的 3072 维、Cohere v5 默认的 2048 维，以及千问 v4 选到 2048 维时（它默认 1024 维），都不能直接给 `vector` 列建索引。要么用 Matryoshka 截到 2000 维以内（推荐 1024），要么改用 `halfvec` 索引（见 9.1）。

---

## 3. 建索引

### 3.1 HNSW（推荐）

```sql
CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops);

-- 可选参数（默认值 m=16, ef_construction=64）
CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

operator class 有三种：

- `vector_cosine_ops`：cosine 距离
- `vector_l2_ops`：L2 距离
- `vector_ip_ops`：inner product，向量已归一化时首选，速度最快

### 3.2 IVFFlat（老方案 / 小数据）

```sql
CREATE INDEX ON docs USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);    -- 官方建议：100 万行以内取 行数/1000，更多时取 sqrt(行数)
```

HNSW 出来以后，IVFFlat 已经用得不多了。

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

批量插入：

```python
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

数据到了百万级，用 `COPY` 会快很多：

```python
import numpy as np


with conn.cursor() as cur:
    with cur.copy("COPY docs (content, category, embedding) FROM STDIN WITH (FORMAT BINARY)") as copy:
        copy.set_types(["text", "text", "vector"])   # 需要先 register_vector(conn)
        for text, cat, emb in zip(texts, cats, embs):
            copy.write_row([text, cat, np.asarray(emb, dtype=np.float32)])
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

- `<=>`：cosine 距离（越小越近）
- `<->`：L2 距离
- `<#>`：负 inner product（向量已归一化时用它最快）

---

## 6. SQL filter（pgvector 的强项）

过滤条件直接写成熟悉的 SQL where：

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

功能上相当于向量库的 metadata filter，但这里写的是完整的 SQL，可以 JOIN、写子查询、UNION。

---

## 7. 调优：ef_search 与 hnsw.iterative_scan

查询时可以在召回率和速度之间做取舍：

```sql
-- 默认 ef_search=40
SET hnsw.ef_search = 100;   -- 提高召回率，慢些

-- 带 WHERE 过滤时结果凑不够 LIMIT 条：打开迭代扫描（pgvector 0.8.0+）
SET hnsw.iterative_scan = strict_order;   -- 或 relaxed_order，更快但顺序可能略乱
```

也可以只在当前事务里设置：

```sql
BEGIN;
SET LOCAL hnsw.ef_search = 100;
SELECT ... FROM docs ORDER BY embedding <=> %s LIMIT 5;
COMMIT;
```

---

## 8. 规模与内存（示意）

下表是示意数据，用来说明量级，不是在某台机器上的测量结果：HNSW 默认参数、`vector(1024)`，假设机器内存足够放下整个索引（100 万条这一行对应 8 核 16G 级别的单机）。

| 数据量 | Index 大小 | Query latency P95 |
|--------|-----------|--------------------|
| 10 万 × 1024 维 | ~600 MB | 5 ms |
| 100 万 × 1024 维 | ~6 GB | 10 ms |

索引要能整个放进内存，否则查询会频繁读盘，延迟会成倍上升。100 万条 1024 维大约要 6GB，部署时要预留出来；1000 万条按同样比例约 60GB，单机 16G 放不下，要么上更大内存的机器，要么先用 `halfvec`（见 9.1）或截维把索引压小。

---

## 9. 维度大、数据量大时的优化

### 9.1 量化（pgvector 0.7+）

```sql
CREATE INDEX ON docs USING hnsw ((embedding::halfvec(1024)) halfvec_cosine_ops);
```

`halfvec` 是半精度 float16，能省一半空间。

### 9.2 截维 (Matryoshka)

```sql
ALTER TABLE docs ADD COLUMN embedding_512 vector(512)
  GENERATED ALWAYS AS (subvector(embedding, 1, 512)::vector) STORED;

CREATE INDEX ON docs USING hnsw (embedding_512 vector_cosine_ops);
```

先用 512 维的索引召回，再用原始 1024 维向量重排。

---

## 10. ACID 与事务

pgvector 完整继承了 PG 的事务能力：

```python
with conn.transaction():          # psycopg 3：块内全部成功才提交，出错自动回滚
    with conn.cursor() as cur:
        cur.execute("INSERT INTO docs ...")
        cur.execute("UPDATE other_table ...")
```

专用向量库一般做不到这一点，这是 pgvector 最大的优势。

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

详见[增量索引：文档新增 / 修改 / 删除](../07-production/01-incremental.md)。

---

## 12. 完整示例

```python
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
| SQL filter | 最强 | 有限 | 支持 |
| 事务 / ACID | 完整支持 | 不支持 | 不支持 |
| JOIN 业务表 | 原生支持 | 不支持 | 不支持 |
| 极致召回 / 速度 | 一般（PG 通用引擎） | 强 | 强 |
| 量级 > 1 亿 | 难 | 可以 | 可以 |
| 运维成本 | 低（已有 PG） | 0（托管） | 中 |

满足下面几条，就可以放心选 pgvector：已经在用 PG；希望业务数据和向量放在一起，方便 JOIN 和事务；数据量在 1000 万以内；团队熟悉 PG。

---

## 14. 常见坑

| 坑 | 解 |
|----|----|
| 索引比内存大 | 加内存，或者用量化 |
| 查询慢 | 用 `EXPLAIN ANALYZE` 确认是否走了 HNSW；调 `ef_search` |
| filter 慢 | 给 filter 字段建 B-tree 索引 |
| 想用 ALTER TABLE 改维度 | 不支持，只能重建表 |

---

## 15. 下一步

- 嵌入式方案：[Chroma / LanceDB：嵌入式向量库](./05-chroma-lancedb.md)
- HNSW 原理：[HNSW / IVF / PQ 索引原理](./06-index-algorithms.md)
- 增量更新：[增量索引：文档新增 / 修改 / 删除](../07-production/01-incremental.md)
