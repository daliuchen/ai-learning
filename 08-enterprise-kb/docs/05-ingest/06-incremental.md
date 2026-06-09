# EKB 26：增量更新——文档改了怎么办

> **一句话**：文档会改、会删、会新增。每次全量重 ingest 既慢又浪费（还要重新付 embedding 费用）。本篇讲怎么做增量更新——只处理变了的文档，靠内容哈希判断变化，并干净地替换旧 chunk。

---

## 1. 为什么需要增量

知识库不是一次性的。制度改版、流程更新、新文档加入——如果每次都全量重 ingest：

- **慢**：几千 chunk 重新 embed，等很久
- **贵**：API embedding 重复付费
- **影响在线**：重建期间检索可能不一致

增量更新的目标：**只动变化的部分**，没变的文档碰都不碰。

---

## 2. 怎么判断文档变了：内容哈希

给每篇文档算一个内容哈希，存进 `documents`。下次 ingest 时比对：

```python
import hashlib

def content_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

# documents 表加一列
# ALTER TABLE documents ADD COLUMN content_hash TEXT;
```

ingest 时的判断逻辑：

```python
def ingest_file(conn, path):
    raw = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    new_hash = content_hash(raw)

    old = conn.execute(
        "SELECT id, content_hash FROM documents WHERE source_url = %s",
        (meta.get("source_url"),),
    ).fetchone()

    if old and old[1] == new_hash:
        print(f"⏭  跳过（未变化）: {meta['title']}")
        return
    if old:
        delete_document(conn, old[0])     # 删旧的全部 chunk + acl
    load_full(conn, meta, body, new_hash) # 重新解析入库
```

哈希没变 → 跳过；变了 → 删旧 + 写新。这样只有真正改过的文档才会被重新处理。

---

## 3. 干净地删除旧 chunk

文档更新 = 删掉它的旧 chunk + acl，再写新的。靠外键级联删除最省事：

```sql
-- 建表时 chunks/acl 都 ON DELETE CASCADE 引用 documents
-- 删文档时，它的 chunks 和 acl 自动一起删
DELETE FROM documents WHERE id = %s;
```

```python
def delete_document(conn, doc_id):
    conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
    # chunks 和 acl 因 ON DELETE CASCADE 自动清理
```

**注意**：不能只更新 content 不删旧 chunk——文档改短了的话，旧文档多出来的 chunk 会变成「幽灵 chunk」继续被检索到，答出已删除的内容。**删干净再重建**是最安全的。

---

## 4. 三种变化的处理

| 变化 | 处理 |
|------|------|
| 新增文档 | 直接 ingest（哈希查不到旧记录） |
| 修改文档 | 哈希不同 → 删旧 chunk + 重新 ingest |
| 删除文档 | 源没了 → 删 documents（级联删 chunk/acl） |
| 文档下线（暂时） | 不删，`status='archived'`，检索时过滤掉 |

「下线」和「删除」要分开：临时下线用 `status` 标记，检索时 `WHERE status='active'` 过滤；彻底删除才真删行。这样下线的文档能随时恢复，不用重新 ingest。

---

## 5. 增量更新与评估的配合

文档更新后，**评估集也可能要跟着更新**：

- 制度改版后，旧答案要点过期 → 更新对应评估用例的 `answer_points`
- 新增文档 → 加几条针对它的评估用例
- 删除文档 → 检查有没有评估用例指向了已删文档（`expected_doc_ids` 失效）

否则评估集会和实际知识库脱节，测出来的分数失真。**数据变，标尺也要跟着维护。**

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 每次全量重 ingest | 慢、贵 | 哈希判断，只处理变化 |
| 更新时不删旧 chunk | 幽灵 chunk 答出旧内容 | 删干净再重建 |
| 没有 ON DELETE CASCADE | 删文档留下孤儿 chunk/acl | 建表时设级联 |
| 下线和删除不分 | 误删难恢复 | 下线用 status，删除才真删 |
| 文档更新但评估集没更 | 标尺失真 | 同步维护评估用例 |

---

## 下一步

数据全进库了。现在搭最朴素的端到端 RAG——MVP 的核心：

→ [06-basic-rag/01-vector-retrieval](../06-basic-rag/01-vector-retrieval.md)
