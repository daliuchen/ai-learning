# EKB 25：写入 pgvector——文档、chunk、ACL、全文索引

> **一句话**：把解析好的文档元数据写进 `documents`、可见角色写进 `acl`、带向量的 chunk 写进 `chunks`（同时生成全文检索用的 `tsv`）。本篇给出完整的入库代码，并解释为什么要在写入时就把 BM25 用的 tsvector 一起算好。

---

## 1. 入库的整体流程

```
解析产物 + 向量
  │
  ├─ documents 表：title, space, source_url, updated_at
  ├─ acl 表：     (doc_id, role) 多行
  └─ chunks 表：  content, section_path, embedding, tsv, chunk_index
```

一篇文档的入库是个事务——文档、ACL、chunks 要么全成功要么全回滚，避免「文档进了但 chunk 没进」的脏数据。

---

## 2. 写文档 + ACL

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
        ).fetchone()[0]

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

`roles: [all]` → acl 一行 `(doc_id, 'all')`；`roles: [hr, finance]` → 两行。检索时按角色 join 这张表过滤。

---

## 3. 写 chunk + 同时生成 tsv

关键：写 chunk 时，**让 Postgres 顺便算好全文检索向量 `tsv`**，供 BM25/关键词检索用：

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

`to_tsvector('simple', content)` 在写入时就把内容转成全文检索向量。这样检索时两条路（向量 + 全文）都现成，不用临时算。

> 中文全文检索注意：Postgres 内置分词对中文不友好。生产中文场景常用 `pg_jieba`/`zhparser` 扩展做中文分词，或在应用层用 `rank-bm25` 自己算 BM25（详见 [07-retrieval/02-bm25](../07-retrieval/02-bm25.md)）。`'simple'` 配置适合演示和中英混合的关键词匹配。

---

## 4. 注册 pgvector 类型

写 `embedding`（Python list）到 `vector` 列前，要让 psycopg 认识这个类型：

```python
conn = psycopg.connect("postgresql://postgres:ekb@localhost/postgres")
register_vector(conn)    # 之后就能直接传 list 给 vector 列
```

没注册的话，传 list 会报类型错误。这是 pgvector + psycopg 的固定一步。

---

## 5. 一个完整的 ingest 入口

把 parse → chunk → embed → load 串起来：

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

跑一遍，整个 demo 数据集就进库了。这时可以写个查询验证 chunk 数对不对。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 不用事务 | 部分写入留脏数据 | 文档+acl+chunks 一个事务 |
| 忘了 register_vector | 写 vector 列报错 | 连接后注册 |
| tsv 检索时才算 | 慢、重复算 | 写入时 to_tsvector |
| 中文用 'simple' 还指望精准分词 | 中文召回差 | 上 zhparser 或应用层 BM25 |
| roles 不展开成多行 | 权限表达不了多角色 | 每个 role 一行 |

---

## 下一步

首次全量入库会了，但文档会更新——怎么增量重跑：

→ [06-incremental](./06-incremental.md)
