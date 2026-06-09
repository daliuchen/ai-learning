# EKB 27：单路向量检索——MVP 的检索

> **一句话**：MVP 阶段用最朴素的检索——把问题 embed 成向量，在 pgvector 里找最近的 top-k 个 chunk。先不管 BM25、不管 rerank、不管权限。这一步的目标是**打通「问题 → 相关片段」这条链**，建立 baseline，后面再逐步增强。

---

## 1. 最小检索实现

```python
# retrieve/vector.py
from generate.embedder import embed_query
from db import get_conn
from pgvector.psycopg import register_vector

def vector_search(question: str, k: int = 5) -> list[dict]:
    q_vec = embed_query(question)           # 问题 → 向量（查询侧处理）
    conn = get_conn()
    register_vector(conn)
    rows = conn.execute(
        "SELECT c.id, c.doc_id, c.content, c.section_path, "
        "       d.title, d.source_url, "
        "       c.embedding <=> %s AS distance "
        "FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE d.status = 'active' "
        "ORDER BY c.embedding <=> %s "
        "LIMIT %s",
        (q_vec, q_vec, k),
    ).fetchall()
    return [dict(zip(
        ["id", "doc_id", "content", "section_path", "title", "source_url", "distance"],
        r)) for r in rows]
```

就一句 SQL：`ORDER BY embedding <=> q_vec LIMIT k`。`<=>` 是余弦距离，越小越近。join `documents` 是为了顺便带回标题/链接（引用要用）和过滤已下线文档。

---

## 2. 为什么 MVP 先不加别的

新手会想「既然知道要混合检索 + rerank，干脆一次做全」。但 MVP 纵切的价值在于：

```
先跑通单路向量 → 测出 baseline（比如 recall@5 = 0.62）
→ 加 BM25 → 重测（0.71，+0.09）→ 知道 BM25 值多少
→ 加 rerank → 重测（0.81，+0.10）→ 知道 rerank 值多少
```

**每个增强的价值都被单独量化**。如果一上来全堆上，跑出 0.81，你根本不知道这 0.81 里 BM25 占多少、rerank 占多少，也不知道哪个其实没用。详见 [01-intro/04-mvp-first](../01-intro/04-mvp-first.md)。

---

## 3. 距离阈值：初步的「答不出」信号

向量检索总会返回 k 个结果——哪怕问题和知识库毫无关系，它也会返回「最不相关的 k 个」。所以要看**距离**：如果最近的 chunk 距离都很大（相似度很低），说明知识库里大概没有相关内容。

```python
RELEVANCE_THRESHOLD = 0.35   # 余弦距离阈值，需用评估集校准

def vector_search_with_gate(question, k=5):
    results = vector_search(question, k)
    # 过滤掉明显不相关的
    relevant = [r for r in results if r["distance"] < RELEVANCE_THRESHOLD]
    return relevant
```

这是兜底的第一道防线（生成层还有第二道，见 [04-say-i-dont-know](./04-say-i-dont-know.md)）。阈值不能拍脑袋，要用评估集里的「答不出」用例校准——既不能把有答案的也滤掉，又要拦住无关问题。

---

## 4. top-k 取多少

MVP 阶段建议 k=5 起步，但要意识到这是个权衡：

| k 小（3） | k 大（10） |
|-----------|-----------|
| 噪声少，prompt 短，便宜 | 召回全，少漏 |
| 但可能漏掉相关片段 | 但噪声多，模型易被带偏 |

MVP 先用 5，后面加了 rerank 后，策略会变成「检索阶段召回多（k=20），rerank 精排到少（top 3-5）」——召回要广、入 prompt 要精。详见 [07-retrieval/04-rerank](../07-retrieval/04-rerank.md)。

---

## 5. 验证检索本身（脱离生成）

检索写完，先**单独测它**，别急着接生成。用评估集只跑检索层，看 recall：

```python
# 只测检索，不生成
for case in load_testset():
    docs = [r["doc_id"] for r in vector_search(case["question"], k=5)]
    recall = recall_at_k(docs, case["expected_doc_ids"], 5)
    print(f"#{case['id']} recall@5={recall}")
```

这就是第 04 章「分层评估」的好处——检索能单独打分，你能确认「相关片段确实被召回了」，再去接生成。如果检索这层就漏了，生成再好也白搭。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| MVP 就堆全套检索 | 增强价值无法归因 | 先单路向量建 baseline |
| 不看距离，无脑返回 k 个 | 无关问题也硬给片段 | 加距离阈值过滤 |
| 阈值拍脑袋定 | 误滤或漏拦 | 用评估集校准 |
| 检索没单独测就接生成 | 答错了分不清哪层 | 先单独测检索 recall |
| 查询不走 embed_query | 和文档侧处理不一致 | 用统一查询侧编码 |

---

## 下一步

检索到片段了，交给生成层——用 Pydantic AI 输出结构化答案：

→ [02-structured-output](./02-structured-output.md)
