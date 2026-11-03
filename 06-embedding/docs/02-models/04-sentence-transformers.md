# sentence-transformers 工具链

> 在 Python 里用开源 embedding 模型，绕不开 `sentence-transformers`：加载模型、encode、fine-tune、评测都靠它。这篇按实际用到的顺序过一遍，从安装、编码一直到导出 ONNX 和接 TEI。

---

## 1. 安装

```bash
pip install sentence-transformers
```

它依赖 PyTorch 和 transformers。要用 GPU，先按 PyTorch 官网给的命令装对应 CUDA 版本的 torch（形如 `pip install torch --index-url https://download.pytorch.org/whl/cu128`），再装 sentence-transformers。

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

几个参数要留意：

- `normalize_embeddings=True`：建议总是打开，归一化后 cosine 和 dot product 等价
- `batch_size`：一般 32-128，CPU 用 16-32，GPU 用 64-256
- `convert_to_tensor`：要写进向量库就用 False；结果直接交给 reranker 之类的 torch 代码时用 True

---

## 4. 算相似度

```python
from sentence_transformers import util


query = "如何取消订阅"
docs = ["停止自动续费", "登录方法", "退款流程"]


query_vec = model.encode(query, normalize_embeddings=True)
doc_vecs = model.encode(docs, normalize_embeddings=True)

scores = util.cos_sim(query_vec, doc_vecs)
print(scores)  # 形如 tensor([[0.85, 0.32, 0.41]])，数值示意


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

100 万条文档在单张 GPU 上从几分钟到几十分钟不等，主要看文本长度和 batch size。

---

## 6. fine-tune

### 6.1 准备数据

从 v3 开始，训练改用 `datasets.Dataset` 加 `SentenceTransformerTrainer`。以前的 `InputExample + DataLoader + model.fit()` 在官方文档里归到了 Deprecated Training，新代码别再用了。要注意数据集的**列顺序必须和损失函数对上**（列名无所谓，只有叫 `label` 或 `score` 的列会被当成标签）：

```python
from datasets import Dataset

# MultipleNegativesRankingLoss：(anchor, positive) 两列即可，负例自动取 batch 内其它样本
train_ds = Dataset.from_dict({
    "anchor":   ["如何取消订阅", "怎么改密码"],
    "positive": ["停止自动续费的方法", "重置密码教程"],
    # 数千到数万对
})
```

### 6.2 训练

```python
# 需要 torch；sentence-transformers 3.0 起的 Trainer 接口
from sentence_transformers import (
    SentenceTransformer,
    SentenceTransformerTrainer,
    SentenceTransformerTrainingArguments,
    losses,
)

model = SentenceTransformer("BAAI/bge-base-zh-v1.5")
loss = losses.MultipleNegativesRankingLoss(model)

args = SentenceTransformerTrainingArguments(
    output_dir="./my-bge-finetuned",
    num_train_epochs=3,
    per_device_train_batch_size=16,   # MNRL：batch 越大负例越多、效果通常越好
    warmup_ratio=0.1,
)

trainer = SentenceTransformerTrainer(model=model, args=args, train_dataset=train_ds, loss=loss)
trainer.train()
model.save_pretrained("./my-bge-finetuned/final")
```

几万对数据在单卡上通常几十分钟到几小时就能训完。

### 6.3 各种 Loss

| Loss | 用于 | 数据列 |
|------|------|--------|
| `CosineSimilarityLoss` | 给定相似度分数（0-1） | sentence1, sentence2, score |
| `MultipleNegativesRankingLoss` | 只有正例对，in-batch negative | anchor, positive |
| `TripletLoss` | (anchor, positive, negative) 三元组 | anchor, positive, negative |
| `ContrastiveLoss` | 二分类正负例 | sentence1, sentence2, label |

最常用的是 MultipleNegativesRankingLoss：只要准备正例对，负例从同一个 batch 里自动取。显存不够时，可以换成 `CachedMultipleNegativesRankingLoss` 放大有效 batch。训练 API 的迁移细节看官方 [Training Overview](https://sbert.net/docs/sentence_transformer/training_overview.html) 和迁移指南，比如 transformers 4.46 起 Trainer 的 `tokenizer` 参数改名成了 `processing_class`。

---

## 7. 评测：跟 MTEB 配合

```python
# 装 mteb 库
# pip install mteb

import mteb


tasks = mteb.get_tasks(
    languages=["zho"],           # 语言用 ISO 639-3 三字母码；也可按名字选任务
    task_types=["Retrieval"],
)

# mteb 2.x：用 mteb.get_model 载入模型（没有内置实现时回退到 SentenceTransformer）
model = mteb.get_model("BAAI/bge-base-zh-v1.5")

# 旧的 mteb.MTEB(tasks=...).run(model) 已被 mteb.evaluate 取代
results = mteb.evaluate(model, tasks=tasks)

# 自己 finetune 后再跑
ft_model = mteb.get_model("./my-bge-finetuned/final")
ft_results = mteb.evaluate(ft_model, tasks=tasks)
```

运行这段时 mteb 会下载体积不小的评测数据集和模型；写法按 mteb 2.x 的官方 README。

把 fine-tune 前后的结果放在一起比，确认模型真的变好了，而不是只是过拟合了你自己的数据。

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

上面是手动用 optimum 导出的写法，要自己处理 pooling 和归一化。sentence-transformers 3.2 起可以直接 `SentenceTransformer(model_id, backend="onnx")`，pooling 照旧由它处理，写法见[开源模型：Qwen3-Embedding / BGE / Jina / Nomic](./03-open-source.md)第 8 节。和纯 PyTorch 比，ONNX 在 CPU 上通常有明显提速，GPU 上收益小一些，具体幅度以自己的压测为准。

---

## 9. 跟 TEI 配合

`text-embeddings-inference`（HuggingFace 官方推理服务）可以直接加载 sentence-transformers 模型：

```bash
docker run -p 8080:80 --gpus all \
  -v ~/.cache/huggingface:/data \
  ghcr.io/huggingface/text-embeddings-inference:1.8 \
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

生产环境我推荐 TEI，它有 dynamic batching，能把 GPU 跑满。

---

## 10. 跟 LangChain / LlamaIndex 集成

```python
# LangChain（pip install langchain-huggingface）
from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-large-zh-v1.5",
    encode_kwargs={"normalize_embeddings": True},
)


# LlamaIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-large-zh-v1.5")
```

两边底层都是直接加载 sentence-transformers 模型。

---

## 11. 提速技巧

```python
# 1. 半精度（fp16）
model.half().to("cuda")
# 显存约减半、速度更快，召回基本不变

# 2. CPU 上换 ONNX / OpenVINO 后端（sentence-transformers ≥ 3.2）
model = SentenceTransformer("BAAI/bge-base-zh-v1.5", backend="onnx")

# 3. 大 batch
model.encode(texts, batch_size=256)
# GPU 利用率上来

# 4. 截断长度
model.max_seq_length = 256   # encode() 本身没有这个参数，要设在模型上
model.encode(texts)
# 短点更快，但太短丢信息
```

---

## 12. 完整示例

```python
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

- 用 rerank 做精排：[Rerank 模型：召回之后的精排](./05-rerank.md)
- 榜单怎么读：[MTEB 怎么看 + 选型决策树](./06-mteb-selection.md)
- 部署到生产：[部署形态选型](../07-production/04-deployment.md)
