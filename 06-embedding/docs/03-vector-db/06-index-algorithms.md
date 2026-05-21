# HNSW / IVF / PQ 索引原理

> **一句话**：纯暴力搜索 (Flat) O(N) 慢，所以现代向量库用 **HNSW**（图）或 **IVF**（倒排）+ **PQ**（量化）做近似最近邻 (ANN) 搜索——快 100x，召回率 95%+。

---

## 1. 为啥需要近似搜索

```
1 亿条 × 1024 维 暴力搜：
  每次 query 要算 1 亿次 cosine ≈ 数秒
  
ANN 索引：
  ~10ms，召回率 95%+
```

牺牲一点点准度换巨大性能提升。**99% 场景能接受**（"我要的相关文档"比"绝对最近邻"重要）。

---

## 2. HNSW（最主流）

> Hierarchical Navigable Small World

直觉：像"高速公路 + 国道 + 乡道"的多层图。

```
Layer 2:    A -------- B
            |          |
Layer 1:    A--C---D---B
            |   |       |
Layer 0:    A-X-C-Y-D-Z-B  (所有点)
```

**搜索**：

1. 从顶层（最稀疏）开始
2. 找当前层最近邻
3. 下一层继续找，越来越精细
4. 到底层找最终 top-k

**插入**：每个新点按概率分到某层，然后跟该层近邻连边。

**复杂度**：O(log N)。

**参数**：

| 参数 | 含义 | 默认 | 调高代价 |
|------|------|------|----------|
| `M` | 每节点最多连几条边 | 16 | 内存 + 构建时间 |
| `ef_construction` | 构建时探索深度 | 64 | 构建时间 |
| `ef_search` | 查询时探索深度 | 40 | 查询时间 |

调高 `ef_search` → 召回率提升、延迟增加。**常用 100-200**。

---

## 3. IVF（倒排）

> Inverted File Index

直觉：先把所有 doc 聚类成 N 个簇，搜索时只看最近的几个簇。

```
1. 训练：把所有向量做 k-means → 100 个簇
2. 索引：每个向量记录"属于哪簇"
3. 搜索：
   query 找最近的 nprobe 簇 → 只在这些簇里精确搜
```

**参数**：

| 参数 | 含义 | 默认 |
|------|------|------|
| `nlist` | 簇数 | sqrt(N) ~ 4 × sqrt(N) |
| `nprobe` | 搜索时探索几个簇 | 1-20 |

调高 `nprobe` → 召回率 ↑ / 速度 ↓。

**复杂度**：O(nprobe × N/nlist)。

比 HNSW 召回率略低，但**索引大小小、构建快**。大数据集用得多。

---

## 4. Flat（暴力）

```
不建索引，每次 query 算所有距离
```

- 召回率 100%
- 速度 O(N)
- 适合：< 10 万条 / 不能接受任何召回损失

```sql
-- pgvector 不建索引就是 Flat
ORDER BY embedding <=> query_vec LIMIT 5;
```

---

## 5. PQ（Product Quantization）

> 把向量压缩，节省存储 + 加速距离计算

直觉：把 1024 维向量切成 16 段（每段 64 维），每段用 k-means 找 256 个"代表向量"，原 64 维浮点 → 1 个字节（0-255 索引）。

```
原: 1024 × 4 bytes = 4096 bytes
PQ: 16 segments × 1 byte = 16 bytes  (× 256)
```

精度下降，但**距离计算变成查表 + 加法**，cache-friendly，飞快。

通常跟 IVF 组合：**IVF-PQ**。

---

## 6. HNSW vs IVF 对比

| | HNSW | IVF (+ PQ) |
|---|---|---|
| 召回率 | 高 (95%+) | 中-高 |
| 速度 | 快 | 极快（PQ） |
| 索引大小 | 大（图） | 小 |
| 构建时间 | 慢 | 快 |
| 量级适合 | < 1 亿 | > 1 亿（IVF-PQ）|
| 增量更新 | ✅ 好 | ⚠️ 一般 |

**默认选 HNSW**，量级到亿级再考虑 IVF-PQ。

---

## 7. 在不同向量库里怎么用

### Qdrant

默认 HNSW，参数：

```python
from qdrant_client.models import HnswConfigDiff


client.create_collection(
    collection_name="docs",
    vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
    hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
)


# 查询时
hits = client.search(
    collection_name="docs",
    query_vector=[...],
    limit=5,
    search_params={"hnsw_ef": 128},
)
```

### Pinecone

内部用专有 HNSW 变体，不暴露参数。

### pgvector

```sql
-- HNSW
CREATE INDEX ON docs USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- IVF
CREATE INDEX ON docs USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- 查询时
SET hnsw.ef_search = 100;
SET ivfflat.probes = 10;
```

### Milvus

支持 HNSW / IVF_FLAT / IVF_PQ / DISKANN 等多种：

```python
collection.create_index(
    field_name="embedding",
    index_params={
        "index_type": "HNSW",
        "metric_type": "COSINE",
        "params": {"M": 16, "efConstruction": 64},
    },
)
```

---

## 8. Recall vs Latency trade-off

实测 100 万 × 1024 维，ground truth 是 Flat 召回结果：

| 索引 | ef_search / nprobe | Recall@10 | P95 Latency |
|------|-------------------|-----------|-------------|
| Flat | - | 100% | 800 ms |
| HNSW | 40 | 92% | 5 ms |
| HNSW | 100 | 97% | 8 ms |
| HNSW | 200 | 99% | 14 ms |
| IVF (100 lists) | 1 | 70% | 3 ms |
| IVF (100 lists) | 10 | 92% | 10 ms |
| IVF-PQ (16 sub) | 10 | 88% | 4 ms |

**实战调参**：

1. 从默认开始
2. 跑 evalset 看 recall
3. 不够 → 调高 ef_search / nprobe
4. 太慢 → 调低或换索引

---

## 9. DiskANN：当内存装不下

> 微软 2019，索引在硬盘上也能跑

适合：数据 100GB+，内存装不下。

支持的库：

- Milvus（`index_type="DISKANN"`)
- Qdrant 部分支持
- pgvectorscale（pgvector 加强版，timescale 出品）

---

## 10. ColBERT：多向量

每个 doc 用多个向量（每个 token 一个）代替单一向量：

```
传统：doc → 1 个 vector
ColBERT：doc → N 个 vector (每 token 一个)
```

更精细，但存储 × 50-100，检索慢。

主流库不直接支持，要专门方案。BGE-m3 输出 ColBERT vecs 可以用 multi-vector 搜索。

---

## 11. demo：HNSW 调参影响

```python
# demos/vector_db/06_hnsw_tuning.py
import time
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, HnswConfigDiff


client = QdrantClient(":memory:")


# 100K 随机向量
np.random.seed(42)
vectors = np.random.randn(100_000, 384).astype(np.float32)
vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)


for m, ef_construct in [(16, 64), (32, 128)]:
    name = f"docs_m{m}_ef{ef_construct}"
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=384, distance=Distance.COSINE),
        hnsw_config=HnswConfigDiff(m=m, ef_construct=ef_construct),
    )

    t = time.time()
    client.upsert(
        collection_name=name,
        points=[PointStruct(id=i, vector=v.tolist()) for i, v in enumerate(vectors)],
    )
    build_time = time.time() - t

    # 测查询
    query = vectors[0].tolist()
    for ef_search in [40, 100, 200]:
        t = time.time()
        for _ in range(100):
            client.search(
                collection_name=name,
                query_vector=query,
                limit=10,
                search_params={"hnsw_ef": ef_search},
            )
        query_avg = (time.time() - t) / 100 * 1000

        print(f"M={m} ef_c={ef_construct} ef_s={ef_search}: "
              f"build={build_time:.1f}s query_avg={query_avg:.2f}ms")
```

---

## 12. 实战建议

```
默认 HNSW + 默认参数 起步

发现召回率低 (< 90%)：
  → ef_search 100 → 200

延迟太高 (> 50ms)：
  → ef_search 40
  → 或加 quantization

数据量 > 1 亿：
  → IVF-PQ 或 DiskANN

数据要频繁更新：
  → HNSW（更新友好）

数据基本不变 + 内存紧：
  → IVF-PQ
```

---

## 13. 下一步

- 📖 混合存储（vec + metadata） → [07-hybrid-storage.md](./07-hybrid-storage.md)
- 📖 chunking → [04-chunking/01-why-chunking.md](../04-chunking/01-why-chunking.md)
- 📖 召回评测 → [06-evaluation/01-metrics.md](../06-evaluation/01-metrics.md)
