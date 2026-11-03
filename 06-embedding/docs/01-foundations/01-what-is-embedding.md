# 什么是 Embedding，它为什么有效

> Embedding 把文字、图片、音频、代码这类东西压成一个固定长度的浮点向量，语义相近的输入，得到的向量也相近。语义搜索、RAG、推荐、聚类都建在这一点上，所以这本手册从它讲起。

---

## 1. 30 秒定义

```python
embed("猫") → [0.12, -0.43, 0.88, ..., 0.05]  # 比如 1536 维
embed("kitten") → [0.13, -0.41, 0.87, ..., 0.06]  # 跟"猫"非常接近
embed("Linux") → [-0.55, 0.22, -0.31, ..., 0.78]  # 跟"猫"差得远
```

向量之间可以算相似度（cosine / dot product），相似度越高，语义越近。

---

## 2. 为什么需要 embedding

举个例子：用户搜"取消订阅"，知识库里那篇文章的标题是"如何停止自动续费"。关键词搜索在这里直接落空：

```python
# ❌ 关键词搜索
"取消订阅" 不在 "如何停止自动续费" 里 → 召回 0
```

换成 embedding：

```python
query_vec = embed("取消订阅")
doc_vecs = [embed(doc) for doc in all_docs]

# 算相似度，召回最相似的
similarities = [cosine(query_vec, dv) for dv in doc_vecs]
top_doc = docs[argmax(similarities)]  # → "如何停止自动续费"
```

两句话字面上没有重合，但在向量空间里离得很近，所以能被找回来。

---

## 3. 它为什么能 work

直觉上，模型在海量文本里见过各种上下文，学到了哪些词经常一起出现：

```
"猫" 经常跟 ["可爱", "宠物", "kitten", "喵", "尾巴"] 一起出现
"狗" 经常跟 ["可爱", "宠物", "puppy", "汪", "尾巴"] 一起出现
"Linux" 经常跟 ["内核", "服务器", "命令行", "Ubuntu"] 一起出现
```

训练目标是让出现在相似上下文里的词，在向量空间里彼此靠近。训完之后：

- 猫、狗、宠物之间是中等距离
- 猫和 Linux 离得很远
- king - man + woman ≈ queen，这是早期 word2vec 词向量的经典演示；现代句向量模型的空间不一定保持这种干净的线性关系，但"语义近则向量近"这一点不变

训练细节见[Embedding 如何训练：Contrastive Learning 简史](./03-how-trained.md)。

---

## 4. 跟传统检索的区别

| | 关键词检索（BM25） | Embedding 检索 |
|---|---|---|
| 匹配什么 | 词面 | 语义 |
| 同义词 | 不行 | 行 |
| 拼写错误 | 不行 | 一般 |
| 语义相反 | 不会误判 | 容易混淆（"涨"和"跌"有时挺近） |
| 长尾词 / 专有名词 | 强 | 弱（需要特定训练） |
| 速度 | 极快（倒排索引） | 快（向量索引） |
| 实现成本 | 低（Elasticsearch / Lucene） | 中（embedding + 向量库） |

两者的强项正好互补，所以实践中混合检索（BM25 + embedding）几乎总比只用一种好，做法见[BM25 + Dense 融合：RRF / Weighted](../05-retrieval/02-bm25-fusion.md)。

---

## 5. 主要用途

1. 语义搜索 / RAG：本手册的主线
2. 推荐：用户 embedding 和商品 embedding 求相似
3. 去重 / 聚类：比如相似度 > 0.9 视为重复
4. 分类：在 embedding 上面训一个轻量分类器
5. 异常检测：离正常样本很远的就是异常
6. 多模态搜索：文字搜图、图搜图（CLIP 类模型）

---

## 6. Hello world

```python
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

print(f"猫 vs kitten:  {cosine(vec_cat, vec_kitten):.4f}")  # 明显高于下一行
print(f"猫 vs Linux:   {cosine(vec_cat, vec_linux):.4f}")   # 具体数值随模型而变，只看相对大小
```

---

## 7. 向量是什么样子

```
"猫" → 1536 维浮点向量

[0.012, -0.043, 0.088, 0.155, -0.092, ..., 0.005]
   ↑      ↑      ↑
   每个维度代表"某种隐式语义特征"
   人类无法直接解释每个维度是什么
```

- 长度（dimension）由模型决定：OpenAI 3-small 是 1536，3-large 是 3072，BGE-base 是 768
- 每一维的值通常在 [-1, 1] 附近；OpenAI、千问等 API 返回的向量已经做过 L2 归一化，开源模型（如 BGE）常要自己打开 normalize 选项
- 默认是 float32，可以量化到 int8 / int4 来省存储

---

## 8. 跟传统 ML 模型的关系

现在的 embedding 模型有两条主流路线。一条是 transformer encoder（BERT 一类，BGE、E5 都是）：

```
输入文本 "猫" → tokenize → BERT-like encoder → [CLS] 或 mean pooling → 1 个向量
```

另一条是基于 LLM（decoder）的，比如 Qwen3-Embedding：拿一个生成模型做底座，取最后一个 token 的 hidden state 当向量，再用对比学习专门训练。

直接拿一个没训过的生成模型取 hidden state 当 embedding，效果通常不好。不管走哪条路线，专用 embedding 模型都经过对比学习训练，检索质量比裸用 hidden state 高得多；encoder 路线的模型一般更小、更快，LLM 路线的模型更大，在多语言和长文本上往往更强。

---

## 9. 常见误解

| 误解 | 实际情况 |
|------|------|
| "Embedding 是真理" | 不同模型给的向量不能直接比，同一模型的不同版本也不行 |
| "维度越高越好" | 不一定。3072 维的存储是 1536 维的 2 倍，检索也更慢，检索质量却往往只高一点 |
| "Embedding 能理解一切" | 长文档、复杂逻辑、多跳推理它都处理不好 |
| "余弦相似度 0.95 = 几乎一样" | 要看模型，有的模型 0.7 就已经很近了 |
| "上线后不用动了" | 模型换版本、数据扩充以后，embedding 都要重建 |

---

## 10. 我应该选什么模型

详细对比在[模型选型](../02-models)一章，这里先给个粗略建议：

- 不想多纠结：OpenAI `text-embedding-3-small`，或者国内能直连的千问 `text-embedding-v4`（本手册的示例默认用它）
- 预算紧或要自部署：开源的 `Qwen3-Embedding-0.6B/4B/8B` 或 `BAAI/bge-m3`
- 质量要求高：Cohere `embed-v5.0-pro`、Voyage `voyage-4-large`、`text-embedding-3-large`，或千问 v4 的 2048 维。先在自己的 evalset 上比一比再定
- 多模态：CLIP 系列（OpenCLIP），或者新一代多模态 embedding（千问 `qwen3-vl-embedding`、Cohere `embed-v5.0`、Jina v5 omni）

---

## 11. 下一步

- 相似度怎么算：[向量空间与相似度度量](./02-similarity-metrics.md)
- 模型是怎么训出来的：[Embedding 如何训练：Contrastive Learning 简史](./03-how-trained.md)
- 维度怎么选：[维度、精度与性能的权衡](./04-dimension-tradeoff.md)
- 想马上搭一个 RAG：[完整 RAG Pipeline 实战](../08-applications/01-full-rag.md)
