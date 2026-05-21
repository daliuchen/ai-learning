# Dimension / 精度 / 性能权衡

> **一句话**：维度越高质量略好但**存储和检索成本显著上升**——大多数生产场景用 384-1536 维就足够，3072 维只在质量敏感任务才值得。

---

## 1. 主流模型 dimension 对比

| 模型 | Dimension | MTEB avg |
|------|-----------|----------|
| sentence-transformers/all-MiniLM-L6-v2 | 384 | ~58 |
| BAAI/bge-small-zh-v1.5 | 512 | ~63 (C-MTEB) |
| BAAI/bge-base-zh-v1.5 | 768 | ~65 |
| BAAI/bge-large-zh-v1.5 | 1024 | ~66 |
| OpenAI text-embedding-3-small | 1536 | ~62 |
| OpenAI text-embedding-3-large | 3072 | ~64 |
| Cohere embed-v3 | 1024 | ~64 |
| voyage-3 | 1024 | ~65 |

**质量收益 vs 维度增长**：从 384 → 1536 平均提高 5-7 分；从 1536 → 3072 只提 1-2 分。

---

## 2. 存储成本

一条 embedding 的字节数：

```
float32: dim × 4 bytes
float16: dim × 2 bytes  (有些库不支持)
int8:    dim × 1 byte   (量化)
```

百万条文档的存储：

| dim | float32 | float16 | int8 |
|-----|---------|---------|------|
| 384 | 1.5 GB | 768 MB | 384 MB |
| 768 | 3 GB | 1.5 GB | 768 MB |
| 1536 | 6 GB | 3 GB | 1.5 GB |
| 3072 | 12 GB | 6 GB | 3 GB |

**1 亿条文档** × 3072 维 float32 = **1.2 TB 纯 embedding**，加 metadata / 索引开销翻倍。

---

## 3. 检索速度

向量库的检索复杂度：

- **Flat**（暴力）: O(N × dim) — 都得算
- **HNSW**: O(log N × dim) — 主流，详见 [03-vector-db/06-index-algorithms.md](../03-vector-db/06-index-algorithms.md)

dim × 2 → 每次距离计算时间 × 2。检索 latency 直接相关。

```
百万级文档检索 latency（HNSW，cosine）：
  dim=384:  ~3ms
  dim=1536: ~10ms
  dim=3072: ~20ms
```

QPS 高的场景 dim 越小越好。

---

## 4. Matryoshka：一向量多用

OpenAI text-embedding-3 / Nomic-Embed 支持"套娃"：训出一个 3072 维向量，前 N 维（256 / 512 / 1024）也可用：

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

效果：

| 截到 | MTEB avg | 相对 3072 维 |
|------|----------|--------------|
| 256 | ~58 | -6 |
| 512 | ~60 | -4 |
| 1024 | ~62 | -2 |
| 1536 | ~63 | -1 |
| 3072 | ~64 | 0 |

**实战**：

- 召回阶段用低维（1024 / 512）省成本
- 精排阶段如果需要可以用全维（但通常上 rerank 模型更好）

详见 [02-models/01-openai.md](../02-models/01-openai.md)。

---

## 5. 量化（quantization）

把 float32 压缩成 int8 / int4，省 4x / 8x 存储，速度也快。

```python
import numpy as np


def quantize_int8(vec_float32):
    """简单 int8 量化（不是最优）"""
    vec = np.array(vec_float32)
    scale = vec.max() / 127
    return (vec / scale).astype(np.int8), scale


def dequantize_int8(vec_int8, scale):
    return vec_int8.astype(np.float32) * scale
```

实际生产用更精细的量化（如 PQ / SQ），向量库自带：

- Qdrant: scalar quantization 内置
- Faiss: PQ / OPQ / SQ
- Pinecone: 内部自动

质量损失通常 < 3% MTEB 分，召回率几乎不变。

---

## 6. 决策树

```
你的场景需要多准？
├─ 普通业务搜索 / chat 问答
│   → 768-1024 维（BGE-base / cohere-embed-v3）
│
├─ 高准确度（法律 / 医疗 / 学术）
│   → 1536-3072 维（text-embedding-3-large / voyage-3）
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
# demos/foundations/04_dimension.py
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

通常 dim ≥ 512 都能找对，dim=256 可能召错。

---

## 8. 实测：256 维够用吗

经验（同一份 1 万条客服 KB，retrieval evalset 200 条）：

| dim | Recall@5 | Latency P95 | 存储 |
|-----|----------|-------------|------|
| 256 | 82% | 4 ms | 10 MB |
| 512 | 87% | 5 ms | 20 MB |
| 1024 | 90% | 8 ms | 40 MB |
| 1536 | 91% | 10 ms | 60 MB |
| 3072 | 92% | 18 ms | 120 MB |

**结论**：90% 客服场景 768-1024 维就够，加 rerank 模型补上面那一点点。

---

## 9. 常见坑

| 坑 | 解 |
|----|----|
| 一开始就用 3072 维"图省心" | 量大时迁移到低维代价高，从 768-1024 起步 |
| 量化后召回崩了 | 调高 ef_search 或保留 float32 索引做 rerank |
| 不同维度的库里 vector 混存 | 不行，dim 是 fixed 的，要重建 |
| 想"截一半"看效果但模型不支持 Matryoshka | 老模型截维就废了，得换 |

---

## 10. 下一步

- 📖 多语言怎么处理 → [05-multilingual.md](./05-multilingual.md)
- 📖 多模态 embedding → [06-multimodal.md](./06-multimodal.md)
- 📖 OpenAI Matryoshka 实操 → [02-models/01-openai.md](../02-models/01-openai.md)
- 📖 量化 / 向量库选型 → [03-vector-db/01-selection.md](../03-vector-db/01-selection.md)
