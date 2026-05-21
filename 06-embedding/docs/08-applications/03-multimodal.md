# 多模态：图搜图 / 文搜图 / 图搜文

> **一句话**：用 CLIP 类多模态模型，把图片和文本编码到同一向量空间，文字、图片当 query / doc 任意组合——电商商品搜索、图库管理、视频帧检索都用这套。

---

## 1. CLIP 基础回顾

详见 [01-foundations/06-multimodal.md](../01-foundations/06-multimodal.md)。

```python
embed_text("a red sneaker")  → vec
embed_image(img)             → vec
# 同空间，能直接比 cosine
```

---

## 2. 实战 setup

```python
# pip install open_clip_torch pillow qdrant-client
import torch
import open_clip
from PIL import Image
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct


# 1. 加载 CLIP
model, _, preprocess = open_clip.create_model_and_transforms(
    "ViT-B-32",
    pretrained="laion2b_s34b_b79k",
)
tokenizer = open_clip.get_tokenizer("ViT-B-32")
model.eval()
DIM = 512


def embed_image(img_path: str):
    img = preprocess(Image.open(img_path).convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        feat = model.encode_image(img)
        feat /= feat.norm(dim=-1, keepdim=True)
    return feat[0].numpy()


def embed_text(text: str):
    tokens = tokenizer([text])
    with torch.no_grad():
        feat = model.encode_text(tokens)
        feat /= feat.norm(dim=-1, keepdim=True)
    return feat[0].numpy()


# 2. 向量库
client = QdrantClient(":memory:")
client.create_collection(
    "products",
    vectors_config=VectorParams(size=DIM, distance=Distance.COSINE),
)
```

---

## 3. 索引商品（图 + 文）

```python
products = [
    {"id": 1, "title": "Red Nike Sneakers", "image": "imgs/red_nike.jpg", "price": 89},
    {"id": 2, "title": "Black Leather Boots", "image": "imgs/black_boots.jpg", "price": 150},
    {"id": 3, "title": "White Adidas Running Shoes", "image": "imgs/white_adidas.jpg", "price": 99},
    # ...
]


points = []
for p in products:
    img_vec = embed_image(p["image"])
    points.append(PointStruct(
        id=p["id"],
        vector=img_vec.tolist(),       # 用图片 embedding
        payload=p,
    ))


client.upsert("products", points=points)
```

---

## 4. 文搜图

```python
def search_by_text(query, top_k=5):
    q_vec = embed_text(query)
    hits = client.search("products", query_vector=q_vec.tolist(), limit=top_k)
    return [(h.payload, h.score) for h in hits]


# 用法
results = search_by_text("red running shoes")
for p, score in results:
    print(f"  {score:.4f}  {p['title']}")
```

---

## 5. 图搜图

```python
def search_by_image(image_path, top_k=5):
    q_vec = embed_image(image_path)
    hits = client.search("products", query_vector=q_vec.tolist(), limit=top_k)
    return [(h.payload, h.score) for h in hits]


results = search_by_image("user_uploaded.jpg")
```

电商常用："用图找同款"。

---

## 6. 文 + 图混合 query

```python
def search_with_text_and_image(text, image_path, top_k=5, alpha=0.5):
    """alpha: text 占比"""
    t_vec = embed_text(text)
    i_vec = embed_image(image_path)
    
    # 加权融合
    import numpy as np
    combined = alpha * t_vec + (1 - alpha) * i_vec
    combined /= np.linalg.norm(combined)
    
    hits = client.search("products", query_vector=combined.tolist(), limit=top_k)
    return [(h.payload, h.score) for h in hits]


# 用：上传图 + 加"红色" 描述
results = search_with_text_and_image("red color", "user_pic.jpg")
```

---

## 7. 双向量：图 + 标题分开

CLIP 文本能力弱，把"商品标题"用文本 embedding 单独做更准：

```python
client.create_collection(
    "products_v2",
    vectors_config={
        "image": VectorParams(size=512, distance=Distance.COSINE),      # CLIP
        "text": VectorParams(size=1536, distance=Distance.COSINE),      # text-embedding-3
    },
)


# 上传
for p in products:
    img_vec = embed_image(p["image"])
    text_vec = embed_openai(p["title"])    # 用更好的文本模型
    client.upsert("products_v2", points=[PointStruct(
        id=p["id"],
        vector={"image": img_vec.tolist(), "text": text_vec.tolist()},
        payload=p,
    )])


# 查（按 query 类型选向量）
text_hits = client.search("products_v2", query_vector=("text", embed_openai(query).tolist()))
image_hits = client.search("products_v2", query_vector=("image", embed_text(query).tolist()))

# RRF 融合
```

---

## 8. zero-shot 分类

不用训练，CLIP 直接给图分类：

```python
def classify_image(image_path, labels):
    img_vec = embed_image(image_path)
    label_vecs = [embed_text(f"a photo of a {l}") for l in labels]
    
    sims = [float(img_vec @ lv) for lv in label_vecs]
    return labels[sims.index(max(sims))]


cat_or_dog = classify_image("photo.jpg", ["cat", "dog", "neither"])
```

适合：内容审核、商品 tag 自动打标、视频内容分类。

---

## 9. 视频帧检索

```python
import cv2


def extract_keyframes(video_path, every_sec=5):
    """每 N 秒抽一帧"""
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_interval = int(fps * every_sec)
    
    frames = []
    i = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if i % frame_interval == 0:
            frames.append({"frame": frame, "time_sec": i / fps})
        i += 1
    
    cap.release()
    return frames


def index_video(video_path, video_id):
    frames = extract_keyframes(video_path)
    for f in frames:
        img = Image.fromarray(cv2.cvtColor(f["frame"], cv2.COLOR_BGR2RGB))
        vec = embed_pil_image(img)
        client.upsert("videos", points=[PointStruct(
            id=hash((video_id, f["time_sec"])),
            vector=vec.tolist(),
            payload={"video_id": video_id, "time_sec": f["time_sec"]},
        )])


# 文字搜视频片段
hits = client.search("videos", query_vector=embed_text("有人跳舞").tolist(), limit=5)
for h in hits:
    print(f"video={h.payload['video_id']}  time={h.payload['time_sec']}s")
```

---

## 10. 完整 demo

```python
# demos/applications/03_multimodal.py
import torch
import open_clip
from PIL import Image
import io
import requests


model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
tokenizer = open_clip.get_tokenizer("ViT-B-32")
model.eval()


def embed_text(text):
    with torch.no_grad():
        feat = model.encode_text(tokenizer([text]))
        feat /= feat.norm(dim=-1, keepdim=True)
    return feat[0].numpy()


def embed_image_from_url(url):
    img = Image.open(io.BytesIO(requests.get(url).content)).convert("RGB")
    img_tensor = preprocess(img).unsqueeze(0)
    with torch.no_grad():
        feat = model.encode_image(img_tensor)
        feat /= feat.norm(dim=-1, keepdim=True)
    return feat[0].numpy()


# 用公开图测试（建议换成本地 / 公司图）
items = [
    {"label": "cat", "url": "https://images.example.com/cat.jpg"},
    {"label": "dog", "url": "https://images.example.com/dog.jpg"},
    {"label": "car", "url": "https://images.example.com/car.jpg"},
]


# Index
vecs = {}
for it in items:
    try:
        vecs[it["label"]] = embed_image_from_url(it["url"])
    except Exception as e:
        print(f"skip {it['label']}: {e}")


# 文搜图
for query in ["a kitten", "a vehicle", "a puppy"]:
    qv = embed_text(query)
    sims = {label: float(qv @ v) for label, v in vecs.items()}
    print(f"\n'{query}':")
    for label, sim in sorted(sims.items(), key=lambda x: -x[1]):
        print(f"  {label:<6}  {sim:.4f}")
```

---

## 11. 模型选型

```
通用图文：CLIP ViT-L/14 / SigLIP（最强）
中文：Chinese-CLIP / Jina-CLIP
长描述：jina-clip-v1
代码截图：CLIP 一般，专模型可考虑
医疗 / 卫星 / 遥感：domain-specific CLIP（如 RemoteCLIP）
```

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| CLIP 数字 / 计数弱 | 别期望区分"3 只猫"和"5 只猫" |
| 中文用英文 CLIP | 用 Chinese-CLIP / 多语言 CLIP |
| 用 OpenAI text embedding 比 CLIP 图 | 模态不一致，无效 |
| OCR-heavy 图（文档截图）| CLIP 弱，先 OCR 再用文本 embed |

---

## 13. 下一步

- 📖 推荐系统 → [04-recommendation.md](./04-recommendation.md)
- 📖 去重 / 聚类 → [05-deduplication.md](./05-deduplication.md)
- 📖 基础原理 → [01-foundations/06-multimodal.md](../01-foundations/06-multimodal.md)
