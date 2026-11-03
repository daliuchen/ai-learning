# MTEB 怎么看 + 选型决策树

> MTEB、C-MTEB、MMTEB 是选 embedding 模型时绕不开的榜单。但只看平均分很容易选错：要按自己的任务（retrieval、classification、clustering）和语种去看对应的子分，最后还得用自己的 evalset 验证。这篇讲怎么读榜，以及我会怎么做选型。

---

## 1. MTEB 是什么

> Massive Text Embedding Benchmark — https://huggingface.co/spaces/mteb/leaderboard

> 截至 2026 年 11 月，MTEB 已演进到 MTEB v2 / MMTEB（论文 arXiv 2502.13595，数百个任务、跨语种），官方库用 `mteb.get_benchmark("MTEB(eng, v2)")` 这类名字载入各个子榜；老的 56 任务英文榜和单独的 C-MTEB 现在多作为历史口径或子榜出现。不同年份、不同版本的榜单分数不能直接比，比如本手册里 OpenAI 3 系列的 62.3 / 64.6 是 v1 口径，千问 v4 官方给的 68.36 是另一种口径。榜单名次变化很快，还有不少是厂商自报，所以本篇不写"当前第一名"，我也不建议按名次选型。

这是一个社区维护的基准，覆盖：

- English：MTEB(eng, v2)（老的 v1 英文榜是 56 个任务）
- Chinese：C-MTEB（约 35 个任务）
- Multilingual：MMTEB（数百个任务，跨语种）
- Code：MTEB(Code, v1)，包含 CoIR 等代码检索任务
- 领域：法律、医疗等领域子榜（子榜名字和任务集随版本变化，以榜单页为准）

子任务类型：

| 类型 | 做什么 | 例子 |
|------|------|------|
| Retrieval | 给 query 找 doc | MS MARCO / NQ |
| Reranking | 排序候选 | SciDocs |
| STS | 句对相似度 | STS-B |
| Classification | 用 embedding 训分类器 | Banking77 |
| Clustering | 同类聚一起 | RedditClustering |
| PairClassification | 二分类 | TwitterURL |
| Summarization | 文档-summary 配对 | SummEval |
| Bitext Mining | 跨语句子配对 | Tatoeba |

---

## 2. 怎么看排行榜

### 2.1 别只看 Average

```
Model A: avg=64.5, Retrieval=50, Classification=80, Clustering=60
Model B: avg=64.0, Retrieval=62, Classification=70, Clustering=55
```

做 RAG 的话，你关心的是 retrieval。这时候 Model B 更合适，哪怕它的平均分略低。

### 2.2 按任务过滤

榜单页面可以按 Task Type 和 Language 筛选，只看你用得到的那几列就行。

### 2.3 看你的语言

中文场景就看 C-MTEB，或 MMTEB 里和中文相关的子榜（入口 https://huggingface.co/spaces/mteb/leaderboard，在页面里筛语言）。模型卡上也经常会自报 CMTEB 分数。

以英文为主训练的模型用在中文上，分数通常明显下降。

### 2.4 看模型大小

榜单上还有这几列：

- Memory（GB）
- Embedding Dim
- Max Tokens

小模型的平均分往往只低一点，速度和部署成本却好得多。

---

## 3. 选型决策树

```
你的主要任务是？
├─ Retrieval / RAG
│   ├─ 语言：英文 → 看 MTEB-EN Retrieval 分
│   │       中文 → 看 C-MTEB Retrieval 分
│   │       多语 → 看 MMTEB Retrieval
│   ├─ 量级：< 1M docs → 在对应子分靠前的模型里挑 3-5 个候选，
│   │                     用自己的 evalset 比，成本暂时不是主要矛盾
│   │       1M-10M docs → 同上，再把存储和查询成本算进去，
│   │                     维度 1024 左右、支持 Matryoshka 截维的更划算
│   │       > 10M docs → 优先 dim ≤ 1024 + Matryoshka，再考虑量化
│   └─ 长文档：> 512 tokens → BGE-m3 / Qwen3-Embedding / jina-embeddings-v5
│
├─ Classification
│   ├─ 大多场景：text-embedding-3-small / bge-base 足够
│   └─ 小数据 → fine-tune > 选最强模型
│
├─ Clustering
│   └─ 看 Clustering 子分，再在自己的数据上比
│
└─ Code
    └─ voyage-code-4 / Jina Code Embeddings
```

合规、预算、团队有没有自部署能力，这几项要在决策树之外再叠加考虑。

---

## 4. 常犯的错

### 错 1：只信榜单，不在自己的数据上验证

模型在 MTEB 上分数高，不代表在你公司的知识库上同样好。一定要用自己的 evalset 测。

### 错 2：直接上最大的模型

3-large 的 API 单价是 3-small 的 6.5 倍（$0.13 vs $0.02 / 百万 token），默认向量 3072 维，是 small（1536 维）的 2 倍，存储和向量检索的开销也跟着翻倍。如果 small 在你的 evalset 上已经够用，多花的钱换不来什么。

### 错 3：跨语种混用模型

拿英文模型（比如老的 bge-large-en）去 embed 中文 query，召回会很差。一个 collection 只用一个模型。

### 错 4：没 fine-tune 就放弃

通用模型在医疗、法律或公司内部业务这类领域可能表现一般，用领域数据 fine-tune 之后常有明显提升，值得先试再决定换不换模型。

---

## 5. 用 mteb 库自己跑

```python
# pip install mteb

import mteb

tasks = mteb.get_tasks(
    languages=["zho"],           # ISO 639-3 语言码
    task_types=["Retrieval"],
)

# mteb 2.x：get_model 载入（没有内置实现的模型回退到 SentenceTransformer）
model = mteb.get_model("BAAI/bge-base-zh-v1.5")

# 旧的 mteb.MTEB(tasks=tasks).run(model, output_folder=...) 已被 mteb.evaluate 取代
results = mteb.evaluate(model, tasks=tasks)

# 官方预置的整套基准用 mteb.get_benchmark，例如英文 v2：
benchmark = mteb.get_benchmark("MTEB(eng, v2)")
# results = mteb.evaluate(model, tasks=benchmark)

# 命令行：mteb run -m <model> -t <task> --output-folder results
# （运行时会下载数据集和模型权重；写法按 mteb 2.x 官方 README）
```

---

## 6. 自建的小 evalset 比 MTEB 更重要

选型时最准的基准，是你自己的数据：

```jsonl
{"query": "如何取消订阅", "expected_docs": ["doc_42", "doc_89"]}
{"query": "怎么联系客服", "expected_docs": ["doc_3", "doc_15", "doc_67"]}
...
```

100-200 条就够用。怎么建见[建评测集：Query → Expected Docs](../06-evaluation/02-build-evalset.md)。

然后拿不同模型比 Recall@5：

```python
models = {
    "openai-3-small": embed_openai_3_small,
    "qwen-v4-1024": embed_qwen_v4,            # 千问 text-embedding-v4（兼容接口）
    "qwen3-embedding-0.6b": embed_qwen3_local,  # 自部署
    "bge-m3": embed_bge_m3,
    "voyage-4": embed_voyage_4,
}


for name, embed_fn in models.items():
    recall = evaluate(embed_fn, my_evalset)
    print(f"{name}: Recall@5 = {recall:.3f}")
```

---

## 7. 我的起步推荐

不想纠结的话，可以从这里开始：

```
小项目 + 英文 → text-embedding-3-small / voyage-4-lite
小项目 + 中文 → 千问 text-embedding-v4（API）或 Qwen3-Embedding-0.6B（自部署）
中等项目 → text-embedding-3-large 截到 1024 维 / voyage-4 / embed-v5.0 + rerank
大项目 + 数据出不去 → Qwen3-Embedding-4B/8B 或 bge-m3 + Qwen3-Reranker / bge-reranker-v2-m3
长文 / 多语言 → bge-m3 / Qwen3-Embedding / embed-v5.0（128k）

以上只是"起点候选"，最终一定以你自己的 evalset 为准。
```

定下来之后，再按需要做调整或 fine-tune。

---

## 8. 模型版本管理

```python
# 配置驱动选型
EMBEDDING_CONFIG = {
    "model_name": "BAAI/bge-large-zh-v1.5",   # 示例；换成你选定的模型
    "model_version": "v1.5",
    "dim": 1024,
    "normalize": True,
    "query_prefix": "为这个句子生成表示以用于检索相关文章：",
    "doc_prefix": "",
}
```

升级模型时按这个顺序来：

1. 新建一个 collection，不要覆盖老的
2. 跑 evalset 对比
3. 确认有提升后，灰度切流量
4. 确认稳定后再下线老 collection

---

## 9. 常见的"看走眼"

| 宣传点 | 实际情况 |
|------|------|
| "avg 65 分的模型" | 你的任务可能只用 retrieval，要看 retrieval 分 |
| "支持 100+ 语言" | 可能主要是英中日韩表现好，小语种偏弱 |
| "MTEB 第一" | 可能只是某个子任务第一，不一定通用 |
| "8K 长文支持" | query 通常很短，长文支持对你未必重要 |
| "1024 维" | 1024 维不一定比 768 维好 |

---

## 10. 完整的选型对比代码

```python
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

## 11. 模型选型速查

| 选型 | 推荐 |
|------|------|
| 商业 API 英文 | text-embedding-3-large / voyage-4 / embed-v5.0 |
| 商业 API 中文/多语 | 千问 text-embedding-v4 / embed-v5.0 / voyage-4 |
| 开源 / 自部署 中文、多语 | Qwen3-Embedding（0.6B/4B/8B）/ BAAI/bge-m3 |
| Matryoshka | text-embedding-3 / text-embedding-v4 / Qwen3-Embedding / voyage-4 |
| 长文 | bge-m3（8K）/ Qwen3-Embedding（32k）/ voyage-4（32k）/ embed-v5.0（128k） |
| 代码 | voyage-code-4 |
| 任务 LoRA | jina-embeddings-v3 / v5 |
| Rerank 配套 | Qwen3-Reranker / bge-reranker-v2-m3 / cohere rerank-v5.0 |

---

## 12. 下一步

模型这一章到这里结束。

- 接着挑向量库：[向量库选型全景 + 决策树](../03-vector-db/01-selection.md)
- 自己建 evalset：[建评测集：Query → Expected Docs](../06-evaluation/02-build-evalset.md)
- 完整 RAG 实战：[完整 RAG Pipeline 实战](../08-applications/01-full-rag.md)
