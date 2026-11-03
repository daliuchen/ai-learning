# 维度、精度与性能的权衡

> 维度越高，质量会好一点，但存储和检索成本会明显上去。大多数生产场景 384 到 1536 维就够了，3072 维只在对质量特别敏感的任务里才划算。这篇把这笔账拆开算一遍。

---

## 1. 主流模型 dimension 对比

| 模型 | Dimension | MTEB avg |
|------|-----------|----------|
| sentence-transformers/all-MiniLM-L6-v2 | 384 | ~56（MTEB 英文 v1） |
| BAAI/bge-small-zh-v1.5 | 512 | 57.8（C-MTEB，模型卡） |
| BAAI/bge-base-zh-v1.5 | 768 | 63.1（C-MTEB，模型卡） |
| BAAI/bge-large-zh-v1.5 | 1024 | 64.5（C-MTEB，模型卡） |
| OpenAI text-embedding-3-small | 1536 | 62.3（MTEB v1，官方） |
| OpenAI text-embedding-3-large | 3072 | 64.6（MTEB v1，官方） |
| Cohere embed-english-v3.0（老） | 1024 | ~64.5（MTEB v1） |
| voyage-3（老，已被 voyage-4 取代） | 1024 | 官方未给 MTEB 平均分 |
| 千问 text-embedding-v4 | 默认 1024（64–2048 可选） | MTEB 68.36（官方，1024 维，新口径） |
| Qwen3-Embedding-8B | 最高 4096 | MMTEB 70.58（模型卡，新口径） |

读这张表要小心：各行分数的口径不同（英文 MTEB v1、C-MTEB、新的 MTEB v2 / MMTEB），带"~"的是榜单上的近似值，不能跨行直接比大小。它只用来说明一个趋势：维度大不一定更好，而且可以截维。千问 v4 官方给过截维数据（MTEB）：512 维 64.73，1024 维 68.36，2048 维 71.58。

同一个模型截维时，收益是递减的：text-embedding-3-large 从 256 维到 1024 维，官方 MTEB 分从 62.0 涨到 64.1，再到 3072 维只多 0.5 分（64.6）。

---

## 2. 存储成本

一条 embedding 占的字节数：

```
float32: dim × 4 bytes
float16: dim × 2 bytes  (有些库不支持)
int8:    dim × 1 byte   (量化)
```

一百万条文档需要的存储：

| dim | float32 | float16 | int8 |
|-----|---------|---------|------|
| 384 | 1.5 GB | 768 MB | 384 MB |
| 768 | 3 GB | 1.5 GB | 768 MB |
| 1536 | 6 GB | 3 GB | 1.5 GB |
| 3072 | 12 GB | 6 GB | 3 GB |

换成 1 亿条文档、3072 维 float32，光 embedding 就是 1.2 TB，再加上 metadata 和索引，还要翻一倍。

---

## 3. 检索速度

向量库的检索复杂度：

- Flat（暴力搜索）：O(N × dim)，每一条都要算
- HNSW：O(log N × dim)，目前的主流，原理见[HNSW / IVF / PQ 索引原理](../03-vector-db/06-index-algorithms.md)

维度翻倍，每次距离计算的时间也翻倍，检索延迟跟着涨：

```
百万级文档检索 latency（HNSW，cosine，示意量级，随硬件和参数变化）：
  dim=384:  ~3ms
  dim=1536: ~10ms
  dim=3072: ~20ms
```

QPS 要求高的场景，维度越小越好。

---

## 4. Matryoshka：一向量多用

OpenAI text-embedding-3 和 Nomic-Embed 支持"套娃"式训练：训出来的 3072 维向量，取前 256 / 512 / 1024 维也能直接用。

```python
from openai import OpenAI
client = OpenAI()

resp = client.embeddings.create(
    model="text-embedding-3-large",
    input="hello",
    dimensions=512,   # ← 指定截断到 512 维
)
print(len(resp.data[0].embedding))  # 512
```

截到不同维度的效果（text-embedding-3-large，OpenAI 发布时公布的 MTEB v1 平均分；512 和 1536 维官方没给）：

| 截到 | MTEB avg | 相对 3072 维 |
|------|----------|--------------|
| 256 | 62.0 | -2.6 |
| 1024 | 64.1 | -0.5 |
| 3072 | 64.6 | 0 |

我的用法是召回阶段用低维（1024 或 512）省成本；精排阶段需要的话可以用全维，不过通常直接上 rerank 模型效果更好。具体参数见[OpenAI text-embedding-3 系列](../02-models/01-openai.md)。

---

## 5. 量化（quantization）

把 float32 压成 int8 或 int4，存储分别省 4 倍和 8 倍，速度也更快。

```python
import numpy as np


def quantize_int8(vec_float32):
    """简单 int8 量化（不是最优）"""
    vec = np.array(vec_float32)
    scale = np.abs(vec).max() / 127      # 按绝对值最大的分量定 scale，负数才不会溢出
    return np.round(vec / scale).astype(np.int8), scale


def dequantize_int8(vec_int8, scale):
    return vec_int8.astype(np.float32) * scale
```

上面只是演示。生产环境用的是更精细的量化（PQ、SQ 等），向量库一般都自带：

- Qdrant：内置 scalar quantization
- Faiss：PQ / OPQ / SQ
- Pinecone：内部自动处理

int8 标量量化的召回损失通常很小，再用原始 float32 向量对候选做一次 rescore，基本可以忽略；二值量化省得更多，损失也更大，一般要配合 oversampling + rescore 使用。

---

## 6. 决策树

```
你的场景需要多准？
├─ 普通业务搜索 / chat 问答
│   → 768-1024 维（BGE-base / bge-m3 / 千问 v4 1024 维）
│
├─ 高准确度（法律 / 医疗 / 学术）
│   → 1536-3072 维（text-embedding-3-large / embed-v5.0 / voyage-4 / 千问 v4 2048 维）；用 pgvector 要注意 vector 索引上限 2000 维
│
└─ 移动端 / 边缘 / 高并发
    → 384-512 维（all-MiniLM / bge-small）+ 量化

数据量？
├─ < 100 万：dim 无所谓
├─ 100 万 - 1000 万：选 ≤ 1024 维
└─ > 1000 万：考虑 Matryoshka + 量化

预算？
├─ 紧 → 开源 + 自部署
└─ 充足 → 商业 API（OpenAI / Cohere / Voyage）
```

---

## 7. demo：维度对召回的影响

```python
import numpy as np
from openai import OpenAI

client = OpenAI()


def embed_with_dim(texts, dim):
    resp = client.embeddings.create(
        model="text-embedding-3-large",
        input=texts,
        dimensions=dim,
    )
    return np.array([d.embedding for d in resp.data])


query = "怎么取消订阅"
docs = [
    "如何关闭自动续费",
    "如何登录账号",
    "停止订阅的方法",
    "重置密码教程",
    "退款流程",
]


for dim in [256, 512, 1024, 1536, 3072]:
    vecs = embed_with_dim([query, *docs], dim=dim)
    qv, dvs = vecs[0], vecs[1:]
    sims = dvs @ qv  # 已归一化
    top = np.argsort(-sims)
    print(f"dim={dim}: top doc = {docs[top[0]]} (sim={sims[top[0]]:.3f})")
```

一般 512 维及以上都能找对，256 维有可能召错。

---

## 8. 256 维够用吗（示意）

下表是示意数据，用来说明趋势：场景设定为 1 万条客服知识库、200 条检索评测集，Recall 和延迟没有固定到某个模型和硬件，不要当成基准引用。存储一列是按 float32 直接算出来的（10000 × 维度 × 4 字节）。

| dim | Recall@5 | Latency P95 | 存储 |
|-----|----------|-------------|------|
| 256 | 82% | 4 ms | 10 MB |
| 512 | 87% | 5 ms | 20 MB |
| 1024 | 90% | 8 ms | 40 MB |
| 1536 | 91% | 10 ms | 60 MB |
| 3072 | 92% | 18 ms | 120 MB |

从 1024 维升到 3072 维，Recall 只多 2 个点，延迟和存储却都翻了一倍多。客服这类场景，多数情况下 768–1024 维就够了，剩下那一点差距交给 rerank 模型去补。

---

## 9. 常见坑

| 坑 | 解 |
|----|----|
| 图省事一开始就上 3072 维 | 数据量大了再迁到低维代价很高，建议从 768-1024 起步 |
| 量化后召回明显下降 | 调高 ef_search，或保留一份 float32 索引用于 rerank |
| 同一个库里混存不同维度的向量 | 不行，维度是固定的，只能重建 |
| 模型不支持 Matryoshka 却想截一半看看 | 老模型截维后基本不能用，只能换模型 |

---

## 10. 下一步

- 多语言怎么处理：[多语言 Embedding 的工作原理](./05-multilingual.md)
- 多模态 embedding：[多模态 Embedding：CLIP 原理](./06-multimodal.md)
- OpenAI 的 Matryoshka 实操：[OpenAI text-embedding-3 系列](../02-models/01-openai.md)
- 量化和向量库选型：[向量库选型全景 + 决策树](../03-vector-db/01-selection.md)
