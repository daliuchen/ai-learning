# EKB 27：MVP 的单路向量检索

> MVP 阶段的检索用最朴素的做法：把问题 embed 成向量，到 pgvector 里找距离最近的 top-k 个 chunk。BM25、rerank、权限这些先都不管。这一步只求打通从问题到相关片段这条链，测出 baseline，后面再一项项往上加。

---

## 1. 最小检索实现

```python
# retrieve/vector.py
from pgvector import Vector
from generate.embedder import embed_query
from db import get_conn                     # 已 register_vector，行是 dict（dict_row）

def vector_search(question: str, k: int = 5) -> list[dict]:
    q_vec = Vector(embed_query(question))   # 问题 → 向量；查询参数要包成 Vector
    conn = get_conn()
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
    return rows                             # dict_row：每行已是 dict
```

核心只有 `ORDER BY embedding <=> q_vec LIMIT k` 这一句。`<=>` 是余弦距离，值越小越近。join `documents` 有两个用处：顺带取回标题和链接（引用要用），以及过滤掉已经下线的文档。

---

## 2. MVP 为什么先不加别的

刚上手的人常会想：反正迟早要做混合检索和 rerank，不如一次做齐。MVP 先只做一路的好处在这里（下面的数字是示意数据）：

```
先跑通单路向量 → 测出 baseline（比如 recall@5 = 0.66）
→ 加 BM25 → 重测（0.79，+0.13）→ 知道 BM25 值多少
→ 加 rerank → 重测（recall@5 0.81，recall@3 0.68 → 0.78）→ 知道 rerank 值多少
```

每一项增强的价值都能单独量出来。要是一开始就全堆上去，跑出个 0.81，你分不清其中 BM25 贡献了多少、rerank 贡献了多少，也看不出哪一项其实没起作用。更多讨论见 [MVP 优先](../01-intro/04-mvp-first.md)。

---

## 3. 距离阈值：最初的「答不出」信号

向量检索永远会返回 k 个结果，哪怕问题和知识库毫不相干，它也会把最接近的 k 个交出来，只不过这些结果其实都不相关。所以要看距离：如果连最近的 chunk 距离都很大（相似度很低），多半说明知识库里没有相关内容。

```python
RELEVANCE_THRESHOLD = 0.35   # 余弦距离阈值，需用评估集校准

def vector_search_with_gate(question, k=5):
    results = vector_search(question, k)
    # 过滤掉明显不相关的
    relevant = [r for r in results if r["distance"] < RELEVANCE_THRESHOLD]
    return relevant
```

这是兜底的第一道防线，生成层还有第二道，见 [答不出就承认](./04-say-i-dont-know.md)。阈值不能拍脑袋定，要拿评估集里的「答不出」用例来校准：既不能把有答案的问题也滤掉，又得拦住无关问题。

---

## 4. top-k 取多少

MVP 阶段我从 k=5 起步，不过这个值本身就是一种取舍：

| k 小（3） | k 大（10） |
|-----------|-----------|
| 噪声少，prompt 短，便宜 | 召回全，少漏 |
| 但可能漏掉相关片段 | 但噪声多，模型易被带偏 |

先用 5。等加了 rerank，策略会变成检索阶段多召回一些（k=20），再由 rerank 精排到 3 到 5 条：召回要宽，进 prompt 的要精。详见 [Rerank 精排](../07-retrieval/04-rerank.md)。

---

## 5. 先单独验证检索

检索写完，先单独测它，别急着接生成。用评估集只跑检索这一层，看 recall：

```python
# 只测检索，不生成
for case in load_testset():
    docs = [r["doc_id"] for r in vector_search(case["question"], k=5)]
    recall = recall_at_k(docs, case["expected_doc_ids"], 5)
    print(f"#{case['id']} recall@5={recall}")
```

第 04 章讲的分层评估在这里就用上了：检索能单独打分，确认相关片段确实召回了，再去接生成。检索这层就漏了的话，生成写得再好也没用。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| MVP 就堆全套检索 | 增强价值无法归因 | 先用单路向量建 baseline |
| 不看距离，固定返回 k 个 | 无关问题也硬塞片段 | 加距离阈值过滤 |
| 阈值拍脑袋定 | 误滤或漏拦 | 用评估集校准 |
| 检索没单独测就接生成 | 答错了分不清是哪层 | 先单独测检索 recall |
| 查询不走 embed_query | 和文档侧处理不一致 | 统一用查询侧编码 |

---

## 下一步

片段检索到了，交给生成层，用 Pydantic AI 输出结构化答案：[结构化生成](./02-structured-output.md)
