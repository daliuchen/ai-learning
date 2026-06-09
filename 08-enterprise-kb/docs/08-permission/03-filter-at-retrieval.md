# EKB 40：检索阶段过滤——把权限焊进每一路召回

> **一句话**：权限过滤必须在**召回阶段**完成——无权文档从一开始就不进候选集。本篇给出向量检索、BM25、混合检索三条路各自怎么接入 ACL 过滤，以及为什么 pgvector 同库 join 在这里是巨大优势。

---

## 1. 过滤要前置到「召回 SQL 里」

回顾原则（[38 篇](./01-permission-is-devil.md)）：不是召回后再筛，而是召回时就只查有权的。落到向量检索，就是在那句 SQL 里加 ACL join：

```python
# retrieve/vector.py —— 加了权限
def vector_search(question, roles: list[str], k=20):
    q_vec = embed_query(question)
    eff_roles = list(set(roles) | {"all"})    # 永远含 all
    rows = conn.execute(
        "SELECT c.id, c.doc_id, c.content, d.title, d.source_url, "
        "       c.embedding <=> %s AS distance "
        "FROM chunks c "
        "JOIN documents d ON d.id = c.doc_id "
        "WHERE d.status = 'active' "
        "  AND EXISTS (SELECT 1 FROM acl a "             # ← 权限过滤
        "              WHERE a.doc_id = c.doc_id "
        "              AND a.role = ANY(%s)) "
        "ORDER BY c.embedding <=> %s LIMIT %s",
        (q_vec, eff_roles, q_vec, k),
    ).fetchall()
    return [...]
```

`EXISTS (...acl... role = ANY(eff_roles))` 保证：**只有可见角色和用户角色有交集的文档，才进入向量排序**。无权文档在排序之前就被 SQL 排除了。

---

## 2. 这就是选 pgvector 的回报

回想 [12 篇](../03-selection/02-vector-db-pgvector.md) 说的：pgvector 最大优势是能和 ACL 同库 join。这里兑现了——**一句 SQL 同时完成「向量检索 + 权限过滤」**。

对比独立向量库（向量在 Qdrant、权限在 Postgres）：

```
独立库的麻烦：
1. 先查 Postgres：该角色能看哪些 doc_id → 可能上千个
2. 带着 doc_id 列表去 Qdrant 做 filter 检索
3. 列表太大时 filter 性能差，还要分页

pgvector：一句 join 搞定，无往返、无列表传递
```

权限过滤是企业知识库的高频操作，pgvector 在这里省下的复杂度是实打实的。

---

## 3. BM25 路也要过滤

混合检索有两路，**两路都要过滤**——只过滤向量路，BM25 路照样会漏出无权文档：

```python
# retrieve/bm25.py —— 加权限
def bm25_search(question, roles: list[str], k=20):
    eff_roles = set(roles) | {"all"}
    scores = bm25.get_scores(tokenize(question))
    ranked = sorted(zip(scores, chunk_rows), key=lambda x: -x[0])
    # 过滤：只保留有权文档的 chunk
    allowed = [
        {**r, "bm25_score": s}
        for s, r in ranked
        if doc_visible(r["doc_id"], eff_roles)   # 查该 doc 的可见角色
    ][:k]
    return allowed
```

> 应用层 BM25 的过滤要查每个 chunk 所属 doc 的可见角色。可以预加载一个 `doc_id → roles` 的内存映射，避免每次查库。**关键是别忘了 BM25 路**——这是常见的权限漏洞：只给向量路加了过滤，BM25 路成了后门。

---

## 4. 混合检索统一入口

把权限参数一路透传到底，混合检索的签名带上 `roles`：

```python
# retrieve/hybrid.py
def hybrid_search(question, roles: list[str], recall_k=20, final_k=20):
    v_hits = vector_search(question, roles, k=recall_k)   # 已过滤
    b_hits = bm25_search(question, roles, k=recall_k)     # 已过滤
    return rrf_fuse(v_hits, b_hits)[:final_k]

# 检索总入口
async def retrieve(question, roles: list[str], k=4):
    queries = await rewrite_query(question)
    all_hits = [hybrid_search(q, roles, 20, 20) for q in queries]
    merged = rrf_fuse_many(all_hits)
    return rerank(question, merged, top_k=k)
```

`roles` 从 API 层一路传进来，每一路召回都带着它。rerank 不涉及权限（候选已经全是有权的），只排序。

---

## 5. 过滤在「检索前」还是「检索中」

两种等价但性能不同的实现：

| 方式 | 做法 | 适合 |
|------|------|------|
| 检索中过滤（本篇） | SQL 里 join acl，边检索边过滤 | pgvector，doc 量大 |
| 检索前圈定 | 先算「该角色能看的 doc_id 集」再限定 | doc 量小，集合不大 |

pgvector 推荐**检索中过滤**（一句 SQL）。如果用独立向量库且角色可见的 doc 不多，「检索前圈定」也可行。无论哪种，**结果一致：无权文档不进候选**。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只给向量路加过滤 | BM25 路成后门 | 每一路都过滤 |
| 召回后用 Python 筛 | 无权内容已进过中间态 | 过滤进 SQL/召回 |
| 忘了带 all | 公开文档查不到 | eff_roles 永远含 all |
| roles 没一路透传 | 中途丢失，过滤失效 | 从 API 透传到每路 |
| 独立向量库还硬塞大 doc 列表 | 性能差 | 用 pgvector join |

---

## 下一步

过滤写好了，怎么验证它对不同角色真的生效：

→ [04-multi-role-test](./04-multi-role-test.md)
