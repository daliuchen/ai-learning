# 向量空间 + Similarity Metrics

> **一句话**：算"两个向量有多像"主要用 **cosine / dot product / euclidean**——大多数现代 embedding 模型给的向量已经 L2-normalize 过，这时 cosine 跟 dot 完全等价。

---

## 1. 三种距离 / 相似度

### 1.1 Cosine Similarity（最常用）

```
cos(a, b) = (a · b) / (||a|| × ||b||)
```

值域 [-1, 1]，1 = 完全同向，0 = 正交（无关），-1 = 完全反向。

```python
import numpy as np

def cosine(a, b):
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
```

**关心方向不关心长度**——非常适合 embedding（长度受文本长度等噪声影响）。

### 1.2 Dot Product

```
dot(a, b) = a · b = Σ aᵢ × bᵢ
```

值域无界。**算最快**（不用归一化）。

```python
def dot(a, b):
    return float(np.array(a) @ np.array(b))
```

如果两个向量都 L2-normalize 过，`dot == cosine`。

### 1.3 Euclidean Distance（L2）

```
d(a, b) = √Σ(aᵢ - bᵢ)²
```

值域 [0, ∞)，越小越像。

```python
def euclidean(a, b):
    return float(np.linalg.norm(np.array(a) - np.array(b)))
```

**关心绝对位置**——embedding 一般不用，因为同义词的"长度"也可能差很多。

---

## 2. L2-normalize 是啥

把向量除以自己的 L2 范数，让它长度变成 1：

```python
def l2_normalize(v):
    return v / np.linalg.norm(v)
```

```python
v = [3.0, 4.0]
# 长度 = √(9+16) = 5
v_normalized = [0.6, 0.8]  # 长度 = 1
```

**为啥重要**：

- 归一化后，cosine = dot product → **算 dot 就行，比 cosine 快**
- 不同长度的文本（一个词 vs 一段话）embedding 长度可能差很多，归一化能消除这个影响

OpenAI / Cohere / sentence-transformers 默认输出**已经归一化的向量**。

```python
# 验证 OpenAI 输出是不是归一化的
import numpy as np
from openai import OpenAI
client = OpenAI()

vec = client.embeddings.create(model="text-embedding-3-small", input="hello").data[0].embedding
print(np.linalg.norm(vec))  # ≈ 1.0
```

---

## 3. 选哪个

| 场景 | 推荐 |
|------|------|
| 文本 embedding（OpenAI / BGE / Cohere） | Cosine 或 Dot（两者等价，Dot 更快） |
| 图片 embedding（CLIP） | Cosine |
| 老式 word2vec | Cosine |
| 数值特征（不是 embedding） | Euclidean |

向量库里一般选 **cosine** 或 **dot**——都行，跟 embedding 模型一致就好。

---

## 4. 数值范围参考

不同模型给的"高相似"阈值不一样：

| 模型 | "几乎一样"阈值 | "相关"阈值 | "无关"阈值 |
|------|---------------|-----------|-----------|
| OpenAI text-embedding-3-small | > 0.85 | > 0.5 | < 0.2 |
| BGE-base | > 0.9 | > 0.6 | < 0.3 |
| sentence-transformers/all-MiniLM | > 0.85 | > 0.5 | < 0.2 |
| Cohere embed-v3 | > 0.7 | > 0.4 | < 0.1 |

⚠️ **不要靠绝对阈值过滤**——同样的相似度 0.6，在 BGE 是"还行"，在 Cohere 已经"挺相关"了。

**做法**：用 top-k 取最相似的 N 个，而不是 "similarity > 0.7"。

---

## 5. 向量空间的"形状"

直觉理解：

```
2D 平面想象图（实际是 768/1536 维）：

      宠物相关
       ↑
       ●猫
       ●狗
       ●仓鼠
       ●kitten ────── 翻译/语言相关
       ●puppy ─────── ●Spanish dog
                      ●perro

技术相关 ────●Linux●Python●Kubernetes●Docker
       
       ↓
      日常物品
       ●桌子
       ●椅子
```

**相似的概念聚在一起，不相关的离很远**。

但这只是直觉——真实 1536 维空间是"巨大球面"，2D 投影后会失真。

---

## 6. cosine 跟 angle 的关系

```
cos(θ) = 1  → θ = 0°    完全同向
cos(θ) = 0.5 → θ = 60°
cos(θ) = 0  → θ = 90°   正交
cos(θ) = -1 → θ = 180°  反向
```

**实战观察**：现代 embedding 几乎不会出 cos < 0 的对。原因：训练目标只关心"正样本拉近"，反向比正交更难得。

---

## 7. 完整对比 demo

```python
# demos/foundations/02_similarity.py
import numpy as np
from openai import OpenAI

client = OpenAI()


def embed(texts: list[str]) -> np.ndarray:
    resp = client.embeddings.create(model="text-embedding-3-small", input=texts)
    return np.array([d.embedding for d in resp.data])


def cosine(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def dot(a, b):
    return float(a @ b)


def euclidean(a, b):
    return float(np.linalg.norm(a - b))


pairs = [
    ("猫", "kitten"),
    ("猫", "狗"),
    ("猫", "Linux"),
    ("取消订阅", "如何停止自动续费"),
    ("北京", "中国首都"),
    ("python", "snake"),
    ("python", "编程语言"),
]


for a, b in pairs:
    va, vb = embed([a, b])
    print(f"{a:>10} ↔ {b:<20}  cos={cosine(va, vb):.3f}  dot={dot(va, vb):.3f}  L2={euclidean(va, vb):.3f}")
```

输出大致：

```
        猫 ↔ kitten              cos=0.638  dot=0.638  L2=0.851
        猫 ↔ 狗                  cos=0.633  dot=0.633  L2=0.857
        猫 ↔ Linux               cos=0.082  dot=0.082  L2=1.356
   取消订阅 ↔ 如何停止自动续费    cos=0.527  dot=0.527  L2=0.972
       北京 ↔ 中国首都            cos=0.622  dot=0.622  L2=0.870
     python ↔ snake               cos=0.265  dot=0.265  L2=1.212
     python ↔ 编程语言            cos=0.443  dot=0.443  L2=1.055
```

注意：`cos == dot` 因为 OpenAI 输出是归一化的。

---

## 8. 在向量库怎么配

```python
# Qdrant
from qdrant_client.models import Distance, VectorParams
collection_config = VectorParams(size=1536, distance=Distance.COSINE)

# Pinecone
pc.create_index(name="my", dimension=1536, metric="cosine")

# pgvector
# CREATE INDEX ON items USING hnsw (embedding vector_cosine_ops);

# Chroma
collection = client.create_collection(
    name="my",
    metadata={"hnsw:space": "cosine"},  # or "l2" / "ip"
)
```

**默认选 cosine**，除非有特殊理由。

---

## 9. 常见坑

| 坑 | 解 |
|----|----|
| 用 L2 距离比较未归一化向量 | 归一化后再比，或换 cosine |
| 跨模型直接比相似度 | 不行，必须用同一模型 |
| 假设"相似度 > 0.8 = 强相关" | 看模型，用 top-k 而不是阈值 |
| 拼接多个 embedding 比较 | 通常更糟，不如重新 embed 整段 |

---

## 10. 下一步

- 📖 这些向量是怎么训出来的 → [03-how-trained.md](./03-how-trained.md)
- 📖 选 dimension → [04-dimension-tradeoff.md](./04-dimension-tradeoff.md)
- 📖 向量库怎么算 → [03-vector-db/06-index-algorithms.md](../03-vector-db/06-index-algorithms.md)
