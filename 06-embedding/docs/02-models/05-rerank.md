# Rerank 模型：召回之后的精排

> Rerank 做的事，是把向量召回的前 100 条重新排一遍，挑出最相关的前 5 条。它用的是 cross-encoder，把 query 和文档放在一起看，所以比 bi-encoder 的 embedding 更准；代价是慢，只能用在小候选集上。embedding 召回加 reranker 精排的两阶段结构，是现在生产 RAG 最常见的做法。

版本：Cohere 模型名以[官方 rerank 文档](https://docs.cohere.com/docs/rerank)（2026 年 11 月）为准，参数名对照 `cohere 7.3.0` 的 `ClientV2.rerank`；本地 reranker（FlagEmbedding / CrossEncoder）需要 torch。

---

## 1. 为什么需要 rerank

embedding 的局限在于它把两段文字分别编码：

```
embedding 编码两段文字成独立向量 → 比 cosine
  ↑
  快但"理解"浅 - 跨段交互信息丢失
```

cross-encoder（reranker）则把两段放在一起编码：

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
| 输出 | 两个 vector → cosine | 一个相关性分数 |
| 速度 | 快（doc 可预 embed） | 慢（每次都要算） |
| 准度 | 中 | 高（能看到 query 和 doc 的逐词交互） |
| 用法 | 索引召回 | top-N 精排 |

---

## 3. 主流 reranker

| 模型 | 类型 | 备注 |
|------|------|------|
| `Cohere rerank-v5.0-pro` / `rerank-v5.0-fast` | 商业 API | 最新一代，32k 上下文，100+ 语言（中文在重点语言之列）；仅文本；独有 Context Focus（返回文档内最相关片段） |
| `Cohere rerank-v4.0-pro` / `-fast` | 商业 API | 上一代，32k 上下文 |
| `Cohere rerank-v3.5` | 商业 API | 4k 上下文，多语言 |
| `Cohere rerank-english-v3.0` / `rerank-multilingual-v3.0` | 商业 API | 老版本，4k |
| `Qwen/Qwen3-Reranker-0.6B/4B/8B` | 开源 | 与 Qwen3-Embedding 配套，多语言、长文 |
| `BAAI/bge-reranker-v2-m3` | 开源 | 多语 + 长文，轻量，中文可用 |
| `BAAI/bge-reranker-large` | 开源 | 老一代（v1），中文强 |
| `jinaai/jina-reranker-v2-base-multilingual` / 更新的 Jina reranker | 开源/API | jina 出品，协议见模型卡 |
| `mixedbread-ai/mxbai-rerank-large-v2` | 开源 | 强 |

---

## 4. Cohere rerank 实战

```python
import cohere


co = cohere.ClientV2()   # 读取环境变量 CO_API_KEY


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
    model="rerank-v5.0-fast",   # 追求质量用 rerank-v5.0-pro
    top_n=3,
)

for r in resp.results:
    print(f"  index={r.index}  score={r.relevance_score:.4f}  doc={docs[r.index]}")
```

输出大致是这样：

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

## 6. 完整的两阶段流程

```python
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

10 万条文档先召回 100 条，rerank 这 100 条，最后留 5 条。比起直接对 10 万条做精排，成本低得多。

---

## 7. 性能 / 延迟（示意）

| Reranker | latency per 100 pairs (CPU) | (GPU) |
|----------|---------------------------|-------|
| bge-reranker-large | 5-8s | 200ms |
| bge-reranker-v2-m3 | 4-6s | 150ms |
| Cohere rerank API | ~300-500ms | (API) |

生产上的常见配置：召回 100 条、rerank 100 条、取前 5。想更快就召回和 rerank 都降到 50。如果是实时业务，要压到 100ms 以内，只能用 GPU 部署的本地 reranker，而且候选数要降到 50 左右（按上表，GPU 上 100 条要 150–200ms）。

---

## 8. 效果对比（示意）

某个客服知识库，10K 文档，evalset 200 条。带 rerank 的两行都是先召回 30 条再精排，bge-reranker-large 跑在 GPU 上（按第 7 节，100 对约 200ms，30 对约 60ms，加上召回就是表里的 90ms 量级）：

| 方案 | Recall@5 | Latency P95 |
|------|----------|-------------|
| BGE embedding only | 88% | 15ms |
| OpenAI 3-large only | 90% | 25ms |
| BGE + bge-reranker-large | 95% | 90ms |
| BGE + Cohere rerank（v3 时代） | 96% | 350ms |

加上 rerank 后 Recall@5 提高 7–8 个百分点，在 RAG 的各种优化里，它是投入产出比最高的之一。

---

## 9. 跟 LLM-as-judge 的区别

| | Reranker | LLM-as-judge |
|---|---|---|
| 模型 | 专门训的 cross-encoder | 通用 LLM |
| 输出 | 单个 score | 自由文本 + 推理 |
| 准度 | 高 | 也高 |
| 延迟 | 100-500ms | 1-5s |
| 成本 | 低（自托管）| 高（API token） |
| 解释性 | 无 | 有（能给 reason） |

我的分工是：线上流量走 reranker；评测、排查问题、造训练数据时再用 LLM-as-judge。

---

## 10. 把 rerank 接进向量库

向量库一般不自带 rerank，流程要自己串：

```
1. 向量库召回 top-100 docs + 它们的 text
2. 你的服务把 (query, docs) 喂给 reranker
3. rerank 排序后取 top-N 给 LLM
```

Cohere 有托管的 rerank，直接调 `co.rerank()` 即可。

Qdrant 的 Query API（`query_points` + `prefetch`）能在库内做多阶段召回和 RRF 融合，但 cross-encoder 精排还是得你在服务端自己调 reranker。

---

## 11. 选型

```
多语言强 → Qwen3-Reranker / BGE-reranker-v2-m3 / Cohere rerank-v5.0
英文为主 → mxbai-rerank-large-v2 / Cohere rerank-v5.0
要长文 → Cohere rerank-v5.0（32k）/ Qwen3-Reranker / BGE-reranker-v2-m3
预算紧 / 数据合规 → 自部署 Qwen3-Reranker-0.6B 或 BGE-reranker-v2-m3
```

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| 把所有 doc 都 rerank | 太慢，先用 embedding 召回 top-100 |
| 用 embedding 模型当 reranker | 不行，cross-encoder 是单独的模型 |
| 纠结要不要归一化 reranker score | 只排序的话直接 argsort，不用 normalize |
| 不同 reranker 的分数放一起比 | 分数尺度不同，各排各的 |

---

## 13. 下一步

- 选型流程：[MTEB 怎么看 + 选型决策树](./06-mteb-selection.md)
- 放进检索流程：[重排 Pipeline：召回 → 精排](../05-retrieval/05-rerank-pipeline.md)
- 端到端 RAG：[完整 RAG Pipeline 实战](../08-applications/01-full-rag.md)
