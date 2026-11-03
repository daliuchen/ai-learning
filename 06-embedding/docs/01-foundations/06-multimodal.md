# 多模态 Embedding：CLIP 原理

> CLIP、OpenCLIP、Jina-CLIP 这类模型把图片和文字编码进同一个向量空间，于是文字搜图、图搜图、图搜文都能用同一套 cosine 相似度来做。这篇讲 CLIP 的训练思路、实际怎么用，以及它在哪些地方靠不住。

---

## 1. 什么是多模态 embedding

```python
embed_text("a cat in a spacesuit")  → [0.12, -0.43, ..., 0.05]
embed_image(open("astronaut_cat.jpg"))  → [0.13, -0.41, ..., 0.06]
                                          ↑
                                  跟上面那个文本向量很近
```

文字向量和图片向量落在同一个空间里，这就叫跨模态对齐。

---

## 2. CLIP 原理（30 秒）

> CLIP: Learning Transferable Visual Models From Natural Language Supervision (2021)

1. 数据：从网上爬了 4 亿对 (image, alt-text)
2. 架构：
   - 文本 encoder（transformer）→ text embedding
   - 图片 encoder（ViT 或 ResNet）→ image embedding
3. 训练目标：contrastive

```
batch 里 N 对 (image, text)：
  让对应的 (image_i, text_i) 相似度最高
  让 (image_i, text_j) i ≠ j 相似度最低
```

训练的结果是，每张图片和它配对的文本被拉近，和其他文本被推远，两种模态就落进了同一个向量空间。

---

## 3. CLIP 系列模型

| 模型 | dim | 特点 |
|------|-----|------|
| OpenAI CLIP ViT-B/32 | 512 | 原版，2021 |
| OpenAI CLIP ViT-L/14 | 768 | 更强，慢 |
| OpenCLIP（laion） | 不同 | 开源复现，社区训练 |
| jina-clip-v2 | 1024（可截维） | 多语言，文本侧支持 8k token，文字能力比 CLIP 强 |
| SigLIP / SigLIP 2（Google） | 随模型尺寸不同 | 用 sigmoid loss 训练，同等规模下通常比 CLIP 好 |
| EVA-CLIP | 不同 | 社区大规模训练的 CLIP 变体 |

> 除了经典的 CLIP 双塔，新一代商用多模态 embedding 已经能把文本、图片，甚至视频、音频、图文混排的 PDF 放进同一个向量空间。比如千问 `qwen3-vl-embedding`（文本/图片/视频，256–2560 维，见百炼官方文档）、Cohere `embed-v5.0`（文本/图片/混合）、Jina `jina-embeddings-v5-omni`（图/音/视频，非商用协议）。它们对截图、PDF 页这类图文混排的文档更友好。本篇后面讲的 CLIP 原理和代码同样适用，把 embed 函数换成对应的 API 即可（需要对应平台的账号）。

OpenAI 原版 CLIP 的模型在 https://github.com/openai/CLIP ，它不属于 OpenAI API。

---

## 4. 跑起来：OpenCLIP

```python
import torch
import open_clip
from PIL import Image


model, _, preprocess = open_clip.create_model_and_transforms(
    "ViT-B-32",
    pretrained="laion2b_s34b_b79k",
)
tokenizer = open_clip.get_tokenizer("ViT-B-32")
model.eval()


def embed_image(path: str):
    img = preprocess(Image.open(path).convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        feat = model.encode_image(img)
        feat /= feat.norm(dim=-1, keepdim=True)
    return feat.numpy()[0]


def embed_text(text: str):
    tokens = tokenizer([text])
    with torch.no_grad():
        feat = model.encode_text(tokens)
        feat /= feat.norm(dim=-1, keepdim=True)
    return feat.numpy()[0]


# 准备几张图
images = {
    "cat.jpg": "a cute cat",
    "dog.jpg": "a happy dog",
    "linux.jpg": "linux terminal",
}

img_vecs = {path: embed_image(path) for path in images}

# 用文字搜图
queries = ["a kitten", "a server room", "a puppy"]

import numpy as np

for q in queries:
    qv = embed_text(q)
    sims = {p: float(qv @ v) for p, v in img_vecs.items()}
    best = max(sims, key=sims.get)
    print(f"'{q}' → {best} (sim={sims[best]:.3f})")
```

输出大致如下：

```
'a kitten' → cat.jpg (sim=0.302)
'a server room' → linux.jpg (sim=0.268)
'a puppy' → dog.jpg (sim=0.295)
```

CLIP 给出的相似度绝对值普遍不高，0.2 到 0.3 已经算很好，应该看相对排序。

---

## 5. CLIP 的弱点

CLIP 做 zero-shot 任务很强，但下面几类情况它处理不好：

| 弱点 | 表现 |
|------|------|
| 文字理解弱 | "猫在桌子下"和"桌子在猫下"，CLIP 区分不开 |
| 数字 / 计数 | "三只猫"和"五只猫"分不太清 |
| 否定 | "没有猫"和"有猫"容易混 |
| 长文本 | text encoder 只有 77 tokens 的上下文，长描述损失大 |
| 中文 | OpenAI CLIP 的训练数据以英文为主，中文弱 |

应对办法：文字描述写得精炼，突出关键词；中文场景用 Chinese-CLIP 或支持多语言的 Jina-CLIP；语义复杂的需求，考虑基于 LLM 的多模态模型。

---

## 6. 主流用法

### 6.1 图搜图

```python
def find_similar_images(query_image_path, image_db):
    qv = embed_image(query_image_path)
    sims = [(p, qv @ embed_image(p)) for p in image_db]
    return sorted(sims, key=lambda x: -x[1])[:5]
```

用在电商商品图搜索、相册去重、内容审核等场景。

### 6.2 文搜图

```python
def text_to_image(query, image_db):
    qv = embed_text(query)
    sims = [(p, qv @ embed_image(p)) for p in image_db]
    return sorted(sims, key=lambda x: -x[1])[:5]
```

用在图库搜索、设计素材库，以及视频帧搜索（先把关键帧 embed，再用文字去搜）。

### 6.3 图分类 zero-shot

```python
def classify_image(image_path, labels):
    iv = embed_image(image_path)
    sims = {label: iv @ embed_text(f"a photo of a {label}") for label in labels}
    return max(sims, key=sims.get)


classify_image("photo.jpg", ["cat", "dog", "car", "tree"])
```

不用训练，直接做零样本分类。

---

## 7. 把多模态接到向量库

向量库并不关心向量来自哪种模态：

```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct


client = QdrantClient(url="http://localhost:6333")

client.create_collection(
    collection_name="products",
    vectors_config=VectorParams(size=512, distance=Distance.COSINE),
)


# 上传：同一个 collection 里既能 search 文字也能 search 图
points = []
for i, (path, alt_text) in enumerate(product_data):
    img_vec = embed_image(path)
    points.append(PointStruct(
        id=i,
        vector=img_vec.tolist(),
        payload={"path": path, "alt": alt_text},
    ))

client.upsert(collection_name="products", points=points)


# 用文字搜
query_vec = embed_text("red sneakers")
hits = client.query_points(
    collection_name="products",
    query=query_vec.tolist(),
    limit=10,
).points
```

文字向量和图片向量能放在一起比，前提是它们出自同一个 CLIP 模型（同一个向量空间）。维度一样但来自不同模型的向量不能混用。

---

## 8. 选型决策

```
仅图搜图（电商相似商品）：
  → OpenCLIP ViT-L/14 / SigLIP 2（开源，用自己的商品图比一下再定）

中文场景：
  → Chinese-CLIP / jina-clip-v2

多语言文搜图：
  → jina-clip-v2 / multilingual-clip

需要把图描述精细：
  → 用多模态大模型（Qwen-VL 系列、gpt-6.1-sol 等）生成描述，再用文本 embedding
  → 而不是直接 CLIP

视频：
  → 提关键帧 → CLIP embed 每帧
  → 或 X-CLIP（专门视频模型）
```

---

## 9. CLIP + 文本 embedding 混搭

商品检索里常见的做法是两路并行：

```
商品检索：
  query → 同时算：
    1. CLIP query embedding → 跟 CLIP 商品图 embedding 比对
    2. 文本 embedding → 跟商品标题 / 描述 embedding 比对
  
  两路结果 RRF 融合
```

融合方法见[BM25 + Dense 融合：RRF / Weighted](../05-retrieval/02-bm25-fusion.md)。

---

## 10. 实战项目示例

```python
# 见 08-applications/03-multimodal.md
```

完整示例在[多模态：图搜图 / 文搜图 / 图搜文](../08-applications/03-multimodal.md)。

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| 用 OpenAI text-embedding 做图片检索 | 不行，模态对不上 |
| 用 CLIP 处理文字很多的图（文档截图） | CLIP 在这里很弱，改用 OCR + 文本 embedding |
| 整段视频直接 embed | 先切关键帧，再逐帧 embed |
| 向量没有归一化 | open_clip 的输出默认没归一化，拿 dot 当 cosine 用之前要先 normalize |

---

## 12. 下一步

基础原理部分到这里结束，接下来开始挑模型、搭向量库：

- OpenAI text-embedding-3 选型：[OpenAI text-embedding-3 系列](../02-models/01-openai.md)
- 商业 API 对比：[Cohere 与 VoyageAI：OpenAI 之外的商业 API](../02-models/02-cohere-voyage.md)
- 开源模型：[开源模型：Qwen3-Embedding / BGE / Jina / Nomic](../02-models/03-open-source.md)
- 多模态应用实战：[多模态：图搜图 / 文搜图 / 图搜文](../08-applications/03-multimodal.md)
