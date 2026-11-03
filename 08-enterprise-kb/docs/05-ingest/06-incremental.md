# EKB 26：文档变更后的增量更新

> 文档会修改、会删除，也会不断新增。每次都全量重新 ingest，既慢又浪费，embedding 费用还得再付一遍。这篇讲增量更新：用内容哈希判断哪些文档变了，只处理这些文档，并把它们的旧 chunk 清理干净。

---

## 1. 为什么需要增量

知识库不是建一次就完事的。制度改版、流程调整、新文档加入，如果每次都全量重新 ingest，会有三个问题：几千个 chunk 重新 embed 要等很久；用 API 的话 embedding 费用重复支付；重建期间在线检索的结果还可能前后不一致。

增量更新的目标很简单：只动变了的部分，没变的文档一概不碰。

---

## 2. 用内容哈希判断文档是否变了

给每篇文档算一个内容哈希，存进 `documents`，下次 ingest 时拿来比对：

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

    if old and old["content_hash"] == new_hash:   # 连接用的是 dict_row
        print(f"⏭  跳过（未变化）: {meta['title']}")
        return
    if old:
        delete_document(conn, old["id"])  # 删旧的全部 chunk + acl
    load_full(conn, meta, body, new_hash) # 重新解析入库
```

哈希没变就跳过；变了就先删旧的、再写新的。只有真正改过的文档才会被重新处理。

---

## 3. 把旧 chunk 删干净

更新一篇文档，就是删掉它的旧 chunk 和 acl，再写入新的。最省事的办法是外键级联删除：

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

不能只更新 content 而不删旧 chunk。如果文档改短了，原来多出来的那些 chunk 会变成幽灵 chunk，继续被检索到，系统就会答出已经删掉的内容。先删干净再重建最稳妥。

---

## 4. 几种变化分别怎么处理

| 变化 | 处理 |
|------|------|
| 新增文档 | 直接 ingest（哈希查不到旧记录） |
| 修改文档 | 哈希不同 → 删旧 chunk + 重新 ingest |
| 删除文档 | 源没了 → 删 documents（级联删 chunk/acl） |
| 文档下线（暂时） | 不删，`status='archived'`，检索时过滤掉 |

下线和删除要分开处理。临时下线只打 `status` 标记，检索时用 `WHERE status='active'` 过滤掉；彻底删除才真正删行。这样下线的文档随时能恢复，不必重新 ingest。

---

## 5. 评估集也要跟着改

文档更新之后，评估集往往也得同步调整：

- 制度改版，旧的答案要点过期了，要更新对应用例的 `answer_points`
- 新增了文档，就针对它补几条评估用例
- 删除了文档，要检查有没有用例的 `expected_doc_ids` 还指向它

不跟着改，评估集就会和实际的知识库脱节，测出来的分数也就不准了。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 每次全量重新 ingest | 慢、贵 | 用哈希判断，只处理变化的文档 |
| 更新时不删旧 chunk | 幽灵 chunk 答出旧内容 | 删干净再重建 |
| 没有 ON DELETE CASCADE | 删文档后留下孤儿 chunk/acl | 建表时设级联 |
| 下线和删除不分 | 误删后难恢复 | 下线用 status，删除才真删 |
| 文档更新了评估集没更新 | 标尺失真 | 同步维护评估用例 |

---

## 下一步

数据都进库了，接下来搭一个最朴素的端到端 RAG，也就是 MVP 的核心：[单路向量检索](../06-basic-rag/01-vector-retrieval.md)
