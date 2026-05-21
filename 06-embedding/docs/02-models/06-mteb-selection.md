# MTEB 怎么看 + 选型决策树

> **一句话**：MTEB / C-MTEB / MMTEB 是 embedding 界的"高考"——但**不要只看 avg 分**，按你的任务（retrieval / classification / clustering）和语种分别看，再用自己的 evalset 验证。

---

## 1. MTEB 是啥

> Massive Text Embedding Benchmark — https://huggingface.co/spaces/mteb/leaderboard

社区基准，覆盖：

- **English**：56+ 任务（MTEB-EN）
- **Chinese**：C-MTEB（约 35 任务）
- **Multilingual**：MMTEB（数百任务，跨语种）
- **Code**：CoIR / CodeXGLUE
- **Domain**：MTEB-Finance / MTEB-Law / MTEB-Bio

子任务类型：

| 类型 | 干啥 | 例子 |
|------|------|------|
| **Retrieval** | 给 query 找 doc | MS MARCO / NQ |
| **Reranking** | 排序候选 | SciDocs |
| **STS** | 句对相似度 | STS-B |
| **Classification** | 用 embedding 训分类器 | Banking77 |
| **Clustering** | 同类聚一起 | RedditClustering |
| **PairClassification** | 二分类 | TwitterURL |
| **Summarization** | 文档-summary 配对 | SummEval |
| **Bitext Mining** | 跨语句子配对 | Tatoeba |

---

## 2. 怎么看排行榜

### 2.1 别只看 Average

```
Model A: avg=64.5, Retrieval=50, Classification=80, Clustering=60
Model B: avg=64.0, Retrieval=62, Classification=70, Clustering=55
```

如果你是做 RAG（retrieval），**Model B 完胜 Model A**，虽然 avg 略低。

### 2.2 按任务过滤

榜单网页可以筛 Task Type / Language。**只看你用得到的列**。

### 2.3 看你的语言

中文场景 → C-MTEB 榜（https://huggingface.co/spaces/mteb/leaderboard?language=zh）。

英文模型在中文上经常掉 5-10 分。

### 2.4 看模型大小

- Memory（GB）
- Embedding Dim
- Max Tokens

小模型可能 avg 低 1-2 分，但速度 / 部署成本好得多。

---

## 3. 实战选型决策树

```
你的主要任务是？
├─ Retrieval / RAG
│   ├─ 语言：英文 → 看 MTEB-EN Retrieval 分
│   │       中文 → 看 C-MTEB Retrieval 分
│   │       多语 → 看 MMTEB Retrieval
│   ├─ 量级：< 1M docs → 任选 top-5 模型
│   │       > 10M docs → 优先 dim ≤ 1024 + Matryoshka
│   └─ 长文档：> 512 tokens → BGE-m3 / jina-embeddings-v3
│
├─ Classification
│   ├─ 大多场景：text-embedding-3-small / bge-base 足够
│   └─ 小数据 → fine-tune > 选最强模型
│
├─ Clustering
│   ├─ 看 Clustering 子分
│   └─ Nomic / mxbai 优势
│
└─ Code
    └─ voyage-code-3 / Jina Code Embeddings
```

合规 / 预算 / 自部署能力是叠加约束。

---

## 4. 不要犯的错

### 错 1：信榜单不验证自己数据

模型在 MTEB 上 80 分不代表在你公司 KB 上 80 分。**必须自己 evalset 验**。

### 错 2：选最大的模型

3-large 比 3-small 慢 1.5x、贵 6.5x，但你的任务可能 small 已经 95% 准了——money / latency 不值。

### 错 3：跨语种模型混用

英文模型给中文 query embed → 召回崩。一个 collection 一个模型。

### 错 4：不 fine-tune 就放弃

通用模型在你的领域（医疗 / 法律 / 内部业务）可能 60 分，fine-tune 后能到 80 分。

---

## 5. 用 mteb 库自己跑

```python
# pip install mteb

import mteb
from sentence_transformers import SentenceTransformer


tasks = mteb.get_tasks(
    languages=["zho_Hans"],
    task_types=["Retrieval"],
)

model = SentenceTransformer("BAAI/bge-base-zh-v1.5")
evaluation = mteb.MTEB(tasks=tasks)
results = evaluation.run(model, output_folder="./mteb_zh_retrieval")


# 自定义 evalset 也行
custom_task = mteb.tasks.Retrieval.MyCustomTask()  # 自己定义
evaluation = mteb.MTEB(tasks=[custom_task])
```

---

## 6. 自己建 mini-evalset 比 MTEB 重要

最准的"选型基准"是你自己的数据：

```jsonl
{"query": "如何取消订阅", "expected_docs": ["doc_42", "doc_89"]}
{"query": "怎么联系客服", "expected_docs": ["doc_3", "doc_15", "doc_67"]}
...
```

100-200 条够用。详见 [06-evaluation/02-build-evalset.md](../06-evaluation/02-build-evalset.md)。

跑不同模型对比 Recall@5：

```python
models = {
    "openai-3-small": embed_openai_3_small,
    "openai-3-large": embed_openai_3_large,
    "bge-base-zh": embed_bge_base,
    "bge-large-zh": embed_bge_large,
    "bge-m3": embed_bge_m3,
}


for name, embed_fn in models.items():
    recall = evaluate(embed_fn, my_evalset)
    print(f"{name}: Recall@5 = {recall:.3f}")
```

---

## 7. 我的"开局推荐"

无脑选：

```
小项目 + 英文 → text-embedding-3-small
小项目 + 中文 → BAAI/bge-base-zh-v1.5（自部署）
中等项目 → text-embedding-3-large 截到 1024 维 + Cohere rerank
大项目 + 数据出不去 → bge-m3 + bge-reranker-v2-m3
长文 / 多语言 → bge-m3
```

之后再针对性微调 / fine-tune。

---

## 8. 模型版本管理

```python
# 配置驱动选型
EMBEDDING_CONFIG = {
    "model_name": "BAAI/bge-large-zh-v1.5",
    "model_version": "v1.5",
    "dim": 1024,
    "normalize": True,
    "query_prefix": "为这个句子生成表示以用于检索相关文章：",
    "doc_prefix": "",
}
```

模型升级时：

1. 起新 collection（不要覆盖老的）
2. 跑 evalset 对比
3. 确认提升 → 灰度切流量
4. 安全后下线老 collection

---

## 9. 常见"看走眼"的情况

| 现象 | 真相 |
|------|------|
| "avg 65 分的模型" | 你的任务可能只用 retrieval，看 retrieval 分 |
| "支持 100+ 语言" | 主要可能英中日韩好，小语种弱 |
| "MTEB 第一" | 可能在某个特定子任务上，不一定通用 |
| "8K 长文支持" | 但 query 通常很短，长文支持对你不重要 |
| "1024 维" | 不等于 1024 维一定比 768 维好 |

---

## 10. 完整选型 demo

```python
# demos/models/06_selection_eval.py
import asyncio
import json
import numpy as np
from sentence_transformers import SentenceTransformer
from openai import AsyncOpenAI


client = AsyncOpenAI()


# 假设有 evalset
EVALSET = [
    {"query": "如何取消订阅", "relevant_docs": [0, 3]},
    {"query": "登录失败怎么办", "relevant_docs": [5, 7]},
    # ...
]


CORPUS = ["如何关闭自动续费", "登录指南", "...", "停止订阅", ...]


async def embed_openai(texts):
    resp = await client.embeddings.create(model="text-embedding-3-small", input=texts)
    return np.array([d.embedding for d in resp.data])


bge = SentenceTransformer("BAAI/bge-base-zh-v1.5")


def embed_bge(texts, is_query=False):
    if is_query:
        texts = [f"为这个句子生成表示以用于检索相关文章：{t}" for t in texts]
    return bge.encode(texts, normalize_embeddings=True)


async def recall_at_k(embed_q_fn, embed_d_fn, k=5):
    corpus_vec = embed_d_fn(CORPUS) if not asyncio.iscoroutinefunction(embed_d_fn) else await embed_d_fn(CORPUS)
    hits = 0
    for case in EVALSET:
        qv = embed_q_fn([case["query"]])[0] if not asyncio.iscoroutinefunction(embed_q_fn) else (await embed_q_fn([case["query"]]))[0]
        sims = corpus_vec @ qv
        top_k = np.argsort(-sims)[:k]
        if any(idx in case["relevant_docs"] for idx in top_k):
            hits += 1
    return hits / len(EVALSET)


async def main():
    print("Evaluating models...")

    bge_recall = await recall_at_k(
        lambda q: embed_bge(q, is_query=True),
        lambda d: embed_bge(d),
        k=5,
    )
    print(f"BGE base zh:        Recall@5 = {bge_recall:.3f}")

    openai_recall = await recall_at_k(embed_openai, embed_openai, k=5)
    print(f"OpenAI 3-small:     Recall@5 = {openai_recall:.3f}")


asyncio.run(main())
```

---

## 11. 02-models 章节小结

| 选型 | 推荐 |
|------|------|
| 商业 API 英文 | text-embedding-3-large / voyage-3 |
| 商业 API 多语 | cohere-multilingual-v3 |
| 开源 / 自部署 中文 | BAAI/bge-large-zh-v1.5 |
| 开源 / 多语 | BAAI/bge-m3 / multilingual-e5-large |
| Matryoshka | text-embedding-3 / nomic-embed |
| 长文 8K | bge-m3 / jina-embeddings-v3 |
| 代码 | voyage-code-3 |
| 任务 LoRA | jina-embeddings-v3 |
| Rerank 配套 | bge-reranker-v2-m3 / cohere-rerank-v3 |

---

## 12. 下一步

- 📖 02-models 完结。开始挑向量库 → [03-vector-db/01-selection.md](../03-vector-db/01-selection.md)
- 📖 自己建 evalset → [06-evaluation/02-build-evalset.md](../06-evaluation/02-build-evalset.md)
- 📖 完整 RAG 实战 → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
