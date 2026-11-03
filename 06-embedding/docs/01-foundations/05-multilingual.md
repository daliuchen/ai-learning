# 多语言 Embedding 的工作原理

> 多语言模型把不同语言里意思相同的表达训到同一个向量空间里，"猫"、"cat"、"gato"、"ねこ"彼此靠得很近。这样中文 query 能召回英文文档，反过来也一样。这篇讲它为什么可行、什么时候会出问题、怎么选模型。

---

## 1. 单语言 vs 多语言

| 类型 | 例子 | 适合 |
|------|------|------|
| 单语言 | OpenAI text-embedding-3 系列（虽然标了多语言，但英文最强）、BGE-zh / GTE-zh | 单一语言场景 |
| 多语言 | Cohere embed-v5.0 / voyage-4 / 千问 text-embedding-v4 / Qwen3-Embedding / multilingual-e5 / bge-m3 / jina-embeddings-v3 | 多语言混合、跨语言检索 |

"支持多语言"不等于各语言效果一样好，具体要看 MMTEB、C-MTEB 这类分区域的榜单。

---

## 2. 跨语言检索是怎么做到的

```python
query = "如何取消订阅"  # 中文 query

docs_en = [
    "How to cancel subscription",      # 英文
    "Reset password",
    "Refund policy",
]

# 用多语言 embedding
# 中文 query embedding 跟 "How to cancel subscription" 余弦相似度很高
# → 召回成功
```

原因在训练数据：模型见过海量的中英翻译对，学会了把两种语言里的同一个意思对齐到一起。

---

## 3. 主流多语言模型

### 3.1 Cohere embed（v3 多语言 → v5.0）

```python
import cohere

co = cohere.ClientV2()   # 读取环境变量 CO_API_KEY
resp = co.embed(
    texts=["猫", "cat", "gato"],
    model="embed-v5.0-pro",           # 老的 embed-multilingual-v3.0 仍可用（1024 维，512 token）
    input_type="search_document",  # 或 "search_query"
    embedding_types=["float"],
)
embeddings = resp.embeddings.float_
```

运行这段需要 Cohere API key。v5.0 支持 100 多种语言，默认 2048 维、128k 上下文。它是商业 API，质量高，跨语言效果很好。

### 3.2 intfloat/multilingual-e5-large

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("intfloat/multilingual-e5-large")

# 必须加 prefix
embeddings = model.encode([
    "query: 如何取消订阅",
    "passage: How to cancel subscription",
])
```

支持 100 多种语言，1024 维，开源，可以自己部署。

### 3.3 BAAI/bge-m3

```python
from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)

embeddings = model.encode(
    sentences=["如何取消订阅", "How to cancel subscription"],
    return_dense=True,    # dense embedding
    return_sparse=True,   # 也输出 sparse（BM25-like）
    return_colbert_vecs=True,  # 还可以 ColBERT 多向量
)
```

同样是 100 多种语言、1024 维。它能同时输出 dense、sparse 和 multi-vector 三种表示，上下文长度 8192 tokens。

### 3.4 jinaai/jina-embeddings-v3

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("jinaai/jina-embeddings-v3", trust_remote_code=True)

embeddings = model.encode(
    ["如何取消订阅", "How to cancel subscription"],
    task="retrieval.passage",  # 或 retrieval.query / separation / classification
)
```

100 多种语言，1024 维。内置了按任务切换的 LoRA，检索、分类、聚类各用各的适配器。

---

## 4. 中文专用 vs 多语言

```
单一中文场景：
  BGE-zh-large / GTE-zh-large 通常 > multilingual 模型
  
跨语言场景（中文 query 找英文文档）：
  必须用 multilingual 模型
  
中英混合查询：
  multilingual 模型平衡
  
英文为主：
  OpenAI text-embedding-3 / VoyageAI 通常更强
```

---

## 5. 跨语言 demo

```python
import numpy as np
from sentence_transformers import SentenceTransformer


model = SentenceTransformer("intfloat/multilingual-e5-large")


def encode(texts, is_query=False):
    prefix = "query: " if is_query else "passage: "
    return model.encode([prefix + t for t in texts], normalize_embeddings=True)


query_zh = encode(["如何取消订阅"], is_query=True)[0]

docs = encode([
    "How to cancel subscription",                     # 英文同义
    "How to cancel your subscription on our platform",
    "Reset your password",                            # 英文不相关
    "退款政策",                                       # 中文不相关
    "停止自动续费的方法",                             # 中文同义
])


for doc, sim in zip([
    "EN-同义", "EN-同义(更长)", "EN-不相关",
    "ZH-不相关", "ZH-同义",
], docs @ query_zh):
    print(f"{doc:>15}  sim={sim:.4f}")
```

要看的是相对顺序：两条英文同义文档的相似度应该高于中文的不相关文档（"退款政策"），这说明模型是按意思而不是按语言在比，跨语言检索起作用了。具体数值随模型而变，multilingual-e5 的相似度整体偏高（模型卡提到常集中在 0.7 到 1.0 之间），不要拿绝对值做阈值。

---

## 6. 多语言模型容易出错的地方

1. 同形异义：

   ```
   "python" (en, 编程语言) vs "python" (en, 蟒蛇)
   多语言模型容易混
   ```

2. 小语种：

   ```
   斯瓦希里语 / 蒙古语 / 缅甸语 训练数据少
   → 效果远不如英中
   ```

3. 特定领域术语：

   ```
   "Form W-9" / "ICD-10" 这类英文专业术语
   翻译到中文容易丢失
   → 跨语言检索可能找错
   ```

   解决办法是保留原文、同时加上译文，两种语言都 embed。

4. 代码和公式：

   ```
   多语言模型对代码很弱
   要 embed code → 用 code-embedding 专门模型（Voyage Code / Jina Code）
   ```

---

## 7. instruction / prefix 必须用对

很多多语言模型对 prefix 很敏感：

```python
# ❌ 不加 prefix
model.encode("How to cancel")

# ✅ 按文档要求加
model.encode("query: How to cancel")           # multilingual-e5
model.encode("Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: How to cancel")  # Qwen3-Embedding，只给 query 加
```

Cohere 用的是 input_type 参数：

```python
# Cohere
co.embed(texts, input_type="search_query")
co.embed(texts, input_type="search_document")
co.embed(texts, input_type="classification")
co.embed(texts, input_type="clustering")
```

不同 input_type 给出的向量不一样，不能混着用。

---

## 8. 翻译 + 单语 vs 直接多语

有些团队会绕个弯，先翻译再用单语模型：

```
方案 A（直接多语）：
  用 multilingual 模型 → 中文 query → 直接搜英文 doc

方案 B（翻译 + 单语）：
  中文 query → 翻译成英文 → 用 OpenAI 英文 embedding 搜英文 doc
```

| 方案 | 优 | 劣 |
|------|----|----|
| A | 简单，一步到位 | multilingual 模型质量略低 |
| B | 用更强的单语模型 | 翻译有失真，延迟高 |

B 每个 query 都要多一次翻译，成本高，也不好扩展，所以主流做法是 A。

---

## 9. 跨语言混合检索

BM25（关键词）和 dense（语义）混合时，BM25 那一路要求 query 和文档是同一种语言。常见的做法是分三路：

```
中文 query：
  ↓
分支 1: 中文 BM25 → 中文 docs
分支 2: 翻译成英文 → 英文 BM25 → 英文 docs
分支 3: multilingual embedding → 所有 docs

合并三路 → rerank
```

融合的细节见[BM25 + Dense 融合：RRF / Weighted](../05-retrieval/02-bm25-fusion.md)。

---

## 10. 实战选型

```
单语场景（只用一种语言）：
  → 选该语言最强单语模型（中文 BGE / 英文 OpenAI）

中英为主，偶尔小语种：
  → 千问 text-embedding-v4 / bge-m3 / multilingual-e5

跨语言检索是核心需求：
  → Cohere embed-v5.0 / Qwen3-Embedding / bge-m3，在自己的跨语言 evalset 上比

预算紧：
  → bge-m3 / multilingual-e5 / Qwen3-Embedding-0.6B（开源）

要长上下文：
  → bge-m3（8192 tokens）/ Qwen3-Embedding（32k）
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| 用单语模型做跨语言检索 | 召回会很差，换 multilingual 模型 |
| 中英混排的文档没有切开 | 按语言切成段落分别 embed，或者用 bge-m3 |
| query / doc 没加 prefix | 按官方文档加上 |
| 多个模型的向量存在一起 | 维度和分布都不同，必须分开放到不同 collection |

---

## 12. 下一步

- 图片也能 embedding：[多模态 Embedding：CLIP 原理](./06-multimodal.md)
- 多语言模型的实操对比：[开源模型：Qwen3-Embedding / BGE / Jina / Nomic](../02-models/03-open-source.md)
- 在多语言数据上做混合检索：[BM25 + Dense 融合：RRF / Weighted](../05-retrieval/02-bm25-fusion.md)
