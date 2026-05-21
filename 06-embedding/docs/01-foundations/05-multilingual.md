# 多语言 Embedding 怎么工作的

> **一句话**：多语言模型把不同语言的"同义表达"训到同一向量空间——"猫" / "cat" / "gato" / "ねこ" 在向量空间里靠得很近，所以中文 query 能召回英文文档（反之亦然）。

---

## 1. 单语言 vs 多语言

| 类型 | 例子 | 适合 |
|------|------|------|
| **单语言** | OpenAI text-embedding-3 系列（虽然标"多语言"但英文最强）、BGE-zh / GTE-zh | 单一语言场景 |
| **真·多语言** | Cohere embed-multilingual-v3 / multilingual-e5 / bge-m3 / jina-embeddings-v3 | 多语言混合 / 跨语言检索 |

⚠️ "支持多语言" ≠ "效果一样好"。看 MMTEB / C-MTEB 等区域榜单。

---

## 2. 跨语言检索的 magic

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

为啥能 work：训练时见过海量 (zh, en) 翻译对，模型学到"语义跨语言对齐"。

---

## 3. 主流多语言模型

### 3.1 Cohere embed-multilingual-v3

```python
import cohere

co = cohere.Client()
resp = co.embed(
    texts=["猫", "cat", "gato"],
    model="embed-multilingual-v3.0",
    input_type="search_document",  # 或 "search_query"
)
embeddings = resp.embeddings
```

- 100+ 语言，1024 维
- 商业 API，质量高
- 跨语言效果优秀

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

- 100+ 语言，1024 维
- 开源，可自部署

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

- 100+ 语言，1024 维
- 同时支持 dense / sparse / multi-vector
- 长上下文（8192 tokens）

### 3.4 jinaai/jina-embeddings-v3

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("jinaai/jina-embeddings-v3", trust_remote_code=True)

embeddings = model.encode(
    ["如何取消订阅", "How to cancel subscription"],
    task="retrieval.passage",  # 或 retrieval.query / separation / classification
)
```

- 100+ 语言，1024 维
- 内置 task LoRA：检索 / 分类 / 聚类各自最优

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
# demos/foundations/05_multilingual.py
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

输出大致：

```
       EN-同义  sim=0.85
   EN-同义(更长)  sim=0.87
      EN-不相关  sim=0.32
      ZH-不相关  sim=0.41
       ZH-同义  sim=0.91
```

英文同义文档相似度 > 中文不相关——跨语言检索成功。

---

## 6. 何时多语言会"翻车"

1. **同形异义**：

   ```
   "python" (en, 编程语言) vs "python" (en, 蟒蛇)
   多语言模型容易混
   ```

2. **小语种**：

   ```
   斯瓦希里语 / 蒙古语 / 缅甸语 训练数据少
   → 效果远不如英中
   ```

3. **特定领域术语**：

   ```
   "Form W-9" / "ICD-10" 这类英文专业术语
   翻译到中文容易丢失
   → 跨语言检索可能找错
   ```

   解决：keep 原文 + 加翻译，双语都 embed。

4. **代码 / 公式**：

   ```
   多语言模型对代码很弱
   要 embed code → 用 code-embedding 专门模型（Voyage Code / Jina Code）
   ```

---

## 7. instruction / prefix 必须用对

很多多语言模型对 prefix 敏感：

```python
# ❌ 不加 prefix
model.encode("How to cancel")

# ✅ 按文档要求加
model.encode("query: How to cancel")           # multilingual-e5
model.encode("Represent this query: How to cancel")  # instructor
```

跟 input_type：

```python
# Cohere
co.embed(texts, input_type="search_query")
co.embed(texts, input_type="search_document")
co.embed(texts, input_type="classification")
co.embed(texts, input_type="clustering")
```

**不同 input_type 给的向量不同**——不能混用。

---

## 8. 翻译 + 单语 vs 直接多语

有些团队的"曲线救国"：

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

**实战**：B 在 query 端代价高、不可扩展，**A 是主流**。

---

## 9. 跨语言混合检索

混合 BM25（关键词） + dense（语义） 时，BM25 部分需要 query 跟 doc 同语言。常见策略：

```
中文 query：
  ↓
分支 1: 中文 BM25 → 中文 docs
分支 2: 翻译成英文 → 英文 BM25 → 英文 docs
分支 3: multilingual embedding → 所有 docs

合并三路 → rerank
```

详见 [05-retrieval/02-bm25-fusion.md](../05-retrieval/02-bm25-fusion.md)。

---

## 10. 实战选型

```
单语场景（只用一种语言）：
  → 选该语言最强单语模型（中文 BGE / 英文 OpenAI）

中英为主，偶尔小语种：
  → multilingual-e5 / bge-m3 / cohere-multilingual

跨语言检索是核心需求：
  → cohere-multilingual-v3 / bge-m3（强）

预算紧：
  → bge-m3 / multilingual-e5（开源）

要长上下文：
  → bge-m3（8192 tokens）
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| 用单语模型搜跨语言 | 召回率掉到 < 30%，换 multilingual |
| 中英混搭文档没切分 | 切到同语言段落分别 embed 或用 bge-m3 |
| query / doc 不加 prefix | 按官方文档加 |
| 同时存多个模型的 vec | 维度 / 分布不同，必须分开 collection |

---

## 12. 下一步

- 📖 多模态：图片也能 embedding → [06-multimodal.md](./06-multimodal.md)
- 📖 多语言模型实操对比 → [02-models/03-open-source.md](../02-models/03-open-source.md)
- 📖 在多语言上做混合检索 → [05-retrieval/02-bm25-fusion.md](../05-retrieval/02-bm25-fusion.md)
