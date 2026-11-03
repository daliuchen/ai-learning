# EKB 40：在检索阶段按权限过滤每一路召回

> 权限过滤必须在召回阶段完成，让无权文档从一开始就进不了候选集。这篇分别讲向量检索、BM25 和混合检索三条路怎么接上 ACL 过滤，也顺带说明 pgvector 能和业务表同库 join 在这里省了多少事。

---

## 1. 把过滤写进召回 SQL

[为什么权限是企业知识库的难点](./01-permission-is-devil.md) 那篇定下的原则是：召回时就只查有权的文档，不在召回之后再筛。落到向量检索上，就是在那句 SQL 里加一个 ACL 条件：

```python
# retrieve/vector.py —— 加了权限
def vector_search(question, roles: list[str], k=20):
    q_vec = Vector(embed_query(question))       # 查询参数必须包 Vector（pgvector.Vector）
    eff_roles = list(set(roles) | {"all"})    # 永远含 all
    rows = conn.execute(
        "SELECT c.id, c.doc_id, c.content, c.section_path, d.title, d.source_url, "
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
    return rows
```

`EXISTS (...acl... role = ANY(eff_roles))` 保证只有可见角色和用户角色有交集的文档才参与向量排序，无权文档在排序之前就被 SQL 排除了。

---

## 2. 选 pgvector 的回报

[向量库选型](../03-selection/02-vector-db-pgvector.md) 那篇说过，pgvector 最大的好处是能和 ACL 表同库 join。到这里就兑现了：一句 SQL 同时完成向量检索和权限过滤。

换成独立的向量库（向量放在 Qdrant，权限放在 Postgres）会麻烦得多：

```
独立库的麻烦：
1. 先查 Postgres：该角色能看哪些 doc_id → 可能上千个
2. 带着 doc_id 列表去 Qdrant 做 filter 检索
3. 列表太大时 filter 性能差，还要分页

pgvector：一句 join 搞定，无往返、无列表传递
```

企业知识库每次查询都要做权限过滤，pgvector 在这里省下的复杂度很实在。

---

## 3. HNSW 近似索引加过滤，可能少返回甚至返回空

上面的 SQL 逻辑没问题，但 pgvector 的 HNSW 是近似索引：它先从索引里取出 `hnsw.ef_search`（默认 40）个最近邻候选，然后才应用 `WHERE` 过滤。如果当前角色能看的文档只占很小一部分，这 40 个候选大多是无权文档，过滤完只剩几条。这算不上越权，问题出在漏召回：有权的相关文档明明在库里却没搜出来，员工看到的是「未找到」。

举个例子（示意）：2 万条向量，只有 0.2%（40 条）对目标角色可见，强制走 HNSW 索引，`LIMIT 10`：

```
默认（不开迭代扫描）            → 返回 1 条
SET hnsw.iterative_scan = relaxed_order → 返回 10 条
SET hnsw.iterative_scan = strict_order  → 返回 10 条
```

解法是 pgvector 0.8.0 开始提供的迭代扫描：索引扫描没凑够 `LIMIT` 条时继续往下扫，直到凑够，或者碰到 `hnsw.max_scan_tuples`（默认 20000）的上限：

```python
# 在查询所在的事务里开（SET LOCAL 只对当前事务生效；psycopg 默认就开着事务）
conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")   # 或 strict_order
```

几点补充：

- `strict_order` 保证结果严格按距离排序；`relaxed_order` 允许轻微乱序，召回更好。我们后面还要 rerank，乱序无所谓，所以用 `relaxed_order`。
- 小知识库（几千个 chunk）的查询规划器常常直接走全表扫描，碰不到这个问题；数据量上去，或者某个角色的可见比例很低时才会出现。所以多角色测试里要专门造一个低可见比例的用例。
- 也有别的办法：按空间建分区或 partial index，或者把过滤列也放进索引。官方 README 的建议是：过滤条件选择性高，就给过滤列建索引；取值少用 partial index，取值多用分区。

---

## 4. BM25 这一路也要过滤

混合检索有两路，两路都要过滤。只给向量路加了过滤，BM25 路照样会漏出无权文档：

```python
# retrieve/bm25.py —— 加权限
# doc_roles：启动时从 acl 表预加载的 {doc_id: {可见角色}}，ACL 变更时同步刷新
def bm25_search(question, roles: list[str], k=20):
    eff_roles = set(roles) | {"all"}
    # 1. 先按权限圈定候选：无权文档的 chunk 不参与打分，也进不了候选集
    allowed_idx = [i for i, r in enumerate(chunk_rows)
                   if doc_roles.get(r["doc_id"], set()) & eff_roles]
    if not allowed_idx:
        return []
    # 2. 只给有权 chunk 打分（rank_bm25 的 get_batch_scores 按下标给子集打分）
    scores = bm25.get_batch_scores(tokenize(question), allowed_idx)
    ranked = sorted(zip(scores, allowed_idx), key=lambda x: -x[0])[:k]
    return [{**chunk_rows[i], "bm25_score": s} for s, i in ranked]
```

> BM25 路和向量路一样，先按权限圈定、再排序取 top-k，不是全量打分后再筛。`doc_roles` 放内存，免得每次查库；IDF 这类语料统计仍按全库计算，只影响分数，不会把无权内容带进结果。别漏了 BM25 这一路，只给向量路加过滤、让 BM25 成了后门，是很常见的权限漏洞。

---

## 5. 混合检索的统一入口

权限参数要一路透传到底，混合检索的函数签名也带上 `roles`：

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

`roles` 从 API 层一路传进来，每一路召回都带着它。rerank 不涉及权限，因为候选已经全是有权文档，它只负责排序。

---

## 6. 检索前圈定，还是检索中过滤

有两种实现，结果一样，性能不同：

| 方式 | 做法 | 适合 |
|------|------|------|
| 检索中过滤（本篇向量路） | SQL 里 join acl，边检索边过滤 | pgvector，文档量大 |
| 检索前圈定（本篇 BM25 路） | 先算出该角色能看的 doc_id 集合，再限定范围 | 文档量小，集合不大；应用层自己实现的检索 |

用 pgvector 就推荐检索中过滤，一句 SQL 搞定。如果用的是独立向量库，而且角色能看的文档不多，检索前圈定也行。两种做法的结果相同：无权文档进不了候选。

---

## 7. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 只给向量路加过滤 | BM25 路成了后门 | 每一路都过滤 |
| 召回后再用 Python 筛 | 无权内容已经进过中间环节 | 过滤写进 SQL 或召回逻辑 |
| 忘了带 all | 公开文档查不到 | eff_roles 始终包含 all |
| HNSW 先取候选再过滤，没开迭代扫描 | 可见比例低的角色漏召回（只返回 0 到几条） | `SET LOCAL hnsw.iterative_scan = relaxed_order`（pgvector 0.8+） |
| 查询向量直接传 list | `vector <=> double precision[]` 报错 | 包一层 `Vector(...)` |
| roles 没有一路透传 | 中途丢了，过滤失效 | 从 API 层透传到每一路 |
| 用独立向量库还硬塞一大串 doc 列表 | 性能差 | 用 pgvector join |

---

## 下一步

过滤写好了，接下来验证它对不同角色是否真的生效：

→ [多角色测试](./04-multi-role-test.md)
