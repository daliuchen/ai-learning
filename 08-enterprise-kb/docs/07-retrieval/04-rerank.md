# EKB 35：Rerank 精排

> 混合检索召回了 10 到 20 条，里面仍有噪声，排序也不一定对。Rerank 用一个更精确、也更贵的模型给这些候选重新打分，挑出最相关的 3 到 5 条放进 prompt。它是「召回宽、精排窄」的最后一环，对答案质量的提升往往也最明显。

---

## 1. 召回之后为什么还要 rerank

向量检索和 BM25 都快，但精度有限。它们用的是轻量的相似度（向量点积、词频），能很快从几千个 chunk 里捞出候选，排序却比较粗：

```
混合检索 top-10：第 1 条其实只是沾边，真正最相关的排在第 6
→ 如果直接取 top-3 进 prompt，最相关的那条被漏掉了
```

Rerank 模型（cross-encoder）把问题和每个 chunk 成对送进去做深度比较，精度高很多，代价是慢。所以不能拿它检索全库，只能给召回回来的少量候选重新排序。

```
全库几千 chunk → [快速检索] → 召回 20 条 → [rerank 精排] → top 3-5 → prompt
                  recall 优先              precision 优先
```

---

## 2. 接入 rerank

可以用 cross-encoder 类的开源 rerank 模型（比如 bge-reranker），也可以用商用 rerank API。国内用千问的话，百炼有现成的 rerank 模型：官方文档列出了 `qwen3-rerank`、`qwen3.7-text-rerank` 等，单次最多 500 个候选；`gte-rerank` 已公告下线，官方推荐换成 `qwen3-rerank`。接口是 DashScope 的 `text-rerank` 服务，请求体是 `input.query` 加 `input.documents`，返回每个候选的 `relevance_score`，取值 0 到 1，而且只在同一次请求内可比。域名和鉴权以百炼文档为准。下面的代码用本地 cross-encoder（可选依赖 `sentence-transformers`）：

```python
# retrieve/rerank.py
from sentence_transformers import CrossEncoder

reranker = CrossEncoder("BAAI/bge-reranker-large")

def rerank(question: str, candidates: list[dict], top_k: int = 4) -> list[dict]:
    pairs = [(question, c["content"]) for c in candidates]
    scores = reranker.predict(pairs)          # 每个候选一个相关度分
    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)
    ranked = sorted(candidates, key=lambda c: -c["rerank_score"])
    return ranked[:top_k]
```

接进检索流水线：

```python
from retrieve.hybrid import hybrid_search
from retrieve.rerank import rerank

def retrieve(question: str, k: int = 4) -> list[dict]:
    candidates = hybrid_search(question, recall_k=20, final_k=20)  # 召回宽
    return rerank(question, candidates, top_k=k)                   # 精排窄
```

这里 `final_k=20`，把混合检索的候选全部交给 rerank，再精排到 `k=4`。

---

## 3. rerank 分数也能用来判断答不出

rerank 分数比检索距离更能反映实际的相关程度，拿来做兜底信号也更合适：

```python
RERANK_THRESHOLD = 0.3    # 评估集校准（不同 rerank 模型分数分布不同，不能照抄；API 返回的分数是单次请求内相对值，阈值要多测）

def retrieve_with_gate(question, k=4):
    ranked = retrieve(question, k=k * 2)
    relevant = [c for c in ranked if c["rerank_score"] > RERANK_THRESHOLD]
    return relevant[:k]    # 全部低于阈值 → 返回空 → 走兜底
```

cross-encoder 判断相关与否更可靠，所以用 rerank 分数设阈值比用向量距离准。第 30 篇讲的第一道兜底防线，因此变得更牢靠。

---

## 4. rerank 的成本

rerank 有代价，要算清楚：

| 维度 | 影响 |
|------|------|
| 延迟 | cross-encoder 给 20 个候选打分，要多花几十到几百毫秒 |
| 成本 | 本地模型吃算力，商用 rerank API 按次计费 |
| 候选数 | 候选越多越准，也越慢 |

我的经验是给 rerank 20 条候选性价比不错，加到 50 条以上提升有限，速度却明显变慢。对延迟敏感时，召回数和 rerank 候选数要一起调，详见 [成本与延迟预算](../10-production/02-cost-latency.md)。

---

## 5. 评估 rerank 的提升

加上 rerank 后跑评估，重点看 recall@3（精排后的头部质量）和引用准确率。下表为示意数据：

```
                 recall@5    recall@3    引用准确率
混合(无rerank)    0.79        0.68        0.74
混合 + rerank     0.81        0.78        0.86   ← recall@3 和引用准确率大涨
```

rerank 对 recall@5 帮助不大，提升主要体现在 recall@3 和引用准确率上。原因是候选本来就在召回结果里，rerank 做的是把最相关的那几条顶到最前面。进 prompt 的片段质量高了，模型答得更准，引用也更对。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 不做 rerank，直接取检索 top-3 | 最相关的可能被漏掉 | 召回宽，rerank 后再取窄 |
| 给 rerank 的候选太少 | 精排没有余地 | 召回 20 条给它 |
| 用 rerank 检索全库 | 非常慢 | rerank 只排候选 |
| 只看 recall@5 评估 rerank | 看不出它的作用 | 看 recall@3 和引用准确率 |
| 忽略 rerank 的延迟 | 线上超时 | 候选数和延迟一起权衡 |

---

## 下一步

检索侧还剩最后一项增强：在源头改写口语化的问题。

→ [Query 改写](./05-query-rewrite.md)
