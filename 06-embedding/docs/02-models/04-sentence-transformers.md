# sentence-transformers 工具链

> **一句话**：`sentence-transformers` 是 Python embedding 生态的瑞士军刀——加载几乎所有开源 embedding 模型、做 encode / fine-tune / 评测都靠它。

---

## 1. 装一下

```bash
pip install sentence-transformers
```

依赖 PyTorch / transformers。需要 GPU 加 `--index-url https://download.pytorch.org/whl/cu121`。

---

## 2. 加载模型

```python
from sentence_transformers import SentenceTransformer


model = SentenceTransformer("BAAI/bge-base-zh-v1.5")
# 从 HuggingFace Hub 自动下载，cache 到 ~/.cache/huggingface


# 离线 / 自部署
model = SentenceTransformer("/path/to/local/model")


# 指定 device
model = SentenceTransformer("...", device="cuda:0")
model = SentenceTransformer("...", device="cpu")
```

---

## 3. encode

```python
sentences = ["第一句", "第二句", "第三句"]

embeddings = model.encode(
    sentences,
    batch_size=32,
    show_progress_bar=True,
    normalize_embeddings=True,
    convert_to_tensor=False,    # True 返回 torch.Tensor
)

print(embeddings.shape)  # (3, 768) np.ndarray
```

**关键参数**：

- `normalize_embeddings=True`：必加（让 cosine = dot product）
- `batch_size`：32-128（CPU 16-32，GPU 64-256）
- `convert_to_tensor`：写库时 False，做 reranker 直接用时 True

---

## 4. 跑相似度

```python
from sentence_transformers import util


query = "如何取消订阅"
docs = ["停止自动续费", "登录方法", "退款流程"]


query_vec = model.encode(query, normalize_embeddings=True)
doc_vecs = model.encode(docs, normalize_embeddings=True)

scores = util.cos_sim(query_vec, doc_vecs)
print(scores)  # tensor([[0.85, 0.32, 0.41]])


# 找 top-k
hits = util.semantic_search(
    query_embeddings=query_vec,
    corpus_embeddings=doc_vecs,
    top_k=3,
)
print(hits)  # [[{'corpus_id': 0, 'score': 0.85}, ...]]
```

---

## 5. 离线 batch 处理

```python
import numpy as np


def embed_batch_to_disk(texts, out_path, batch_size=128):
    """大量文档 embed 落盘"""
    all_vecs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        vecs = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        all_vecs.append(vecs)
    final = np.concatenate(all_vecs)
    np.save(out_path, final)
    return final


embed_batch_to_disk(my_million_docs, "embeddings.npy")
```

100 万文档 GPU 上几十分钟。

---

## 6. fine-tune

### 6.1 准备数据

```python
from sentence_transformers import InputExample


train_examples = [
    InputExample(texts=["如何取消订阅", "停止自动续费的方法"], label=1.0),
    InputExample(texts=["如何取消订阅", "今天天气如何"], label=0.0),
    # 数千到数万对
]
```

### 6.2 训

```python
from sentence_transformers import losses
from torch.utils.data import DataLoader


train_dl = DataLoader(train_examples, batch_size=16, shuffle=True)
train_loss = losses.CosineSimilarityLoss(model)


model.fit(
    train_objectives=[(train_dl, train_loss)],
    epochs=3,
    warmup_steps=100,
    output_path="./my-bge-finetuned",
    show_progress_bar=True,
)
```

几小时后得到一个 fine-tuned 模型。

### 6.3 各种 Loss

| Loss | 用于 |
|------|------|
| `CosineSimilarityLoss` | 给定相似度分数（0-1） |
| `MultipleNegativesRankingLoss` | 只有正例对，in-batch negative |
| `TripletLoss` | (anchor, positive, negative) 三元组 |
| `ContrastiveLoss` | 二分类正负例 |

最实用：**MultipleNegativesRankingLoss**——只需要正例对，负例自动从 batch 里取。

```python
train_examples = [
    InputExample(texts=["query1", "positive_doc_1"]),
    InputExample(texts=["query2", "positive_doc_2"]),
    # ...
]

train_loss = losses.MultipleNegativesRankingLoss(model)
```

---

## 7. 评测：跟 MTEB 配合

```python
# 装 mteb 库
# pip install mteb

import mteb


tasks = mteb.get_tasks(
    languages=["zho_Hans"],
    task_types=["Retrieval"],
)


# 在 BGE 上跑
evaluation = mteb.MTEB(tasks=tasks)
results = evaluation.run(model, output_folder="./mteb_results/bge")


# 自己 finetune 后再跑
ft_model = SentenceTransformer("./my-bge-finetuned")
ft_results = evaluation.run(ft_model, output_folder="./mteb_results/bge-ft")
```

对比看你的 fine-tune 有没有真的提升（防过拟合自己数据）。

---

## 8. 模型导出

### 8.1 保存

```python
model.save("./my-model")
# 含 config.json / tokenizer / model.safetensors
```

### 8.2 转 ONNX

```python
from sentence_transformers import SentenceTransformer
from optimum.onnxruntime import ORTModelForFeatureExtraction
from transformers import AutoTokenizer


# 转
model_id = "BAAI/bge-large-zh-v1.5"
ort_model = ORTModelForFeatureExtraction.from_pretrained(model_id, export=True)
ort_model.save_pretrained("./bge-onnx")

tokenizer = AutoTokenizer.from_pretrained(model_id)
tokenizer.save_pretrained("./bge-onnx")


# 用
from optimum.onnxruntime import ORTModelForFeatureExtraction
import torch


ort_model = ORTModelForFeatureExtraction.from_pretrained("./bge-onnx")
tokenizer = AutoTokenizer.from_pretrained("./bge-onnx")


def embed_onnx(texts):
    inputs = tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=512)
    with torch.no_grad():
        outputs = ort_model(**inputs)
    # 用 [CLS] 或 mean pool
    embeddings = outputs.last_hidden_state[:, 0]
    embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
    return embeddings.numpy()
```

ONNX 比纯 PyTorch 快 2-4 倍 CPU、20% GPU。

---

## 9. 跟 TEI 配合

`text-embeddings-inference`（HuggingFace 官方推理服务）能直接加载 sentence-transformers 模型：

```bash
docker run -p 8080:80 --gpus all \
  -v ~/.cache/huggingface:/data \
  ghcr.io/huggingface/text-embeddings-inference:1.2 \
  --model-id BAAI/bge-large-zh-v1.5 \
  --max-batch-tokens 32768
```

调用：

```python
import httpx

resp = httpx.post("http://localhost:8080/embed", json={
    "inputs": ["你好"],
    "normalize": True,
}, timeout=30)
vec = resp.json()[0]
```

生产部署推荐 TEI（dynamic batching、GPU 跑满）。

---

## 10. 跟 LangChain / LlamaIndex 集成

```python
# LangChain
from langchain_community.embeddings import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-large-zh-v1.5",
    encode_kwargs={"normalize_embeddings": True},
)


# LlamaIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-large-zh-v1.5")
```

两边都直接复用 sentence-transformers 模型。

---

## 11. 性能 tips

```python
# 1. 半精度（fp16）
model.half().to("cuda")
# 内存 / 时间 × 0.5，召回基本不变

# 2. torch.compile（PyTorch 2.0+）
import torch
model = torch.compile(model)
# 提速 20-50%

# 3. 大 batch
model.encode(texts, batch_size=256)
# GPU 利用率上来

# 4. truncation
model.encode(texts, max_seq_length=256)
# 短点更快，但太短丢信息
```

---

## 12. 完整 demo

```python
# demos/models/04_sentence_transformers.py
import numpy as np
from sentence_transformers import SentenceTransformer, util


model = SentenceTransformer("BAAI/bge-base-zh-v1.5", device="cpu")  # 也可 cuda


corpus = [
    "如何关闭自动续费",
    "停止订阅的方法",
    "如何登录账号",
    "重置密码教程",
    "退款流程",
]


corpus_emb = model.encode(corpus, normalize_embeddings=True, show_progress_bar=False)


query = "为这个句子生成表示以用于检索相关文章：如何取消订阅"
query_emb = model.encode(query, normalize_embeddings=True)


hits = util.semantic_search(query_emb, corpus_emb, top_k=3)
for h in hits[0]:
    print(f"  {corpus[h['corpus_id']]:<20} score={h['score']:.4f}")
```

---

## 13. 下一步

- 📖 rerank 模型 → [05-rerank.md](./05-rerank.md)
- 📖 MTEB 怎么看 → [06-mteb-selection.md](./06-mteb-selection.md)
- 📖 部署生产 → [07-production/04-deployment.md](../07-production/04-deployment.md)
