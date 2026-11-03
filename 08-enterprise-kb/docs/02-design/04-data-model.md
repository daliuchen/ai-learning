# EKB 09：以权限为中心的数据模型设计

> 数据模型是整个系统的地基。在企业知识库里，权限要在一开始就设计进表结构，事后补一个字段是补不全的。这篇给出完整的表设计，并解释 chunk 为什么要存文档元数据、ACL 为什么要单独建表。

---

## 1. 五张核心表

```sql
-- 文档：一篇制度/手册/产品文档
CREATE TABLE documents (
    id          BIGSERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    source_url  TEXT,                       -- 原文链接，给引用回链用
    space       TEXT NOT NULL,              -- 所属空间：hr / it / product ...
    status      TEXT NOT NULL DEFAULT 'active',  -- active / archived
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 分块：文档切出来的片段，是检索的最小单位
CREATE TABLE chunks (
    id           BIGSERIAL PRIMARY KEY,
    doc_id       BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content      TEXT NOT NULL,
    section_path TEXT,                       -- 「第3章 > 报销标准」，给引用定位
    embedding    vector(1024),              -- 向量，维度随 embedding 模型
    tsv          tsvector,                   -- BM25/全文检索用
    chunk_index  INT NOT NULL               -- 在原文中的顺序
);

-- 访问控制：哪些角色能看哪篇文档
CREATE TABLE acl (
    doc_id  BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    role    TEXT NOT NULL,                   -- engineer / hr / finance / all ...
    PRIMARY KEY (doc_id, role)
);

-- 评估用例：问题 → 期望命中的文档
CREATE TABLE eval_cases (
    id               BIGSERIAL PRIMARY KEY,
    question         TEXT NOT NULL,
    expected_doc_ids BIGINT[] NOT NULL,
    asker_role       TEXT NOT NULL DEFAULT 'all'  -- 以什么角色提问
);

-- 问答日志：每次问答的记录，反馈回流用
CREATE TABLE query_logs (
    id            BIGSERIAL PRIMARY KEY,
    user_role     TEXT,
    question      TEXT NOT NULL,
    retrieved_ids BIGINT[],                  -- 检索到哪些 chunk
    answer        TEXT,
    found         BOOLEAN,                    -- 是否找到了依据
    feedback      SMALLINT,                  -- +1 有用 / -1 没用 / NULL 未评
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

---

## 2. 几个设计决策

### 2.1 为什么 chunk 要存 `doc_id`、`section_path`、`source_url`（在 doc 上）

为了引用溯源。检索命中的是 chunk，可要回答「这个答案出自哪篇文档第几节、点哪个链接能看原文」，就必须能从 chunk 一路查到文档元数据。chunk 如果只存裸文本，引用就做不出来。

> 这是需求决定数据模型的一个例子：第 06 篇定下的可信、可追溯约束，落到表结构上，就是 chunk 必须能关联到文档元数据。

### 2.2 为什么 ACL 单独建表，而不是给 documents 加一列

如果在 documents 里加一个 `visible_role TEXT`，一篇文档就只能对应一个角色。现实中一篇文档常常对多个角色可见，比如「考勤制度」对 `all` 可见，「薪资细则」对 `hr` 和 `finance` 可见。

单独的 `acl(doc_id, role)` 多对多表才能表达一篇文档对应多个可见角色，检索时用它 join 过滤。详见[ACL 模型](../08-permission/02-acl-model.md)。

### 2.3 为什么 chunk 同时存 `embedding` 和 `tsv`

因为要做混合检索。`embedding` 供向量语义检索用，`tsv`（tsvector）供 BM25 和关键词检索用。两路数据在同一张表里，检索时各取所需，再做融合。详见[混合检索](../07-retrieval/03-hybrid-fusion.md)。

### 2.4 为什么要 `query_logs`

上线第一天就要记录每次问答，否则反馈没有地方回流。有了 `retrieved_ids`、`found`、`feedback` 这三个字段，事后就能分析哪些问题答不好、哪些引用总被标错。

---

## 3. 索引

```sql
-- 向量检索索引（HNSW，pgvector 0.5+；带权限过滤请用 0.8+ 的迭代扫描，见 08 章）
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);

-- 全文检索索引
CREATE INDEX ON chunks USING gin (tsv);

-- ACL 过滤常按 doc_id + role 查
CREATE INDEX ON acl (role);
```

向量索引和全文索引各建一个，分别服务混合检索的两路。性能主要取决于这两个索引。

---

## 4. 数据关系

```
documents 1 ──< chunks      （一篇文档切成多个 chunk）
documents 1 ──< acl         （一篇文档对应多个可见角色）
eval_cases ── expected_doc_ids → documents（评估期望命中）
query_logs ── retrieved_ids   → chunks    （日志记录检索结果）
```

`documents` 在中心，`chunks`（检索单位）和 `acl`（权限）都挂在它下面。内容和权限分开存，再通过 doc_id 关联起来。

---

## 5. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| chunk 只存裸文本 | 无法引用溯源 | 存 doc_id + section_path |
| 权限塞进 documents 的一列 | 表达不了多角色可见 | 单独建 acl 多对多表 |
| 只存 embedding 不存 tsv | 做不了混合检索 | 两个字段都留 |
| 没有 query_logs | 反馈无法回流 | 第一天就记录问答 |
| 向量列不建 HNSW 索引 | 检索走全表扫描，慢 | 建 hnsw 索引 |

---

## 下一步

- 把这些表组装成完整架构 → [整体架构](./05-architecture.md)
- 权限表怎么用于检索过滤 → [ACL 模型](../08-permission/02-acl-model.md)
