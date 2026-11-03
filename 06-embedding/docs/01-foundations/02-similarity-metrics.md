# 向量空间与相似度度量

> 衡量两个向量有多像，常用的是 cosine、dot product 和 euclidean 三种。现在多数 embedding 模型输出的向量已经做过 L2 归一化，这种情况下 cosine 和 dot 完全等价，选哪个只影响计算速度。

---

## 1. 三种距离 / 相似度

### 1.1 Cosine Similarity（最常用）

```
cos(a, b) = (a · b) / (||a|| × ||b||)
```

值域 [-1, 1]：1 表示完全同向，0 表示正交（无关），-1 表示完全反向。

```python
import numpy as np

def cosine(a, b):
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
```

cosine 只看方向、不看长度，这正适合 embedding，因为向量长度会受文本长度之类的噪声影响。

### 1.2 Dot Product

```
dot(a, b) = a · b = Σ aᵢ × bᵢ
```

值域无界。它不需要归一化，所以算起来最快。

```python
def dot(a, b):
    return float(np.array(a) @ np.array(b))
```

如果两个向量都做过 L2 归一化，`dot == cosine`。

### 1.3 Euclidean Distance（L2）

```
d(a, b) = √Σ(aᵢ - bᵢ)²
```

值域 [0, ∞)，越小越像。

```python
def euclidean(a, b):
    return float(np.linalg.norm(np.array(a) - np.array(b)))
```

欧氏距离看的是绝对位置。embedding 一般不用它，因为两个同义表达的向量长度也可能差很多。

---

## 2. L2-normalize 是什么

把向量除以自己的 L2 范数，长度就变成 1：

```python
def l2_normalize(v):
    return v / np.linalg.norm(v)
```

```python
v = [3.0, 4.0]
# 长度 = √(9+16) = 5
v_normalized = [0.6, 0.8]  # 长度 = 1
```

这一步有两个好处。一是归一化之后 cosine 等于 dot product，直接算 dot 就行，比算 cosine 快。二是一个词和一段话的 embedding 长度可能差很多，归一化能消掉这个差异。

OpenAI、Cohere、千问的 API 返回的是已经归一化的向量；sentence-transformers 要看具体模型（有的模型自带 Normalize 层，BGE 这类则需要在 `encode` 时传 `normalize_embeddings=True`）。可以自己验证一下：

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

在向量库里选 cosine 或 dot 都可以，和 embedding 模型训练时用的一致就好。

---

## 4. 数值范围参考

不同模型的"高相似"阈值不一样。下表是示意的量级，不是官方数据，实际分布请在自己的数据上抽样看：

| 模型 | "几乎一样"阈值 | "相关"阈值 | "无关"阈值 |
|------|---------------|-----------|-----------|
| OpenAI text-embedding-3-small | > 0.85 | > 0.5 | < 0.2 |
| BGE-base | > 0.9 | > 0.6 | < 0.3 |
| sentence-transformers/all-MiniLM | > 0.85 | > 0.5 | < 0.2 |
| Cohere embed-v3 | > 0.7 | > 0.4 | < 0.1 |

**不要靠绝对阈值过滤。** 同样是 0.6，在 BGE 上算"还行"，在 Cohere 上已经算"挺相关"了。更稳的做法是用 top-k 取最相似的 N 个，而不是写死 "similarity > 0.7"。

---

## 5. 向量空间的"形状"

先看一张示意图：

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

相似的概念聚在一起，不相关的离得很远。不过这张图只用来建立直觉：真实的 1536 维空间更像一个巨大的球面，投影到 2D 后会严重失真。

---

## 6. cosine 跟 angle 的关系

```
cos(θ) = 1  → θ = 0°    完全同向
cos(θ) = 0.5 → θ = 60°
cos(θ) = 0  → θ = 90°   正交
cos(θ) = -1 → θ = 180°  反向
```

实际用下来，现代 embedding 模型很少给出 cos < 0 的文本对。一个常见解释是各向异性（anisotropy）：训练出来的向量往往挤在空间里一个比较窄的锥形区域里，随便拿两段无关文本，cos 也多半是个不大的正数。

---

## 7. 完整对比 demo

```python
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

输出大致如下：

```
        猫 ↔ kitten              cos=0.638  dot=0.638  L2=0.851
        猫 ↔ 狗                  cos=0.633  dot=0.633  L2=0.857
        猫 ↔ Linux               cos=0.082  dot=0.082  L2=1.356
   取消订阅 ↔ 如何停止自动续费    cos=0.527  dot=0.527  L2=0.972
       北京 ↔ 中国首都            cos=0.622  dot=0.622  L2=0.870
     python ↔ snake               cos=0.265  dot=0.265  L2=1.212
     python ↔ 编程语言            cos=0.443  dot=0.443  L2=1.055
```

`cos` 和 `dot` 每一行都相等，因为 OpenAI 的输出是归一化过的。

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
    configuration={"hnsw": {"space": "cosine"}},  # or "l2" / "ip"；旧写法 metadata={"hnsw:space": ...} 仍可用
)
```

没有特殊理由的话，默认选 cosine。

---

## 9. 常见坑

| 坑 | 解 |
|----|----|
| 用 L2 距离比较未归一化向量 | 归一化后再比，或换 cosine |
| 跨模型直接比相似度 | 不行，必须用同一模型 |
| 认为"相似度 > 0.8 就是强相关" | 要看模型，用 top-k 代替阈值 |
| 拼接多个 embedding 再比较 | 通常效果更差，不如把整段重新 embed |

---

## 10. 下一步

- 这些向量是怎么训出来的：[Embedding 如何训练：Contrastive Learning 简史](./03-how-trained.md)
- 维度怎么选：[维度、精度与性能的权衡](./04-dimension-tradeoff.md)
- 向量库内部怎么检索：[HNSW / IVF / PQ 索引原理](../03-vector-db/06-index-algorithms.md)
