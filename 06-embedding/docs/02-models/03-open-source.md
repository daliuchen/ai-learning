# 开源 SOTA：BGE / Nomic / Jina

> **一句话**：自部署 embedding 模型首选 **BAAI/bge-m3**（多语+长文+多向量）或 **nomic-embed-text-v1.5**（英文+Matryoshka）——MTEB 接近商业 API，自己掌控。

---

## 1. 主流开源模型

| 模型 | dim | MTEB | 备注 |
|------|-----|------|------|
| `BAAI/bge-large-en-v1.5` | 1024 | 64.2 | 英文强 |
| `BAAI/bge-large-zh-v1.5` | 1024 | 64.5 (C-MTEB) | 中文强 |
| `BAAI/bge-m3` | 1024 | - | 多语+长文+多向量 |
| `nomic-ai/nomic-embed-text-v1.5` | 64-768 | 62.4 | Matryoshka |
| `intfloat/multilingual-e5-large` | 1024 | 多语 | 100+ 语言 |
| `jinaai/jina-embeddings-v3` | 1024 | 65.5 | task-aware (LoRA) |
| `Snowflake/snowflake-arctic-embed-l-v2.0` | 1024 | 65+ | 多语 |
| `mixedbread-ai/mxbai-embed-large-v1` | 1024 | 64.7 | Matryoshka |

排行榜实时变化，看 https://huggingface.co/spaces/mteb/leaderboard。

---

## 2. BGE 实战

### 2.1 用 sentence-transformers

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-large-zh-v1.5")

# query 加 instruction（C-MTEB 推荐）
queries = ["为这个句子生成表示以用于检索相关文章：" + "如何取消订阅"]
docs = ["停止自动续费的方法", "重置密码教程", ...]

q_vecs = model.encode(queries, normalize_embeddings=True)
d_vecs = model.encode(docs, normalize_embeddings=True)

import numpy as np
sims = d_vecs @ q_vecs[0]
print(np.argsort(-sims)[:3])
```

⚠️ **BGE-zh 必须给 query 加 instruction 前缀**，不加效果差不少。doc 不加。

### 2.2 用 FlagEmbedding（官方）

```python
from FlagEmbedding import FlagModel

model = FlagModel(
    "BAAI/bge-large-zh-v1.5",
    query_instruction_for_retrieval="为这个句子生成表示以用于检索相关文章：",
    use_fp16=True,  # 半精度提速
)

q_vecs = model.encode_queries(queries)  # 自动加 instruction
d_vecs = model.encode(docs)             # 不加
```

`FlagEmbedding` 是 BGE 官方库，比 sentence-transformers 更贴合 BGE 设计。

---

## 3. BGE-M3（M3 = Multilingual / Multi-functional / Multi-granular）

```python
from FlagEmbedding import BGEM3FlagModel


model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)


# 一次调用拿三种 embedding
output = model.encode(
    sentences=["如何取消订阅"],
    return_dense=True,           # ✓ dense 1024 维（最常用）
    return_sparse=True,          # ✓ sparse（BM25-like）
    return_colbert_vecs=True,    # ✓ ColBERT 多向量（每个 token 一个 vec）
)

dense_vec = output["dense_vecs"][0]              # shape (1024,)
sparse_dict = output["lexical_weights"][0]       # 关键词权重 dict
colbert_vecs = output["colbert_vecs"][0]         # shape (n_tokens, 1024)
```

**亮点**：

- 100+ 语言
- 长文支持（8192 tokens，远高于 BGE-large 的 512）
- 同时给 dense / sparse / multi-vector → 一站式混合检索

详见 [05-retrieval/02-bm25-fusion.md](../05-retrieval/02-bm25-fusion.md)。

---

## 4. Nomic Embed（Matryoshka 友好）

```python
from sentence_transformers import SentenceTransformer


model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)


# 必须加 prefix
sentences = [
    "search_query: 如何取消订阅",
    "search_document: 停止自动续费",
]

embeddings = model.encode(sentences, normalize_embeddings=True)
print(embeddings.shape)  # (2, 768)


# Matryoshka：截到 256 维
embeddings_256 = embeddings[:, :256]
embeddings_256 /= np.linalg.norm(embeddings_256, axis=1, keepdims=True)  # 重新归一化
```

Nomic 训练 prefix：

- `search_query:`
- `search_document:`
- `clustering:`
- `classification:`
- `multi:`

---

## 5. jina-embeddings-v3（task-aware）

```python
from sentence_transformers import SentenceTransformer


model = SentenceTransformer("jinaai/jina-embeddings-v3", trust_remote_code=True)


# 不同 task 用不同 LoRA
queries = model.encode(
    ["如何取消订阅"],
    task="retrieval.query",
    normalize_embeddings=True,
)
docs = model.encode(
    ["停止自动续费"],
    task="retrieval.passage",
    normalize_embeddings=True,
)
```

可用 task：

- `retrieval.query` / `retrieval.passage`
- `separation`
- `classification`
- `text-matching`

模型内部加载对应 LoRA → 同一基座模型，多任务最优。

---

## 6. 自部署：性能

```
机器: NVIDIA A100 / 40G  
模型: bge-large-zh-v1.5 (1024 维)

吞吐:
  batch=64, fp16:  ~1500 sentences/sec
  batch=128, fp16: ~2500 sentences/sec
  batch=256, fp16: ~3500 sentences/sec

CPU (16 核):
  batch=16:        ~30 sentences/sec
  ONNX + INT8:     ~120 sentences/sec
```

CPU 也能跑但慢得多。生产建议 GPU。

---

## 7. 部署形态

### 7.1 Python 直接跑（开发 / 小流量）

```python
model = SentenceTransformer("BAAI/bge-large-zh-v1.5")

# 在 FastAPI / Celery 里直接 model.encode(...)
```

### 7.2 Text Embeddings Inference (TEI)

HuggingFace 官方推理服务，专为 embedding 优化：

```bash
docker run -p 8080:80 \
  --gpus all \
  -v $PWD/data:/data \
  ghcr.io/huggingface/text-embeddings-inference:1.2 \
  --model-id BAAI/bge-large-zh-v1.5
```

调用：

```python
import httpx

resp = httpx.post("http://localhost:8080/embed", json={
    "inputs": ["如何取消订阅"],
    "normalize": True,
})
vec = resp.json()[0]
```

TEI 性能优于 sentence-transformers 直跑，支持 dynamic batching。

### 7.3 ONNX / TensorRT

把模型转 ONNX：

```python
from sentence_transformers import SentenceTransformer
from optimum.onnxruntime import ORTModelForFeatureExtraction


# 一次转换
model = SentenceTransformer("BAAI/bge-large-zh-v1.5")
model.save_to_onnx("./bge-onnx")


# 用 onnxruntime 跑（CPU / GPU）
ort_model = ORTModelForFeatureExtraction.from_pretrained("./bge-onnx", provider="CPUExecutionProvider")
```

CPU 上 ONNX 比 PyTorch 快 2-4 倍。

### 7.4 INT8 量化

```bash
pip install optimum[onnxruntime]
optimum-cli onnxruntime quantize \
  --onnx_model ./bge-onnx \
  --output ./bge-onnx-int8 \
  --avx512
```

CPU 上速度 × 2-3，召回率掉 1-2%。

---

## 8. 常用开源模型的中文表现

实测 200 条客服 KB（Recall@5 评测）：

| 模型 | Recall@5 |
|------|----------|
| BGE-large-zh-v1.5 | 91% |
| BGE-base-zh-v1.5 | 89% |
| BGE-m3 | 89% |
| multilingual-e5-large | 88% |
| jina-embeddings-v3 | 87% |
| text-embedding-3-small (OpenAI) | 88% |
| text-embedding-3-large (OpenAI) | 91% |

**结论**：

- 中文场景 BGE-large-zh 跟 OpenAI 3-large 同一档
- 多语言场景 BGE-m3 / multilingual-e5 更稳
- 量大 / 合规 → 自部署 BGE 比 OpenAI 划算

---

## 9. 完整 demo

```python
# demos/models/03_open_source.py
import numpy as np
from sentence_transformers import SentenceTransformer


model = SentenceTransformer("BAAI/bge-large-zh-v1.5")


def encode_queries(queries):
    queries = ["为这个句子生成表示以用于检索相关文章：" + q for q in queries]
    return model.encode(queries, normalize_embeddings=True)


def encode_docs(docs):
    return model.encode(docs, normalize_embeddings=True)


query = "如何取消订阅"
docs = [
    "如何关闭自动续费",
    "如何登录账号",
    "停止订阅的方法",
    "重置密码教程",
]


qv = encode_queries([query])[0]
dvs = encode_docs(docs)

sims = dvs @ qv
order = np.argsort(-sims)

print(f"Query: {query}\n")
for i in order:
    print(f"  {docs[i]:<20}  sim={sims[i]:.4f}")
```

---

## 10. 选型小结

```
预算紧 + 中文为主 → BGE-large-zh
预算紧 + 多语言 → BGE-m3 或 multilingual-e5
预算紧 + 英文 → BGE-large-en 或 nomic-embed
要 Matryoshka → nomic-embed / mxbai
要长文 → BGE-m3（8K）
要多任务 → jina-embeddings-v3（task LoRA）
要混合检索一站式 → BGE-m3（dense+sparse+colbert）
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| BGE-zh 没加 instruction 前缀 | 必加，否则丢分 |
| 多语言用单语模型 | 跨语言全废 |
| CPU 直接跑大模型 | 用 ONNX / TEI 优化 |
| 用 sentence-transformers 跑 BGE 但不归一化 | `normalize_embeddings=True` |

---

## 12. 下一步

- 📖 sentence-transformers 工具链 → [04-sentence-transformers.md](./04-sentence-transformers.md)
- 📖 rerank 模型补强 → [05-rerank.md](./05-rerank.md)
- 📖 MTEB 怎么看 → [06-mteb-selection.md](./06-mteb-selection.md)
