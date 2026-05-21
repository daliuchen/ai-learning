# Embedding 怎么训出来的：Contrastive Learning 简史

> **一句话**：现代 embedding 模型几乎都靠 **contrastive learning（对比学习）** 训成的——给一对相似样本（正例）和一对不相似样本（负例），让模型学会"正例靠近、负例推远"。

---

## 1. 一句话原理

```
loss = -log( exp(sim(anchor, positive) / τ) / Σ exp(sim(anchor, negative_i) / τ) )
```

(InfoNCE loss 的简化版)

人话翻译：

- `anchor`：基准样本（一段文本）
- `positive`：跟 anchor 语义相似的（同义改写 / 翻译 / QA pair）
- `negative`：跟 anchor 不相关的
- 目标：让 anchor 跟 positive 的相似度尽可能高、跟所有 negative 尽可能低
- `τ`（temperature）：控制对比的"陡峭程度"，常用 0.05

---

## 2. 简史：从 word2vec 到现代

### 2013 — word2vec（Mikolov）

```
"The cat sat on the mat"

预测 anchor "cat" 的 context 词 ["the", "sat", "on", "mat"]
```

无监督，从大量文本里自学。开创了 dense embedding 范式。

### 2018 — BERT

不是 embedding 模型，但 BERT encoder 提供了"通用文本表示"。早期大家用 BERT 的 [CLS] 或 mean pooling 当 embedding，但**效果比不上专用 embedding**。

### 2019 — Sentence-BERT (SBERT)

> 论文：Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks

第一个真正把 BERT 适配成"句子级 embedding"的工作：

- siamese 结构（两个共享权重的 BERT）
- NLI 数据集做正例 / 负例
- 用 cosine loss 训

之后 `sentence-transformers` Python 库爆发。

### 2020 — SimCSE

> Simple Contrastive Learning of Sentence Embeddings

关键 trick：**用同一句话过两次 BERT（dropout 不同）→ 两个向量当正例**。无监督也能训出强 embedding。

### 2022-2023 — E5 / BGE / GTE

> Microsoft E5 / 北京智源 BGE / 阿里 GTE

大规模 + 多阶段：

1. **预训练**：海量文本 contrastive
2. **微调**：NLI / QA / web pair
3. **任务感知**：query 加 prefix "query:"，doc 加 "passage:"

MTEB 榜单 BGE / GTE 长期屠榜。

### 2024+ — Matryoshka / GritLM / Nomic-Embed

- **Matryoshka** (套娃)：训一个 768 维向量，前 64 / 256 / 512 维也可用——按需要截
- **GritLM**：一个模型同时做 embedding + 生成（大模型的"另一种用法"）
- **Nomic-Embed**：完全开源（权重 + 数据 + 训练代码）

---

## 3. 训练数据从哪来

**核心问题**：从哪找海量"语义相似"的句子对？

| 来源 | 例子 | 量级 |
|------|------|------|
| 翻译对 | (英→中、中→日) | 数亿 |
| 同义改写 | Quora duplicate / MS MARCO paraphrase | 数百万 |
| QA 对 | StackExchange / SQuAD / FAQ | 数百万 |
| Web 共现 | 同一页面里相邻段落 | 数十亿 |
| Wikipedia | section ↔ summary / hyperlink | 数千万 |
| SimCSE trick | 同句过两次 dropout | 跟你预训练语料一样大 |

负例：

- **In-batch negatives**：同 batch 里其他样本当负例（最常用）
- **Hard negatives**：跟 anchor 有点像但其实不对（用 BM25 / 老模型挖）
- **Random**：随便挑（弱信号）

---

## 4. encoder vs decoder

主流 embedding 模型几乎都是 **encoder-only**（BERT 类）：

```
Input:    [CLS] 这只 猫 真 可爱 [SEP]
           ↓ Transformer encoder
Output:   [CLS] 这只 猫 真 可爱 [SEP]  ← 取 [CLS] 或 mean pool 当向量
```

为啥不用 GPT 类 decoder？

- decoder 只看前文，前面的 token 看不到后面信息
- 对"理解整句话"不利

但 2024+ 也有 decoder-based 工作（如 GritLM），用特殊 prompt 引出 embedding。

---

## 5. instruction-tuned embedding

新趋势：**embedding 也"理解指令"**。

```python
# 不带 instruction（早期）
embed("猫的特征")
embed("猫的英文怎么说")
# 模型只看到字面差异，可能给很相似的向量

# 带 instruction（E5 / BGE / Nomic）
embed("query: 猫的特征")
embed("query: 猫的英文怎么说")
# 模型知道这两个 query 的"意图"不同
```

Cohere `embed-v3` / OpenAI `text-embedding-3` / Nomic-Embed 都内置了类似机制。**用时按官方推荐加 prefix**。

---

## 6. fine-tune 你自己的 embedding

什么时候要自己 finetune：

- 领域特别专（医学 / 法律 / 化学）
- 现成 model MTEB 分数低但你有自己的数据
- 多语言但小语种

工具：

```python
# sentence-transformers + 你的正例对
from sentence_transformers import SentenceTransformer, losses, InputExample
from torch.utils.data import DataLoader

model = SentenceTransformer("BAAI/bge-base-zh-v1.5")

train_examples = [
    InputExample(texts=["这本书很好看", "这本书写得不错"], label=1.0),
    InputExample(texts=["这本书很好看", "今天天气真好"], label=0.0),
    # ... 数千到数万对
]

train_dl = DataLoader(train_examples, shuffle=True, batch_size=16)
train_loss = losses.CosineSimilarityLoss(model)

model.fit(
    train_objectives=[(train_dl, train_loss)],
    epochs=3,
    warmup_steps=100,
    output_path="./my-finetuned-bge",
)
```

详见 [02-models/04-sentence-transformers.md](../02-models/04-sentence-transformers.md)。

**实战提醒**：finetune 前先做 baseline 评测（详见 [06-evaluation](../06-evaluation)）——很多时候用更好的现成模型 + 更好的 prompt 比 finetune 性价比高。

---

## 7. 评测：MTEB 榜单

> Massive Text Embedding Benchmark — https://huggingface.co/spaces/mteb/leaderboard

业界默认基准。在 56+ 任务上算平均分：

- Classification（情感 / 主题）
- Clustering
- Retrieval
- Reranking
- STS（句对相似度）
- Summarization

详见 [02-models/06-mteb-selection.md](../02-models/06-mteb-selection.md)。

⚠️ **不要光看 MTEB 总分**：

- 你的任务是 retrieval → 看 Retrieval 分
- 你的数据是中文 → 看 C-MTEB
- 你要小模型 → 按大小过滤

---

## 8. demo：复现"猫 ≈ kitten"

```python
# demos/foundations/03_training_demo.py
"""演示对比学习的核心 idea（不真的训练，只展示 loss 怎么算）"""
import numpy as np


def simulate_loss(anchor_vec, positive_vec, negative_vecs, temperature=0.05):
    """InfoNCE loss 简化版"""
    def sim(a, b):
        return np.dot(a, b)  # 假设已归一化

    pos_sim = sim(anchor_vec, positive_vec) / temperature
    neg_sims = [sim(anchor_vec, n) / temperature for n in negative_vecs]

    # InfoNCE: -log(exp(pos) / (exp(pos) + Σ exp(neg)))
    all_sims = np.array([pos_sim, *neg_sims])
    log_sum_exp = np.log(np.exp(all_sims).sum())
    loss = -(pos_sim - log_sum_exp)
    return loss


# 假设有现成 embedding
np.random.seed(42)
cat = np.array([0.8, 0.5, -0.1])
kitten = np.array([0.78, 0.52, -0.09])  # 接近 cat
linux = np.array([-0.5, 0.3, 0.8])     # 远离 cat
table = np.array([0.1, -0.7, 0.6])     # 远离 cat

# 归一化
for v in [cat, kitten, linux, table]:
    v /= np.linalg.norm(v)

loss = simulate_loss(cat, kitten, [linux, table])
print(f"Loss = {loss:.4f}")  # 应该比较小（正例近、负例远 → 好）

# 反过来，假装 cat 跟 linux 是正例（语义错误）
loss_wrong = simulate_loss(cat, linux, [kitten, table])
print(f"Loss (wrong pair) = {loss_wrong:.4f}")  # 应该很大
```

---

## 9. 为啥同一模型不同语言效果差距大

```
英文：text-embedding-3-small MTEB-EN avg ~62
中文：text-embedding-3-small C-MTEB ~50
小语种：经常 < 40
```

原因：

- 训练数据**英文占大头**
- 小语种翻译对少
- 文化 / 实体覆盖不均

**实战**：

- 中文场景考虑 BGE-zh / GTE-zh（中文特训）
- 跨语言场景考虑 multilingual-e5 / Cohere embed-multilingual

详见 [05-multilingual.md](./05-multilingual.md)。

---

## 10. 下一步

- 📖 选 dimension → [04-dimension-tradeoff.md](./04-dimension-tradeoff.md)
- 📖 多语言 embedding → [05-multilingual.md](./05-multilingual.md)
- 📖 sentence-transformers fine-tune 实操 → [02-models/04-sentence-transformers.md](../02-models/04-sentence-transformers.md)
