# EKB 12：为什么向量库选 pgvector

> 企业知识库一般只有几千到几万个 chunk，这个规模用不着专用向量数据库。pgvector 作为 Postgres 扩展，速度够用，少一个组件，还能和业务数据、ACL 在同一个库里 join。专用向量库是为亿级向量和独立扩缩容设计的，放在这里属于过度设计。
>
> 版本：pgvector 扩展 0.8.7（官方 README）；Python 示例基于 `psycopg 3.3`、Python 包 `pgvector 0.5` 和 `pgvector/pgvector:pg17` 镜像。

---

## 1. 先算一笔规模账

选向量库之前，先估一下真实的数据量：

```
假设：500 篇文档，平均每篇 10 页，每页约 500 字
总字数 ≈ 500 × 10 × 500 = 250 万字
按每 chunk ~300 字切 → 约 8000 个 chunk
```

结果是 8000 个向量。pgvector 用 HNSW 索引，百万级向量的检索都在毫秒级；8000 个向量的话，不建索引暴力全表扫也用不了几十毫秒。

Milvus、Qdrant、Weaviate 这些专用向量库，擅长的是千万到十亿级的向量。拿它们装 8000 个向量，好比开重卡送外卖。

---

## 2. 候选对比

| 方案 | 适合规模 | 运维成本 | 能否和业务数据 join | 本项目适配 |
|------|----------|----------|---------------------|-----------|
| pgvector | < 百万级 | 低（就是 Postgres） | 能，直接 join | 最合适 |
| Qdrant | 百万到亿级 | 中（独立服务） | 不能，要跨库 | 过度 |
| Milvus | 亿级以上 | 高（组件多） | 不能 | 远超需求 |
| Chroma | 原型、小规模 | 低 | 不能 | 可以用，但没有 pgvector 通用 |
| 纯长上下文（无向量库） | 极小知识库 | 无 | — | 见第 6 节 |

---

## 3. pgvector 用起来什么样

建表和索引在数据模型那篇已经给过，这里只看检索：

```sql
-- 启用扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- chunks 表里 embedding vector(1024)，建 HNSW 索引
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
```

向量检索就是一条 SQL：

```sql
-- 找和查询向量最相近的 5 个 chunk（<=> 是余弦距离）
SELECT id, doc_id, content
FROM chunks
ORDER BY embedding <=> %(query_vec)s
LIMIT 5;
```

Python 这边用 psycopg 加 pgvector：

```python
import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector

# ⚠️ 顺序：必须先 CREATE EXTENSION，再 register_vector —— 否则找不到 vector 类型而报错
with psycopg.connect("postgresql://postgres:ekb@localhost/postgres", autocommit=True) as c:
    c.execute("CREATE EXTENSION IF NOT EXISTS vector")

conn = psycopg.connect("postgresql://postgres:ekb@localhost/postgres",
                       row_factory=psycopg.rows.dict_row)   # 行取成 dict，后文代码都按 dict 用
register_vector(conn)

def search(query_vec: list[float], k=5):
    return conn.execute(
        "SELECT id, doc_id, content "
        "FROM chunks ORDER BY embedding <=> %s LIMIT %s",
        (Vector(query_vec), k),         # ✅ 查询参数要包成 Vector（或 numpy 数组）
    ).fetchall()
```

这里有个坑。`INSERT` 时直接把 Python `list` 传给 `vector` 列可以成功，因为 Postgres 会按列类型转换；但在查询里给 `embedding <=> %s` 传 `list`，会报 `operator does not exist: vector <=> double precision[]`，原因是 psycopg 把 list 当成了数组。查询参数一律包成 `Vector(...)`，或者传 `numpy.ndarray`。

除此之外没什么复杂的：不用额外起服务，也不用学新的 SDK。

---

## 4. pgvector 最大的隐藏优势：能和 ACL join

这是本项目选它的决定性理由。权限过滤要求只在用户可见的文档里检索，用 pgvector 一条 SQL join 就完成了：

```sql
-- 检索 + 权限过滤一气呵成
SELECT c.id, c.doc_id, c.content
FROM chunks c
JOIN acl a ON a.doc_id = c.doc_id
WHERE a.role = %(user_role)s          -- 只看该角色可见的文档
ORDER BY c.embedding <=> %(query_vec)s
LIMIT 5;
```

如果换成独立向量库，向量在 Qdrant，权限在 Postgres，你就得先查权限拿到 doc_id 列表，再带着列表去 Qdrant 过滤。这要两次网络往返，列表太长时还得另想办法。用 pgvector，一条 SQL 就够了。详见[检索阶段过滤](../08-permission/03-filter-at-retrieval.md)。

---

## 5. 本手册用到的几个 pgvector 细节

| 项 | 说明（出自官方 README） |
|----|------------------------|
| HNSW vs IVFFlat | HNSW 查询性能更好，但构建慢、占内存多，不需要先有数据就能建；IVFFlat 构建快、省内存，但要等有了数据再建。几千到几十万 chunk 直接用 HNSW |
| 召回精度参数 | `SET hnsw.ef_search = 100;`（默认 40，越大召回越高、越慢） |
| 带过滤的检索 | 近似索引是先扫索引、后过滤，过滤条件很严时可能返回不足 LIMIT 条；0.8.0 起可用迭代扫描补齐：`SET hnsw.iterative_scan = strict_order / relaxed_order`，上限由 `hnsw.max_scan_tuples`（默认 20000）控制 |
| 维度上限 | `vector` 能存 16000 维，但建索引最多 2000 维；`halfvec`（半精度，存储减半）索引最多 4000 维 |

后面权限一章会用到迭代扫描，它决定了过滤之后还能不能返回足够多的结果。

---

## 6. 2026 年的另一个选项：小到不用向量库

如果知识库特别小，比如几十篇文档、加起来几万 token，可以考虑完全跳过向量检索，把整个知识库放进长上下文窗口，再配合 prompt caching：

```
所有文档 → 拼成一个大 context（缓存住）→ 每次提问直接问
```

前提是知识库总量小于模型窗口的一半，并且更新不频繁，这样缓存才划算。这就是 [07 手册「长上下文 vs RAG」](/docs/07-context-engineering/07-long-context/02-long-context-vs-rag)在实际项目里的用法。不过企业知识库通常超出这个量级，所以本项目还是走 RAG。

---

## 7. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 几千个 chunk 上 Milvus | 过度设计，多个组件要运维 | 用 pgvector |
| 向量库和权限库分离 | 过滤要跨库，两次往返 | pgvector 同库 join |
| 不建 HNSW 索引 | 数据量大时检索变慢 | 建索引 |
| HNSW 索引加权限过滤后结果变少 | 近似索引先取候选再过滤，可能只剩几条甚至 0 条 | `SET hnsw.iterative_scan = relaxed_order`（0.8.0+，见[检索阶段过滤](../08-permission/03-filter-at-retrieval.md)） |
| 查询参数直接传 list | `operator does not exist: vector <=> double precision[]` | 包成 `Vector(...)` 或 numpy |
| 向量维度 > 2000 还想建索引 | `vector` 类型索引最多 2000 维 | 降维（见 embedding 选型）或存成 `halfvec`（索引上限 4000 维） |
| 向量维度写死 | 换 embedding 模型要改表 | 维度跟着模型走，换模型时做迁移 |
| 极小知识库也硬上 RAG | 徒增复杂度 | 考虑长上下文方案 |

---

## 下一步

向量库定了，下一个要定的是生成向量的 embedding 模型，看中文场景怎么选：

→ [Embedding 选型](./03-embedding-selection.md)
