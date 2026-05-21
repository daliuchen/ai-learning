# 什么是 Embedding / 为啥它能 work

> **一句话**：Embedding 把"任何东西"（文字 / 图片 / 音频 / 代码）压成一个固定长度的浮点数向量，**语义相似的东西向量也相似**——这是语义搜索、RAG、推荐、聚类的共同底座。

---

## 1. 30 秒定义

```python
embed("猫") → [0.12, -0.43, 0.88, ..., 0.05]  # 比如 1536 维
embed("kitten") → [0.13, -0.41, 0.87, ..., 0.06]  # 跟"猫"非常接近
embed("Linux") → [-0.55, 0.22, -0.31, ..., 0.78]  # 跟"猫"差得远
```

向量之间能算"相似度"（cosine / dot product），相似度高 → 语义近。

---

## 2. 为啥要这玩意

**问题**：用户搜"取消订阅"，知识库里写的是"如何停止自动续费"——关键词搜索找不到。

```python
# ❌ 关键词搜索
"取消订阅" 不在 "如何停止自动续费" 里 → 召回 0
```

**Embedding 解法**：

```python
query_vec = embed("取消订阅")
doc_vecs = [embed(doc) for doc in all_docs]

# 算相似度，召回最相似的
similarities = [cosine(query_vec, dv) for dv in doc_vecs]
top_doc = docs[argmax(similarities)]  # → "如何停止自动续费"
```

向量空间里"取消订阅"跟"停止自动续费"很近，被找到了。

---

## 3. 它为什么能 work

直觉：模型见过海量"context"——它在大量文本里学到了"哪些词经常出现在一起"。

```
"猫" 经常跟 ["可爱", "宠物", "kitten", "喵", "尾巴"] 一起出现
"狗" 经常跟 ["可爱", "宠物", "puppy", "汪", "尾巴"] 一起出现
"Linux" 经常跟 ["内核", "服务器", "命令行", "Ubuntu"] 一起出现
```

训练目标：让"经常出现在相似 context 里的词"在向量空间里**靠近**。

结果：

- 猫 ≈ 狗 ≈ 宠物（中等距离）
- 猫 ≠ Linux（很远）
- king - man + woman ≈ queen（这是早期 word2vec 演示，今天还成立）

详见 [03-how-trained.md](./03-how-trained.md)。

---

## 4. 跟传统检索的区别

| | 关键词检索（BM25） | Embedding 检索 |
|---|---|---|
| 匹配啥 | 词面 | 语义 |
| 同义词 | 不行 | 行 |
| 拼写错误 | 不行 | 一般 |
| 语义相反 | 一目了然（不会误判） | 容易混淆（"涨"和"跌"有时挺近） |
| 长尾词 / 专有名词 | 强 | 弱（需要特定训练） |
| 速度 | 极快（倒排索引） | 快（向量索引） |
| 实现成本 | 低（Elasticsearch / Lucene） | 中（embedding + 向量库） |

**实战**：混合检索（BM25 + embedding）几乎总比单独一个好。详见 [05-retrieval/02-bm25-fusion.md](../05-retrieval/02-bm25-fusion.md)。

---

## 5. 主要用途

1. **语义搜索 / RAG**：本手册主线
2. **推荐**：用户 embedding × 商品 embedding
3. **去重 / 聚类**：相似度 > 0.9 视为重复
4. **分类**：训一个轻量分类器在 embedding 之上
5. **异常检测**：跟正常样本距离远的就是异常
6. **多模态搜索**：文字搜图、图搜图（CLIP 类模型）

---

## 6. Hello world

```python
# demos/foundations/01_basics.py
from openai import OpenAI


client = OpenAI()


def embed(text: str) -> list[float]:
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return resp.data[0].embedding


def cosine(a: list[float], b: list[float]) -> float:
    import numpy as np
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


vec_cat = embed("猫")
vec_kitten = embed("kitten")
vec_linux = embed("Linux 服务器运维")

print(f"猫 vs kitten:  {cosine(vec_cat, vec_kitten):.4f}")  # ~0.7+
print(f"猫 vs Linux:   {cosine(vec_cat, vec_linux):.4f}")   # ~0.1-0.3
```

---

## 7. 向量是啥样子

```
"猫" → 1536 维浮点向量

[0.012, -0.043, 0.088, 0.155, -0.092, ..., 0.005]
   ↑      ↑      ↑
   每个维度代表"某种隐式语义特征"
   人类无法直接解释每个维度是啥
```

- 长度（dimension）：模型决定（OpenAI 3-small=1536 / 3-large=3072 / BGE-base=768）
- 值域：通常 ~[-1, 1]，多数模型 L2-normalize 过
- 浮点：float32 默认，可以量化到 int8 / int4 省存储

---

## 8. 跟传统 ML 模型的关系

Embedding 模型几乎都是 transformer encoder（BERT 类）训出来的。流程：

```
输入文本 "猫" → tokenize → BERT-like encoder → [CLS] 或 mean pooling → 1 个向量
```

不是生成模型（GPT 类）—— 生成模型也能取最后一层 hidden state 当 embedding，但**专用 embedding 模型**用对比学习专门训练，质量更高、维度更小、计算更快。

---

## 9. 常见误解

| 误解 | 真相 |
|------|------|
| "Embedding 是真理" | 不同模型给的向量不能直接比；同一模型不同版本也不行 |
| "高 dimension 一定更好" | 不一定。3072 维比 1536 维存储 2x、检索慢，但 MTEB 分数可能只高 1-2 分 |
| "Embedding 能理解一切" | 长文档 / 复杂逻辑 / 多跳推理它处理不好 |
| "余弦相似度 0.95 = 几乎一样" | 看模型。有的模型 0.7 已经很近了 |
| "上线后不用动了" | 模型换版本 / 数据扩 → embedding 要重建 |

---

## 10. 我应该选啥模型

详见 [02-models](../02-models)。一句话先给：

- **不想想太多** → OpenAI `text-embedding-3-small`
- **预算紧** → 开源 `BAAI/bge-small-zh-v1.5` 或 `BAAI/bge-m3`
- **质量要求高** → Cohere `embed-multilingual-v3.0` 或 `text-embedding-3-large`
- **多模态** → CLIP 系列（OpenCLIP / Jina-CLIP-v1）

---

## 11. 下一步

- 📖 相似度怎么算 → [02-similarity-metrics.md](./02-similarity-metrics.md)
- 📖 这玩意怎么训出来的 → [03-how-trained.md](./03-how-trained.md)
- 📖 选维度 → [04-dimension-tradeoff.md](./04-dimension-tradeoff.md)
- 📖 想立刻搭 RAG → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
