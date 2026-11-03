# 开源模型：Qwen3-Embedding / BGE / Jina / Nomic

> 2026 年自部署 embedding，我会先试 **Qwen3-Embedding**：有 0.6B、4B、8B 三档，多语言、长文本、可变维度都支持。想要 dense、sparse、多向量一次拿齐，就用 BAAI/bge-m3；英文场景想要 Matryoshka，可以看 nomic 和 Jina v5。这些模型在 MTEB 上已经追平甚至超过商业 API，而且部署和数据都在自己手里。

本篇的本地模型代码（sentence-transformers / FlagEmbedding）按官方模型卡的写法给出，运行需要 torch，第一次会下载模型权重。榜单排名变化很快，所以本篇不写"第几名"，数字只引用模型卡原文并注明日期。如果想先不装任何东西验证效果，可以直接用千问 `text-embedding-v4` API（见 [OpenAI text-embedding-3 系列](./01-openai.md) 第 5 节）。

---

## 1. 主流开源模型

| 模型 | dim | MTEB | 备注 |
|------|-----|------|------|
| `Qwen/Qwen3-Embedding-8B`（另有 4B / 0.6B） | 最高 4096（32–4096 可自定义） | MMTEB 70.58（模型卡，称 2025-06-05 时位列多语言榜第 1） | 32k 上下文；配套 Qwen3-Reranker 0.6B/4B/8B |
| `BAAI/bge-large-en-v1.5` | 1024 | 64.2 | 英文强（老模型） |
| `BAAI/bge-large-zh-v1.5` | 1024 | 64.5 (C-MTEB) | 中文强 |
| `BAAI/bge-m3` | 1024 | - | 多语+长文（8192）+多向量，MIT 协议 |
| `nomic-ai/nomic-embed-text-v1.5` | 64-768 | 62.4 | Matryoshka |
| `intfloat/multilingual-e5-large` | 1024 | 多语 | 100+ 语言 |
| `jinaai/jina-embeddings-v3` | 1024 | 65.5 | task-aware (LoRA)；已有后继 v5（见下） |
| `Snowflake/snowflake-arctic-embed-l-v2.0` | 1024 | 65+ | 多语 |
| `mixedbread-ai/mxbai-embed-large-v1` | 1024 | 64.7 | Matryoshka |

排行榜一直在变，最新的看 https://huggingface.co/spaces/mteb/leaderboard。

表里不少是 2023–2024 年的老模型（bge-large 系、multilingual-e5、nomic v1.5、mxbai v1、arctic-embed-l-v2）。新项目建议从下面几个开始评估：

- Qwen3-Embedding：通义千问团队开源。0.6B 能在 CPU 或小显卡上跑，8B 质量最高，中文和多语言都强，是目前自部署的首选候选。
- jina-embeddings-v5：2026-02 随 Elastic 发布了 text-small、text-nano 两个小模型，5 月又出了支持图、音、视频的 omni 版，Jina 现在已并入 Elastic。参数量以各自模型卡为准；omni 的 small/nano 用的是 CC BY-NC 4.0，不能商用。用之前务必看清模型卡上的协议。
- voyage-4-nano：Voyage 的开源权重版（见 [Cohere 与 VoyageAI：OpenAI 之外的商业 API](./02-cohere-voyage.md)）。
- BGE-M3：dense、sparse、ColBERT 三合一，开源里目前只有它做得成熟，适合做混合检索。

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

短 query 检索长文档时，BGE 官方推荐 query 加这个 instruction 前缀，文档侧不加。v1.5 版本不加前缀时的效果已经比 v1 好很多，但检索场景加上通常更稳，可以在自己的 evalset 上对比一下。

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

`FlagEmbedding` 是 BGE 的官方库，用起来比 sentence-transformers 更贴合 BGE 的设计，比如 query 前缀会自动加上。

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

BGE-M3 支持 100 多种语言，能处理 8192 tokens 的长文本（BGE-large 只有 512），一次调用同时给出 dense、sparse 和多向量，混合检索用它很方便。做法见 [BM25 + Dense 融合：RRF / Weighted](../05-retrieval/02-bm25-fusion.md)。

---

## 4. Qwen3-Embedding（sentence-transformers 用法）

```python
# 需要 torch，首次运行会下载约 1.2GB 权重（以 0.6B 为例）
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")

queries = ["如何取消订阅"]
docs = ["停止自动续费的方法", "重置密码教程"]

# query 端用内置的 "query" prompt（带任务指令），文档端不加
q_vecs = model.encode(queries, prompt_name="query", normalize_embeddings=True)
d_vecs = model.encode(docs, normalize_embeddings=True)
print(q_vecs @ d_vecs.T)
```

Qwen3-Embedding 是指令感知模型：query 带指令，文档不带，思路和 BGE 的 query instruction 一样。它支持 Matryoshka 式的自定义输出维度（模型卡写的是 32–4096），截维后记得重新归一化。prompt 名和参数以[模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-8B)为准。

---

## 5. Nomic Embed（Matryoshka 友好）

```python
import numpy as np
from sentence_transformers import SentenceTransformer


model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)


# 必须加 prefix（nomic-embed-text-v1.5 以英文为主，中文场景选别的模型）
sentences = [
    "search_query: How do I cancel my subscription",
    "search_document: Turn off auto-renewal in account settings",
]

embeddings = model.encode(sentences, normalize_embeddings=True)
print(embeddings.shape)  # (2, 768)


# Matryoshka：截到 256 维
embeddings_256 = embeddings[:, :256]
embeddings_256 /= np.linalg.norm(embeddings_256, axis=1, keepdims=True)  # 重新归一化
```

Nomic 训练时用的 prefix 有这几种：

- `search_query:`
- `search_document:`
- `clustering:`
- `classification:`

---

## 6. jina-embeddings-v3（task-aware）

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

可用的 task：

- `retrieval.query` / `retrieval.passage`
- `separation`
- `classification`
- `text-matching`

模型会按 task 加载对应的 LoRA，同一个基座模型在不同任务上各用各的适配权重。

---

## 7. 自部署：性能

```
示意量级（吞吐随文本长度、序列截断长度和推理框架变化很大，以自己的压测为准）：

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

CPU 也能跑，只是慢得多，生产环境建议上 GPU。

---

## 8. 部署形态

### 8.1 Python 直接跑（开发 / 小流量）

```python
model = SentenceTransformer("BAAI/bge-large-zh-v1.5")

# 在 FastAPI / Celery 里直接 model.encode(...)
```

### 8.2 Text Embeddings Inference (TEI)

HuggingFace 官方的推理服务，专门为 embedding 做了优化：

```bash
docker run -p 8080:80 \
  --gpus all \
  -v $PWD/data:/data \
  ghcr.io/huggingface/text-embeddings-inference:1.8 \
  --model-id BAAI/bge-large-zh-v1.5
```

镜像 tag 按 GPU 架构区分（如 `turing-1.8`、`cpu-1.8`），版本以 TEI 的 GitHub Releases 为准；Qwen3-Embedding 需要 1.7.2 及以上。

调用：

```python
import httpx

resp = httpx.post("http://localhost:8080/embed", json={
    "inputs": ["如何取消订阅"],
    "normalize": True,
})
vec = resp.json()[0]
```

TEI 支持 dynamic batching，性能比直接用 sentence-transformers 好。

### 8.3 ONNX / TensorRT

把模型转成 ONNX：

```python
# pip install "sentence-transformers[onnx]"   # sentence-transformers ≥ 3.2
from sentence_transformers import SentenceTransformer


# backend="onnx"：第一次加载时自动导出 ONNX，之后 encode 走 onnxruntime
model = SentenceTransformer("BAAI/bge-large-zh-v1.5", backend="onnx")
model.save_pretrained("./bge-onnx")      # 导出的文件在 ./bge-onnx/onnx/model.onnx

vecs = model.encode(["如何取消订阅"], normalize_embeddings=True)
```

在 CPU 上，ONNX 通常比 PyTorch 快一截，幅度取决于 CPU 型号和文本长度。

### 8.4 INT8 量化

```python
from sentence_transformers import SentenceTransformer, export_dynamic_quantized_onnx_model

model = SentenceTransformer("BAAI/bge-large-zh-v1.5", backend="onnx")
# 按 CPU 指令集选配置："avx512_vnni" / "avx512" / "avx2" / "arm64"
export_dynamic_quantized_onnx_model(model, "avx512_vnni", "./bge-onnx")
# 生成 ./bge-onnx/onnx/model_qint8_avx512_vnni.onnx，加载时用
# SentenceTransformer("./bge-onnx", backend="onnx", model_kwargs={"file_name": "onnx/model_qint8_avx512_vnni.onnx"})
```

CPU 上还能再快一些，代价是少量召回损失；上线前用自己的 evalset 对比一下量化前后的 Recall。

---

## 9. 常用开源模型的中文表现

下表是示意数据：一个 200 条 query 的中文客服知识库上的 Recall@5，模型都是 2024 年的版本，不是可复现的基准，也没有包含 Qwen3-Embedding、千问 v4 这些新模型。放在这里只是想说明：中文场景下各家差距不大，最终要自己测。

| 模型 | Recall@5 |
|------|----------|
| BGE-large-zh-v1.5 | 91% |
| BGE-base-zh-v1.5 | 89% |
| BGE-m3 | 89% |
| multilingual-e5-large | 88% |
| jina-embeddings-v3 | 87% |
| text-embedding-3-small (OpenAI) | 88% |
| text-embedding-3-large (OpenAI) | 91% |

从这组数据看，纯中文场景下 BGE-large-zh 和 OpenAI 3-large 在同一档，几个多语言模型也只差两三个点；数据量大或有合规要求时，自部署 BGE 比调 OpenAI 划算。

---

## 10. 完整示例

运行这段需要 torch，第一次会下载模型权重。

```python
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

## 11. 选型小结

```
预算紧 + 中文为主 → Qwen3-Embedding（或 BGE-large-zh）
预算紧 + 多语言 → Qwen3-Embedding / BGE-m3
预算紧 + 英文 → BGE-large-en 或 nomic-embed
要 Matryoshka → nomic-embed / mxbai
要长文 → BGE-m3（8K）/ Qwen3-Embedding（32K）
要多任务 → jina-embeddings-v3（task LoRA）
要混合检索一站式 → BGE-m3（dense+sparse+colbert）
```

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| BGE-zh 检索时 query 没加 instruction 前缀 | 按官方建议加上，和不加的版本在 evalset 上对比 |
| 多语言用单语模型 | 跨语言检索基本不可用 |
| CPU 直接跑大模型 | 用 ONNX / TEI 优化 |
| 用 sentence-transformers 跑 BGE 但不归一化 | `normalize_embeddings=True` |

---

## 13. 下一步

- 本地跑模型的工具：[sentence-transformers 工具链](./04-sentence-transformers.md)
- 用 rerank 补强：[Rerank 模型：召回之后的精排](./05-rerank.md)
- 榜单怎么读：[MTEB 怎么看 + 选型决策树](./06-mteb-selection.md)
