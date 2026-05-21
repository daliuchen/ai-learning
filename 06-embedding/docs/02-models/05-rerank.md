# Rerank 模型：被严重低估的"补刀"利器

> **一句话**：Rerank 是把"召回 top 100"二次精排成"真正最相关的 top 5"——cross-encoder 设计让它比 bi-encoder embedding **更准**，但因为慢只能跑在小集合上。两阶段（embedding 召回 + reranker 精排）是工业 RAG 主流架构。

---

## 1. 为啥需要 rerank

embedding 的局限：

```
embedding 编码两段文字成独立向量 → 比 cosine
  ↑
  快但"理解"浅 - 跨段交互信息丢失
```

cross-encoder（reranker）：

```
[query, doc] 拼一起 → 模型一起看 → 输出"相关度分数"
  ↑
  慢但"理解"深 - 模型能看到 query/doc 的所有词怎么互动
```

---

## 2. 架构对比

| | Bi-encoder (embedding) | Cross-encoder (reranker) |
|---|---|---|
| 输入 | query / doc 分别过 | [query+doc] 一起过 |
| 输出 | 两个 vector → cosine | 一个 score 0-1 |
| 速度 | 快（doc 可预 embed） | 慢（每次都要算） |
| 准度 | 中 | 高（提升 5-15%） |
| 用法 | 索引召回 | top-N 精排 |

---

## 3. 主流 reranker

| 模型 | 类型 | 备注 |
|------|------|------|
| `Cohere rerank-multilingual-v3.0` | 商业 API | 多语言强，公认最佳之一 |
| `cohere/rerank-english-v3.0` | 商业 API | 英文 |
| `BAAI/bge-reranker-large` | 开源 | 中文强 |
| `BAAI/bge-reranker-v2-m3` | 开源 | 多语 + 长文 |
| `Alibaba-NLP/gte-reranker` | 开源 | 强 |
| `jinaai/jina-reranker-v2-base` | 开源 | jina 出品 |
| `mixedbread-ai/mxbai-rerank-large` | 开源 | 强 |

---

## 4. Cohere rerank 实战

```python
import cohere


co = cohere.Client()


query = "如何取消订阅"
docs = [
    "如何关闭自动续费",
    "如何登录账号",
    "停止订阅的方法",
    "重置密码教程",
    "退款流程",
]


resp = co.rerank(
    query=query,
    documents=docs,
    model="rerank-multilingual-v3.0",
    top_n=3,
)

for r in resp.results:
    print(f"  index={r.index}  score={r.relevance_score:.4f}  doc={docs[r.index]}")
```

输出大致：

```
  index=2  score=0.95  doc=停止订阅的方法
  index=0  score=0.82  doc=如何关闭自动续费
  index=4  score=0.21  doc=退款流程
```

---

## 5. BGE Reranker（开源）

### 5.1 FlagEmbedding 用法

```python
from FlagEmbedding import FlagReranker


reranker = FlagReranker("BAAI/bge-reranker-large", use_fp16=True)


pairs = [
    ["如何取消订阅", "如何关闭自动续费"],
    ["如何取消订阅", "如何登录账号"],
    ["如何取消订阅", "停止订阅的方法"],
]


scores = reranker.compute_score(pairs, normalize=True)
# scores: [0.92, 0.05, 0.95]
```

### 5.2 sentence-transformers 用法

```python
from sentence_transformers import CrossEncoder


reranker = CrossEncoder("BAAI/bge-reranker-large", max_length=512)


pairs = [[query, doc] for doc in docs]
scores = reranker.predict(pairs)


# 排序
import numpy as np
top_k = np.argsort(-scores)[:3]
for i in top_k:
    print(f"  score={scores[i]:.4f}  doc={docs[i]}")
```

---

## 6. 完整两阶段 pipeline

```python
# demos/models/05_rerank_pipeline.py
import numpy as np
from sentence_transformers import SentenceTransformer, CrossEncoder


# Stage 1: 召回（bi-encoder embedding，快）
embedder = SentenceTransformer("BAAI/bge-base-zh-v1.5")

corpus = [
    "如何关闭自动续费",
    "如何登录账号",
    "停止订阅的方法",
    "重置密码教程",
    "退款流程",
    "联系客服的渠道",
    "免费试用如何延期",
    "升级套餐流程",
    # ... 假设有 10 万条
]

corpus_emb = embedder.encode(corpus, normalize_embeddings=True)


# Stage 2: 精排（cross-encoder reranker，慢但准）
reranker = CrossEncoder("BAAI/bge-reranker-large", max_length=512)


def search(query: str, top_recall=100, top_final=5):
    # 1. embedding 召回
    q_emb = embedder.encode([f"为这个句子生成表示以用于检索相关文章：{query}"], normalize_embeddings=True)
    sims = corpus_emb @ q_emb[0]
    recall_idx = np.argsort(-sims)[:top_recall]
    candidates = [corpus[i] for i in recall_idx]

    # 2. rerank
    pairs = [[query, doc] for doc in candidates]
    rerank_scores = reranker.predict(pairs)
    rerank_order = np.argsort(-rerank_scores)[:top_final]

    return [candidates[i] for i in rerank_order]


print(search("如何取消订阅"))
```

10 万文档 → 召回 100 → rerank 100 → 最终 5。**性价比远高于直接精排 10 万**。

---

## 7. 性能 / 延迟

| Reranker | latency per 100 pairs (CPU) | (GPU) |
|----------|---------------------------|-------|
| bge-reranker-large | 5-8s | 200ms |
| bge-reranker-v2-m3 | 4-6s | 150ms |
| Cohere rerank API | ~300-500ms | (API) |
| GPT-4o as judge | ~3-5s | (API) |

**生产建议**：

- 召回 top-100 → rerank top-100 → 取 top-5
- 想再快 → 召回 top-50 → rerank top-50
- 实时业务（延迟 < 100ms）→ 用 Cohere API 或本地 reranker GPU 部署

---

## 8. 提升效果实测

某客服 KB，10K 文档，evalset 200 条：

| 方案 | Recall@5 | Latency P95 |
|------|----------|-------------|
| BGE embedding only | 88% | 15ms |
| OpenAI 3-large only | 90% | 25ms |
| BGE + bge-reranker-large | 95% | 90ms |
| BGE + Cohere rerank-v3 | 96% | 350ms |

**Rerank 平均提 5-7% Recall**，是性价比最高的 RAG 优化手段之一。

---

## 9. 跟 LLM-as-judge 区别

| | Reranker | LLM-as-judge |
|---|---|---|
| 模型 | 专门训的 cross-encoder | 通用 LLM (GPT-4) |
| 输出 | 单个 score | 自由文本 + 推理 |
| 准度 | 高 | 也高 |
| 延迟 | 100-500ms | 1-5s |
| 成本 | 低（自托管）| 高（API token） |
| 解释性 | 无 | 有（能给 reason） |

实战：

- 主流量用 reranker
- 评测 / debug / 训数据用 LLM-as-judge

---

## 10. 把 rerank 加进向量库

向量库通常**不内置 rerank**。流程：

```
1. 向量库召回 top-100 docs + 它们的 text
2. 你的服务把 (query, docs) 喂给 reranker
3. rerank 排序后取 top-N 给 LLM
```

Cohere 提供 hosted rerank，可以直接 `co.rerank()`。

Qdrant 1.10+ 内置 `rerank` 字段（接 Cohere API）。

---

## 11. 选型

```
预算紧 / 数据合规 → BGE-reranker-large / BGE-reranker-v2-m3
中文强 → BGE-reranker-large
多语言强 → BGE-reranker-v2-m3 / Cohere rerank-multilingual-v3
英文为主 → mxbai-rerank-large / Cohere rerank-english-v3
要长文 → BGE-reranker-v2-m3 / Cohere v3
```

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| 把所有 doc 都 rerank | 太慢，先用 embedding 召回 top-100 |
| 用 embedding 模型当 reranker | 不行，cross-encoder 是单独模型 |
| 不归一化 reranker score | 直接 argsort 就行，不用 normalize |
| 不同 reranker 分数混比 | 分数尺度不同，单独排各自的 |

---

## 13. 下一步

- 📖 MTEB 选型决策 → [06-mteb-selection.md](./06-mteb-selection.md)
- 📖 检索 pipeline 整合 → [05-retrieval/05-rerank-pipeline.md](../05-retrieval/05-rerank-pipeline.md)
- 📖 端到端 RAG → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
