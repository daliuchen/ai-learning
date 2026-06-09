# EKB 09：数据模型设计——权限是第一公民

> **一句话**：数据模型是整个系统的地基，而企业知识库的地基里，**权限**不是附加字段，而是一开始就要焊进去的结构。本篇给出完整的表设计，并解释为什么 chunk 要存文档元数据、为什么 ACL 要独立成表。

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

## 2. 几个关键设计决策

### 2.1 为什么 chunk 要存 `doc_id`、`section_path`、`source_url`（在 doc 上）

因为**引用溯源**。检索命中的是 chunk，但要回答「这答案出自哪篇文档第几节、点哪个链接能看原文」，就得能从 chunk 一路追到文档元数据。如果 chunk 只存裸文本，引用就无从谈起。

> 这是「需求反推数据模型」的典型：第 06 篇定的「可信/可追溯」约束，落到这里就是 chunk 必须能关联到文档元数据。

### 2.2 为什么 ACL 独立成表，而不是 documents 加一列

如果在 documents 里加个 `visible_role TEXT`，就只能「一篇文档对应一个角色」。但现实是**一篇文档常常多个角色可见**（比如「考勤制度」对 `all` 可见，「薪资细则」对 `hr` + `finance` 可见）。

独立的 `acl(doc_id, role)` 多对多表，才能表达「一篇文档 → 多个可见角色」。检索时用它 join 过滤。详见 [08-permission/02-acl-model](../08-permission/02-acl-model.md)。

### 2.3 为什么 chunk 同时存 `embedding` 和 `tsv`

因为我们要做**混合检索**：`embedding` 给向量语义检索用，`tsv`（tsvector）给 BM25/关键词检索用。两路都在同一张表，检索时各取所需再融合。详见 [07-retrieval/03-hybrid-fusion](../07-retrieval/03-hybrid-fusion.md)。

### 2.4 为什么要 `query_logs`

上线第一天就要记录每次问答，否则**反馈无从回流**。`retrieved_ids` + `found` + `feedback` 三个字段，让你能事后分析「哪些问题答不好、哪些引用总被标错」。

---

## 3. 索引（性能关键）

```sql
-- 向量检索索引（HNSW，pgvector 0.5+）
CREATE INDEX ON chunks USING hnsw (embedding vector_cosine_ops);

-- 全文检索索引
CREATE INDEX ON chunks USING gin (tsv);

-- ACL 过滤常按 doc_id + role 查
CREATE INDEX ON acl (role);
```

向量索引和全文索引各建一个，分别服务混合检索的两条路。

---

## 4. 数据关系一图流

```
documents 1 ──< chunks      （一篇文档切成多个 chunk）
documents 1 ──< acl         （一篇文档对应多个可见角色）
eval_cases ── expected_doc_ids → documents（评估期望命中）
query_logs ── retrieved_ids   → chunks    （日志记录检索结果）
```

`documents` 是中心，`chunks`（检索单位）和 `acl`（权限）从它派生——**这正是「内容」和「权限」分离、又通过 doc_id 关联的设计**。

---

## 5. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| chunk 只存裸文本 | 无法引用溯源 | 存 doc_id + section_path |
| 权限塞进 documents 一列 | 无法表达「多角色可见」 | 独立 acl 多对多表 |
| 只存 embedding 不存 tsv | 做不了混合检索 | 两个字段都留 |
| 没有 query_logs | 反馈无法回流 | 第一天就记录问答 |
| 向量列不建 HNSW 索引 | 检索全表扫，慢 | 建 hnsw 索引 |

---

## 下一步

- 把这些表组装成完整架构 → [05-architecture](./05-architecture.md)
- 权限表怎么用于检索过滤 → [08-permission/02-acl-model](../08-permission/02-acl-model.md)
