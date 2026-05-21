# 多模态 Embedding：CLIP 原理

> **一句话**：CLIP / OpenCLIP / Jina-CLIP 这类模型把**图片**和**文字**编码到同一个向量空间，于是"文字搜图"、"图搜图"、"图搜文"都用同一个 cosine 算就行。

---

## 1. 什么是多模态 embedding

```python
embed_text("一只穿宇航服的猫")  → [0.12, -0.43, ..., 0.05]
embed_image(open("astronaut_cat.jpg"))  → [0.13, -0.41, ..., 0.06]
                                          ↑
                                  跟上面那个文本向量很近
```

文字向量和图片向量在同一个空间——这就是跨模态对齐。

---

## 2. CLIP 原理（30 秒）

> CLIP: Learning Transferable Visual Models From Natural Language Supervision (2021)

1. **数据**：从网上爬 4 亿对 (image, alt-text)
2. **架构**：
   - 文本 encoder（transformer）→ text embedding
   - 图片 encoder（ViT 或 ResNet）→ image embedding
3. **训练目标**：contrastive

```
batch 里 N 对 (image, text)：
  让对应的 (image_i, text_i) 相似度最高
  让 (image_i, text_j) i ≠ j 相似度最低
```

结果：图片和它配对的文本被拉近，跟其它都被推远 → 同一向量空间。

---

## 3. CLIP 系列模型

| 模型 | dim | 特点 |
|------|-----|------|
| OpenAI CLIP ViT-B/32 | 512 | 原版，2021 |
| OpenAI CLIP ViT-L/14 | 768 | 更强，慢 |
| OpenCLIP（laion） | 不同 | 开源复现，社区训 |
| Jina-CLIP-v1 | 768 | 文字能力更强 |
| SigLIP（Google） | 768 | 比 CLIP 更高质量 |
| EVA-CLIP | 不同 | 性能 SOTA |

OpenAI 的原版 CLIP 模型可在 https://github.com/openai/CLIP 找到（不是 OpenAI API 的一部分）。

---

## 4. 跑起来：OpenCLIP

```python
# demos/foundations/06_multimodal.py
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
    img = preprocess(Image.open(path)).unsqueeze(0)
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

输出大致：

```
'a kitten' → cat.jpg (sim=0.302)
'a server room' → linux.jpg (sim=0.268)
'a puppy' → dog.jpg (sim=0.295)
```

CLIP 数值绝对值不高（0.2-0.3 已经很好），看 **相对排序**。

---

## 5. CLIP 的"陷阱"

CLIP 在 zero-shot 任务很强，但：

| 弱点 | 表现 |
|------|------|
| 文字理解弱 | "猫在桌子下" vs "桌子在猫下"，CLIP 区分不开 |
| 数字 / 计数 | "三只猫" 和 "五只猫"，CLIP 弱 |
| 否定 | "没有猫" 跟 "有猫" 容易混 |
| 长文本 | text encoder 上下文短（77 tokens），长描述损失大 |
| 中文 | OpenAI CLIP 训练数据英文为主，中文弱 |

**实战**：

- 文字描述精炼、突出关键词
- 中文用 Chinese-CLIP 或 Jina-CLIP（支持多语言）
- 复杂语义考虑用 LLM-based 多模态模型（CLIP 不擅长）

---

## 6. 主流用法

### 6.1 图搜图

```python
def find_similar_images(query_image_path, image_db):
    qv = embed_image(query_image_path)
    sims = [(p, qv @ embed_image(p)) for p in image_db]
    return sorted(sims, key=lambda x: -x[1])[:5]
```

电商商品图搜索、相册去重、内容审核。

### 6.2 文搜图

```python
def text_to_image(query, image_db):
    qv = embed_text(query)
    sims = [(p, qv @ embed_image(p)) for p in image_db]
    return sorted(sims, key=lambda x: -x[1])[:5]
```

图库搜索、设计素材库、视频帧搜索（关键帧 embed 后用文字搜）。

### 6.3 图分类 zero-shot

```python
def classify_image(image_path, labels):
    iv = embed_image(image_path)
    sims = {label: iv @ embed_text(f"a photo of a {label}") for label in labels}
    return max(sims, key=sims.get)


classify_image("photo.jpg", ["cat", "dog", "car", "tree"])
```

不用训练，直接零样本分类。

---

## 7. 把多模态接到向量库

向量库不关心向量来自啥模态：

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
hits = client.search(
    collection_name="products",
    query_vector=query_vec.tolist(),
    limit=10,
)
```

只要 dim 一致，文 / 图向量可以混存。

---

## 8. 选型决策

```
仅图搜图（电商相似商品）：
  → OpenCLIP ViT-L/14（开源）
  → 或 SigLIP（更强）

中文场景：
  → Chinese-CLIP / Jina-CLIP-v1

多语言文搜图：
  → Jina-CLIP-v1 / multilingual-clip

需要把图描述精细：
  → 用 LLaVA / GPT-4V 生成描述，再用文本 embedding
  → 而不是直接 CLIP

视频：
  → 提关键帧 → CLIP embed 每帧
  → 或 X-CLIP（专门视频模型）
```

---

## 9. CLIP + 文本 embedding 混搭

实战常见做法：

```
商品检索：
  query → 同时算：
    1. CLIP query embedding → 跟 CLIP 商品图 embedding 比对
    2. 文本 embedding → 跟商品标题 / 描述 embedding 比对
  
  两路结果 RRF 融合
```

详见 [05-retrieval/02-bm25-fusion.md](../05-retrieval/02-bm25-fusion.md) 的融合方法。

---

## 10. 实战项目示例

```python
# demos/applications/multimodal_search.py
# 见 08-applications/03-multimodal.md
```

详见 [08-applications/03-multimodal.md](../08-applications/03-multimodal.md)。

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| 用 OpenAI text-embedding 给图搜 | 不行，模态不一致 |
| CLIP 处理 OCR-heavy 图（文档截图） | CLIP 弱，考虑 OCR + 文本 embedding |
| 视频直接 embed | 切关键帧再 embed 每帧 |
| 不归一化向量 | CLIP 输出要 normalize 才能 cosine |

---

## 12. 下一步

01-foundations 完结。开始挑模型 / 搭库：

- 📖 OpenAI text-embedding-3 选型 → [02-models/01-openai.md](../02-models/01-openai.md)
- 📖 商业 API 对比 → [02-models/02-cohere-voyage.md](../02-models/02-cohere-voyage.md)
- 📖 开源模型 → [02-models/03-open-source.md](../02-models/03-open-source.md)
- 📖 多模态应用实战 → [08-applications/03-multimodal.md](../08-applications/03-multimodal.md)
