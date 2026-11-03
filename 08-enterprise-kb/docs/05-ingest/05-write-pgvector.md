# EKB 25：写入 pgvector（文档、chunk、ACL、全文索引）

> 入库要写三张表：解析出的文档元数据进 `documents`，可见角色进 `acl`，带向量的 chunk 进 `chunks`，同时生成全文检索用的 `tsv`。这篇给出完整的入库代码，也解释为什么 BM25 要用的 tsvector 最好在写入时就算好。

---

## 1. 入库流程

```
解析产物 + 向量
  │
  ├─ documents 表：title, space, source_url, updated_at
  ├─ acl 表：     (doc_id, role) 多行
  └─ chunks 表：  content, section_path, embedding, tsv, chunk_index
```

一篇文档的入库放在一个事务里：文档、ACL、chunks 要么全部成功，要么全部回滚，免得出现文档进了库、chunk 却没进的脏数据。

---

## 2. 写文档和 ACL

```python
# ingest/load.py
import psycopg
from pgvector.psycopg import register_vector

def load_document(conn, doc_meta: dict, chunks: list[dict]):
    with conn.transaction():
        # 1. 写 documents，拿回 doc_id
        doc_id = conn.execute(
            "INSERT INTO documents (title, space, source_url, updated_at) "
            "VALUES (%(title)s, %(space)s, %(source_url)s, %(updated_at)s) "
            "RETURNING id",
            doc_meta,
        ).fetchone()["id"]          # 连接用了 dict_row（见第 4 节）

        # 2. 写 acl：roles 列表展开成多行
        for role in doc_meta.get("roles", ["all"]):
            conn.execute(
                "INSERT INTO acl (doc_id, role) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (doc_id, role),
            )

        # 3. 写 chunks
        load_chunks(conn, doc_id, chunks)
```

`roles: [all]` 在 acl 里是一行 `(doc_id, 'all')`，`roles: [hr, finance]` 则是两行。检索时按角色 join 这张表来过滤。

---

## 3. 写 chunk，顺便生成 tsv

写 chunk 的同时，让 Postgres 把全文检索向量 `tsv` 一起算好，留给 BM25 和关键词检索用：

```python
def load_chunks(conn, doc_id: int, chunks: list[dict]):
    for i, c in enumerate(chunks):
        conn.execute(
            "INSERT INTO chunks "
            "(doc_id, content, section_path, embedding, tsv, chunk_index) "
            "VALUES (%s, %s, %s, %s, to_tsvector('simple', %s), %s)",
            (doc_id, c["content"], c["section_path"],
             c["embedding"], c["content"], i),
        )
```

`to_tsvector('simple', content)` 在写入时就把内容转成了全文检索向量。到检索阶段，向量和全文两条路都是现成的，不用临时计算。

> Postgres 内置的分词对中文并不友好。生产环境的中文场景，常见做法是装 `pg_jieba` 或 `zhparser` 扩展做中文分词，或者在应用层用 `rank-bm25` 自己算 BM25（见 [BM25 关键词检索](../07-retrieval/02-bm25.md)）。`'simple'` 配置适合演示，以及中英混合的关键词匹配。

---

## 4. 注册 pgvector 类型

把 `embedding`（Python list）写进 `vector` 列之前，要先让 psycopg 认识这个类型：

```python
# ingest/load.py（db.py 里的 get_conn 同理）
# ⚠️ 先确保扩展存在（建表脚本里的 CREATE EXTENSION IF NOT EXISTS vector），再注册
def get_conn():
    conn = psycopg.connect("postgresql://postgres:ekb@localhost/postgres",
                           row_factory=psycopg.rows.dict_row)
    register_vector(conn)    # 之后 vector 列读出来是 numpy 数组，写入可传 list / numpy / Vector
    return conn
```

有三点要注意：

- 在还没创建扩展的库上调用 `register_vector`，会报「vector type not found」。顺序必须是先 `CREATE EXTENSION`，再 `register_vector`。
- 写入时传 `list` 可以（Postgres 会按列类型转换），但查询里的 `embedding <=> %s` 必须传 `Vector(...)` 或 numpy 数组，原因见 [向量库选型](../03-selection/02-vector-db-pgvector.md)。
- 设置了 `row_factory=dict_row` 之后，`fetchone()` 返回的是 dict，所以上面的 `RETURNING id` 要写成 `.fetchone()["id"]`；默认的 tuple 行才用 `[0]`。

---

## 5. HNSW 索引在数据导入之后再建

HNSW 不像 IVFFlat 那样要求表里先有数据，空表也能建。但批量导入之后再建索引，比边插入边维护快得多：

```python
conn.execute("CREATE INDEX IF NOT EXISTS chunks_emb_hnsw "
             "ON chunks USING hnsw (embedding vector_cosine_ops)")
conn.execute("CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING gin (tsv)")
conn.commit()
```

首次全量导入时先灌数据、再建索引；之后的增量更新（第 26 篇）由索引自动维护。

---

## 6. 完整的 ingest 入口

把 parse、chunk、embed、load 串起来：

```python
# ingest/main.py
from pathlib import Path
from .parse import split_frontmatter, parse_blocks
from .chunk import chunk_blocks
from .embed import embed_chunks
from .load import load_document, get_conn

def ingest_file(conn, path: Path):
    raw = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    blocks = parse_blocks(body)
    chunks = chunk_blocks(blocks, meta)
    chunks = embed_chunks(chunks)
    load_document(conn, meta, chunks)
    print(f"✅ {meta['title']}: {len(chunks)} chunks")

if __name__ == "__main__":
    conn = get_conn()
    for f in Path("data/docs").glob("*.md"):
        ingest_file(conn, f)
```

跑一遍，整个 demo 数据集就进库了。这时可以写条查询，核对一下 chunk 数量对不对。

---

## 7. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 不用事务 | 部分写入留下脏数据 | 文档、acl、chunks 放一个事务 |
| 忘了 register_vector | 写 vector 列报错 | 连接后先注册 |
| 检索时才算 tsv | 慢，而且重复计算 | 写入时 to_tsvector |
| 中文用 'simple' 还指望精准分词 | 中文召回差 | 上 zhparser 或应用层 BM25 |
| roles 不展开成多行 | 表达不了多角色权限 | 每个 role 一行 |

---

## 下一步

首次全量入库搞定了，但文档会不断更新，下一篇讲增量重跑：[增量更新](./06-incremental.md)
