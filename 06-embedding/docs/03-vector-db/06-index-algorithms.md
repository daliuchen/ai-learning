# HNSW / IVF / PQ 索引原理

> 暴力搜索（Flat）是 O(N) 的，数据一多就慢。现在的向量库普遍用 HNSW（图）或 IVF（倒排）加 PQ（量化）做近似最近邻（ANN）搜索，速度能快上百倍，召回率仍在 95% 以上。这篇讲这几种索引怎么工作、参数怎么调。

---

## 1. 为什么需要近似搜索

```
1 亿条 × 1024 维 暴力搜：
  每次 query 要算 1 亿次 cosine ≈ 数秒
  
ANN 索引：
  ~10ms，召回率 95%+
```

用一点点准确度换来巨大的性能提升，绝大多数场景都能接受。毕竟业务要的是"相关文档"，而不是数学意义上绝对的最近邻。

---

## 2. HNSW（最主流）

> Hierarchical Navigable Small World

可以把它想成高速公路、国道、乡道组成的多层路网：

```
Layer 2:    A -------- B
            |          |
Layer 1:    A--C---D---B
            |   |       |
Layer 0:    A-X-C-Y-D-Z-B  (所有点)
```

搜索过程：

1. 从最稀疏的顶层开始
2. 在当前层找到最近的点
3. 下到下一层接着找，越往下越精细
4. 在最底层得到最终的 top-k

插入时，每个新点按概率被分配到某一层，再和该层的近邻连边。整体复杂度是 O(log N)。

主要参数：

| 参数 | 含义 | 默认（pgvector） | 调高代价 |
|------|------|------|----------|
| `M` | 每节点最多连几条边 | 16 | 内存 + 构建时间 |
| `ef_construction` | 构建时探索深度 | 64（Qdrant 默认 100） | 构建时间 |
| `ef_search` | 查询时探索深度 | 40 | 查询时间 |

`ef_search` 调高，召回率上升，延迟也随之增加，常用值在 100-200 之间。

---

## 3. IVF（倒排）

> Inverted File Index

思路是先把所有文档聚成 N 个簇，搜索时只看离 query 最近的几个簇：

```
1. 训练：把所有向量做 k-means → 100 个簇
2. 索引：每个向量记录"属于哪簇"
3. 搜索：
   query 找最近的 nprobe 簇 → 只在这些簇里精确搜
```

参数：

| 参数 | 含义 | 常用取值 |
|------|------|------|
| `nlist` | 簇数 | sqrt(N) ~ 4 × sqrt(N) |
| `nprobe` | 搜索时探索几个簇 | 1-20 |

`nprobe` 越大，召回率越高，速度越慢。复杂度是 O(nprobe × N/nlist)。

IVF 的召回率比 HNSW 略低，但索引更小、构建更快，所以在大数据集上用得多。

---

## 4. Flat（暴力）

```
不建索引，每次 query 算所有距离
```

召回率 100%，速度是 O(N)。适合 10 万条以内，或者完全不能接受召回损失的场景。

```sql
-- pgvector 不建索引就是 Flat
ORDER BY embedding <=> query_vec LIMIT 5;
```

---

## 5. PQ（Product Quantization）

> 把向量压缩，节省存储 + 加速距离计算

做法是把 1024 维向量切成 16 段，每段 64 维；每段用 k-means 找出 256 个"代表向量"，于是原来 64 个浮点数就变成 1 个字节（0-255 的编号）。

```
原: 1024 × 4 bytes = 4096 bytes
PQ: 16 segments × 1 byte = 16 bytes  (× 256)
```

精度会下降，但距离计算变成了查表加法，对 CPU cache 很友好，所以非常快。

PQ 通常和 IVF 搭配使用，也就是 IVF-PQ。

---

## 6. HNSW vs IVF

| | HNSW | IVF (+ PQ) |
|---|---|---|
| 召回率 | 高 (95%+) | 中-高 |
| 速度 | 快 | 极快（PQ） |
| 索引大小 | 大（图） | 小 |
| 构建时间 | 慢 | 快 |
| 量级适合 | < 1 亿 | > 1 亿（IVF-PQ）|
| 增量更新 | 好 | 一般 |

**默认选 HNSW**，数据到了亿级再考虑 IVF-PQ。

---

## 7. 在不同向量库里怎么用

### Qdrant

默认就是 HNSW，参数这样设：

```python
from qdrant_client.models import HnswConfigDiff


client.create_collection(
    collection_name="docs",
    vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
    hnsw_config=HnswConfigDiff(m=16, ef_construct=100),
)


# 查询时
hits = client.query_points(
    collection_name="docs",
    query=[...],
    limit=5,
    search_params={"hnsw_ef": 128},
).points
```

### Pinecone

用的是专有索引，不对外暴露参数。

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

支持 HNSW、IVF_FLAT、IVF_PQ、DISKANN 等多种索引：

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

## 8. 召回率和延迟的取舍

下表是示意数据，用来说明各索引在召回和延迟上的相对位置：设定为 100 万条 1024 维数据，以 Flat 的结果作为 ground truth，具体数值会随硬件、实现和数据分布变化，没有对应某次固定配置的测量。

| 索引 | ef_search / nprobe | Recall@10 | P95 Latency |
|------|-------------------|-----------|-------------|
| Flat | - | 100% | 800 ms |
| HNSW | 40 | 92% | 5 ms |
| HNSW | 100 | 97% | 8 ms |
| HNSW | 200 | 99% | 14 ms |
| IVF (100 lists) | 1 | 70% | 3 ms |
| IVF (100 lists) | 10 | 92% | 10 ms |
| IVF-PQ (16 sub) | 10 | 88% | 4 ms |

调参顺序：

1. 先用默认参数
2. 跑 evalset 看召回率
3. 召回不够，调高 ef_search 或 nprobe
4. 太慢，就调低，或者换一种索引

---

## 9. DiskANN：内存装不下的时候

> 微软 2019 年提出，索引放在硬盘上也能跑

数据超过 100GB、内存装不下时可以考虑。目前支持它的有：

- Milvus（`index_type="DISKANN"`)
- pgvectorscale（Timescale 出品，StreamingDiskANN 索引）

Qdrant 没有 DiskANN，但可以把原始向量和 HNSW 图都放到磁盘（`on_disk=True`），内存里只留量化后的向量，思路相近。

---

## 10. ColBERT：多向量

ColBERT 不再用一个向量代表一篇文档，而是每个 token 一个向量：

```
传统：doc → 1 个 vector
ColBERT：doc → N 个 vector (每 token 一个)
```

匹配更精细，但存储随 token 数成倍增长（一个几百 token 的 chunk 就是几百个向量），检索也更慢。

Qdrant（1.10 起的 multivector + MaxSim）、Vespa 已经原生支持，pgvector 这类还要自己拼。BGE-m3 能输出 ColBERT 向量，可以拿来做 multi-vector 搜索。

---

## 11. 示例：HNSW 参数对性能的影响

```python
import time
import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, HnswConfigDiff


# 要连真正的 Qdrant 服务：本地模式（":memory:" / path=）不建 HNSW，只做暴力搜索，
# hnsw_config 和 hnsw_ef 在本地模式下都不生效
client = QdrantClient(url="http://localhost:6333")


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
    client.upload_points(
        collection_name=name,
        points=[PointStruct(id=i, vector=v.tolist()) for i, v in enumerate(vectors)],
        batch_size=1000,
    )
    # HNSW 在服务端后台构建，等 collection 状态变成 green 才算建完
    while client.get_collection(name).status != "green":
        time.sleep(0.5)
    build_time = time.time() - t

    # 测查询
    query = vectors[0].tolist()
    for ef_search in [40, 100, 200]:
        t = time.time()
        for _ in range(100):
            client.query_points(
                collection_name=name,
                query=query,
                limit=10,
                search_params={"hnsw_ef": ef_search},
            ).points
        query_avg = (time.time() - t) / 100 * 1000

        print(f"M={m} ef_c={ef_construct} ef_s={ef_search}: "
              f"build={build_time:.1f}s query_avg={query_avg:.2f}ms")
```

---

## 12. 调参建议

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

- 向量加 metadata：[混合存储：Vector + Metadata + Filter](./07-hybrid-storage.md)
- 切分：[为什么 Chunking 决定 RAG 的上限](../04-chunking/01-why-chunking.md)
- 召回评测：[检索评测指标：Recall@k / MRR / nDCG](../06-evaluation/01-metrics.md)
