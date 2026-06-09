# EKB 12：向量库选型——为什么是 pgvector

> **一句话**：企业知识库通常几千~几万 chunk，这个规模**根本不需要专用向量数据库**。pgvector（Postgres 扩展）足够快、少一个组件、还能和业务数据/ACL 同库 join。专用向量库是为亿级向量和独立扩缩容准备的，用在这里是过度工程。

---

## 1. 先算一笔规模账

选向量库前，先估算你的真实数据量：

```
假设：500 篇文档，平均每篇 10 页，每页约 500 字
总字数 ≈ 500 × 10 × 500 = 250 万字
按每 chunk ~300 字切 → 约 8000 个 chunk
```

**8000 个向量**。这是什么概念？pgvector 用 HNSW 索引，百万级向量都是毫秒级检索。8000 个？全表暴力扫都不到几十毫秒。

专用向量库（Milvus/Qdrant/Weaviate）的主场是**千万~十亿级向量**。拿它来装 8000 个向量，等于开重卡送外卖。

---

## 2. 候选对比

| 方案 | 适合规模 | 运维成本 | 能否和业务数据 join | 本项目适配 |
|------|----------|----------|---------------------|-----------|
| **pgvector** | < 百万级 | 低（就是 Postgres） | ✅ 直接 join | ✅✅✅ |
| Qdrant | 百万~亿级 | 中（独立服务） | ❌ 要跨库 | 过度 |
| Milvus | 亿级+ | 高（一堆组件） | ❌ | 远超需求 |
| Chroma | 原型/小规模 | 低 | ❌ | 可以，但不如 pgvector 通用 |
| 纯长上下文（无向量库） | 极小知识库 | 无 | — | 见第 5 节 |

---

## 3. pgvector 用起来什么样

建表 + 索引（数据模型那篇已给过，这里看检索）：

```sql
-- 启用扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- chunks 表里 embedding vector(1024)，建 HNSW 索引
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);
```

向量检索就是一句 SQL：

```sql
-- 找和查询向量最相近的 5 个 chunk（<=> 是余弦距离）
SELECT id, doc_id, content
FROM chunks
ORDER BY embedding <=> %(query_vec)s
LIMIT 5;
```

Python 侧（用 psycopg + pgvector）：

```python
import psycopg
from pgvector.psycopg import register_vector

conn = psycopg.connect("postgresql://postgres:ekb@localhost/postgres")
register_vector(conn)

def search(query_vec, k=5):
    return conn.execute(
        "SELECT id, doc_id, content "
        "FROM chunks ORDER BY embedding <=> %s LIMIT %s",
        (query_vec, k),
    ).fetchall()
```

就这么简单。没有额外服务、没有额外 SDK 的学习成本。

---

## 4. pgvector 最大的隐藏优势：能和 ACL join

这是本项目选它的**决定性理由**。权限过滤需要「只在用户可见的文档里检索」，用 pgvector 一句 SQL 就 join 完成：

```sql
-- 检索 + 权限过滤一气呵成
SELECT c.id, c.doc_id, c.content
FROM chunks c
JOIN acl a ON a.doc_id = c.doc_id
WHERE a.role = %(user_role)s          -- 只看该角色可见的文档
ORDER BY c.embedding <=> %(query_vec)s
LIMIT 5;
```

如果用独立向量库，向量在 Qdrant、权限在 Postgres，你得**先查权限拿到 doc_id 列表，再带着列表去 Qdrant 过滤**——两次网络往返、还要处理列表过大的问题。pgvector 一句话搞定。详见 [08-permission/03-filter-at-retrieval](../08-permission/03-filter-at-retrieval.md)。

---

## 5. 一个 2026 的新选项：小到不用向量库

如果知识库**特别小**（比如几十篇文档，全部加起来几万 token），可以考虑**完全跳过向量检索**——把整个知识库塞进长上下文窗口，配合 prompt caching：

```
所有文档 → 拼成一个大 context（缓存住）→ 每次提问直接问
```

适用条件：知识库总量 < 模型窗口的一半，且更新不频繁（缓存才划算）。这是 [07 手册「长上下文 vs RAG」](/docs/07-context-engineering/07-long-context/02-long-context-vs-rag) 的真实落地点。但企业知识库通常超出这个量级，所以本项目走 RAG 路线。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 几千 chunk 上 Milvus | 过度工程，多组件运维 | pgvector |
| 向量库和权限库分离 | 过滤要跨库、两次往返 | pgvector 同库 join |
| 不建 HNSW 索引 | 量大时检索变慢 | 建索引 |
| 向量维度写死 | 换 embedding 模型要改表 | 维度随模型，换模型时迁移 |
| 极小知识库也硬上 RAG | 增加复杂度 | 考虑长上下文方案 |

---

## 下一步

向量库定了，下一个变量是生成向量的 embedding 模型——中文场景怎么选：

→ [03-embedding-selection](./03-embedding-selection.md)
