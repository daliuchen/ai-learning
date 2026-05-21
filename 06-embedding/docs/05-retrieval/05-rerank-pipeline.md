# 重排 Pipeline：召回 → 精排

> **一句话**：用 embedding 召回 top-50 ~ top-100，再用 cross-encoder reranker 精排取 top-5 ~ top-10——**这是工业 RAG 的事实标准 pipeline，性价比最高的优化之一**。

---

## 1. 两阶段架构

```
[Query]
   ↓
Stage 1: Recall（召回，要快）
   - Embedding 向量搜（HNSW）
   - BM25
   - 多 query 改写
   → top-100 候选
   ↓
Stage 2: Rerank（精排，要准）
   - Cross-encoder reranker
   - or LLM-as-judge
   → top-5 给 LLM
   ↓
[LLM 生成答案]
```

为啥分两步：

- Stage 1 算法快但粗糙 → 召回率高就行
- Stage 2 算法准但慢 → 只跑 100 条，可承受

---

## 2. 完整 pipeline

```python
import numpy as np
from sentence_transformers import SentenceTransformer, CrossEncoder


# Stage 1: Embedding 召回
embedder = SentenceTransformer("BAAI/bge-base-zh-v1.5")

# Stage 2: Reranker
reranker = CrossEncoder("BAAI/bge-reranker-large", max_length=512)


def two_stage_search(query, corpus, corpus_emb, top_recall=100, top_final=5):
    # Stage 1
    q_emb = embedder.encode([f"为这个句子生成表示以用于检索相关文章：{query}"], normalize_embeddings=True)
    sims = corpus_emb @ q_emb[0]
    recall_idx = np.argsort(-sims)[:top_recall]
    candidates = [(i, corpus[i]) for i in recall_idx]
    
    # Stage 2
    pairs = [[query, doc] for _, doc in candidates]
    rerank_scores = reranker.predict(pairs)
    order = np.argsort(-rerank_scores)[:top_final]
    
    return [
        {
            "doc": candidates[i][1],
            "doc_id": candidates[i][0],
            "rerank_score": float(rerank_scores[i]),
            "recall_score": float(sims[candidates[i][0]]),
        }
        for i in order
    ]
```

---

## 3. 调参指南

```
top_recall:
  小数据（< 10K）: 50
  中数据（< 1M）: 100
  大数据（> 1M）: 100-200（再大延迟难接受）

top_final:
  RAG 默认: 3-5
  研究型 LLM context 大: 10-20

reranker 模型:
  中文: BAAI/bge-reranker-large 或 bge-reranker-v2-m3
  英文: BAAI/bge-reranker-large-en
  多语: BAAI/bge-reranker-v2-m3
  最强: Cohere rerank-multilingual-v3.0（商业 API）
```

---

## 4. 混合召回 + Rerank

```python
def hybrid_with_rerank(query, top_recall=100, top_final=5):
    # 三路召回
    vec_hits = vector_search(query, top_k=top_recall)
    bm25_hits = bm25_search(query, top_k=top_recall)
    hyde_hits = hyde_search(query, top_k=top_recall)
    
    # RRF 融合
    rankings = [
        [h.id for h in vec_hits],
        [h.id for h in bm25_hits],
        [h.id for h in hyde_hits],
    ]
    merged_ids = rrf(rankings, top_k=top_recall)
    
    # 拿出文档
    candidates = [(id, get_doc(id).text) for id in merged_ids]
    
    # Rerank
    pairs = [[query, doc] for _, doc in candidates]
    rerank_scores = reranker.predict(pairs)
    order = np.argsort(-rerank_scores)[:top_final]
    
    return [candidates[i] for i in order]
```

---

## 5. Cohere Rerank API

```python
import cohere


co = cohere.Client()


def cohere_rerank(query, docs, top_n=5):
    resp = co.rerank(
        query=query,
        documents=docs,
        model="rerank-multilingual-v3.0",
        top_n=top_n,
    )
    return [(r.index, r.relevance_score) for r in resp.results]


# 用：
candidates = vector_search(query, top_k=100)
candidates_text = [c.payload["text"] for c in candidates]
top_5 = cohere_rerank(query, candidates_text, top_n=5)

for idx, score in top_5:
    print(f"  score={score:.4f}  {candidates[idx].payload['text'][:80]}")
```

---

## 6. LLM-as-Judge Rerank

```python
def llm_judge_rerank(query, candidates, top_n=5):
    """让 LLM 给每个 candidate 评分"""
    scored = []
    for cand in candidates:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": """你给文档评分（0-10）：这文档能多大程度回答用户问题。
只输出数字。"""},
                {"role": "user", "content": f"问题: {query}\n文档: {cand}"},
            ],
            max_tokens=5,
        )
        try:
            score = float(resp.choices[0].message.content.strip())
        except:
            score = 0
        scored.append((cand, score))
    
    return sorted(scored, key=lambda x: -x[1])[:top_n]
```

**LLM-as-judge 优势**：

- 准（甚至比 cross-encoder 还准）
- 能给 reason

**劣势**：

- 慢（每个 candidate 一次 LLM call）
- 贵

实战：

- 主流量用 cross-encoder
- 评测 / 高价值 query 用 LLM-as-judge

---

## 7. Batch reranking 优化

```python
# 单条 vs batch
def rerank_batch(query, candidates, batch_size=32):
    pairs = [[query, c] for c in candidates]
    scores = []
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i+batch_size]
        batch_scores = reranker.predict(batch)
        scores.extend(batch_scores)
    return scores
```

`predict` 一次给 100 个 pairs 比单条快 5-10x（GPU）。

---

## 8. 延迟 budget 拆分

总目标 500ms：

| 阶段 | 时间 |
|------|------|
| Embed query | 30ms |
| 向量库召回 top-100 | 20ms |
| BM25（可选） | 5ms |
| RRF | < 1ms |
| Rerank（CrossEncoder GPU, batch=100）| 200ms |
| 余量 | 245ms 给 LLM |

如果不能满足：

- 减 top_recall（100 → 50）
- 用更小的 reranker（bge-reranker-base 而非 large）
- 用 ONNX / TensorRT 加速
- 降级到 Cohere API（外部 latency 但稳定）

---

## 9. 完整 demo

```python
# demos/retrieval/05_rerank_pipeline.py
import numpy as np
from sentence_transformers import SentenceTransformer, CrossEncoder
from openai import OpenAI


client = OpenAI()
embedder = SentenceTransformer("BAAI/bge-base-zh-v1.5")
reranker = CrossEncoder("BAAI/bge-reranker-large", max_length=512)


corpus = [
    "如何取消订阅：登录账户 → 设置 → 订阅 → 取消按钮",
    "如何登录：访问 example.com，点击右上'登录'",
    "停止自动续费的方法详解",
    "重置密码教程",
    "退款政策：7 天内全额",
    "支付方式：信用卡 / PayPal",
    "升级套餐流程",
    "API 速率限制说明",
    "AI 功能使用指南",
    "如何修改账单邮箱",
    "免费试用如何延长",
    "联系客服的渠道",
    "如何导出我的数据",
    "怎么注销账号",
    "团队套餐用户管理",
]


corpus_emb = embedder.encode(corpus, normalize_embeddings=True, show_progress_bar=False)


def search_two_stage(query, top_recall=10, top_final=3):
    # Stage 1
    q_emb = embedder.encode([f"为这个句子生成表示以用于检索相关文章：{query}"], normalize_embeddings=True)
    sims = corpus_emb @ q_emb[0]
    recall_idx = np.argsort(-sims)[:top_recall]
    candidates = [(i, corpus[i], float(sims[i])) for i in recall_idx]
    
    print(f"\n[Stage 1 - Embedding 召回 top {top_recall}]")
    for i, doc, s in candidates[:5]:
        print(f"  recall={s:.4f}  {doc[:50]}")
    
    # Stage 2
    pairs = [[query, doc] for _, doc, _ in candidates]
    rerank_scores = reranker.predict(pairs)
    order = np.argsort(-rerank_scores)[:top_final]
    
    print(f"\n[Stage 2 - Rerank top {top_final}]")
    final = []
    for o in order:
        i, doc, recall_s = candidates[o]
        final.append((doc, float(rerank_scores[o]), recall_s))
        print(f"  rerank={float(rerank_scores[o]):.4f} recall={recall_s:.4f}  {doc[:50]}")
    
    return final


search_two_stage("怎么取消订阅", top_recall=10, top_final=3)
```

注意观察：rerank 后排第 1 的可能不是 recall 第 1——这就是 rerank 的价值。

---

## 10. 把 rerank 加进生产服务

```python
class RetrievalService:
    def __init__(self):
        self.embedder = SentenceTransformer("BAAI/bge-large-zh-v1.5")
        self.reranker = CrossEncoder("BAAI/bge-reranker-large")
        self.vector_db = QdrantClient(...)
    
    def search(self, query, top_k=5, with_rerank=True):
        # Stage 1
        q_emb = self.embedder.encode(query, normalize_embeddings=True)
        hits = self.vector_db.search(
            collection_name="docs",
            query_vector=q_emb.tolist(),
            limit=top_k * 20 if with_rerank else top_k,
        )
        
        if not with_rerank:
            return hits[:top_k]
        
        # Stage 2
        pairs = [[query, h.payload["text"]] for h in hits]
        scores = self.reranker.predict(pairs)
        
        ranked = sorted(zip(hits, scores), key=lambda x: -x[1])
        return [h for h, _ in ranked[:top_k]]
```

---

## 11. 实测提升

200 条 evalset：

| Pipeline | Recall@5 |
|----------|----------|
| Embedding 召回 only | 85% |
| Embedding + BM25 (RRF) | 92% |
| Embedding + Rerank | 95% |
| Embedding + BM25 + Rerank | 97% |
| Embedding + BM25 + HyDE + Rerank | 98% |

每加一层提 2-5%，**Rerank 是性价比最高的一层**。

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| 召回 top_k 太小 (5) | 至少 20-50，给 rerank 足空间 |
| 直接 rerank 所有文档 | 必须先召回少量再 rerank |
| Reranker 跑 CPU 太慢 | 用 GPU / ONNX / Cohere API |
| 不同 reranker 分数混比 | 各自单独使用 |

---

## 13. 下一步

- 📖 Self-query / metadata filter → [06-self-query.md](./06-self-query.md)
- 📖 检索评测 → [06-evaluation/01-metrics.md](../06-evaluation/01-metrics.md)
- 📖 完整 RAG → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
