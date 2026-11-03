# Embedding 如何训练：Contrastive Learning 简史

> 现在的 embedding 模型几乎都是用对比学习（contrastive learning）训出来的：给模型一对语义相似的样本（正例）和一些不相关的样本（负例），让它学会把正例拉近、把负例推远。理解了这一点，后面选模型、判断要不要微调都会有依据。

---

## 1. 原理

```
loss = -log( exp(sim(anchor, positive) / τ)
             / [ exp(sim(anchor, positive) / τ) + Σ exp(sim(anchor, negative_i) / τ) ] )
```

(InfoNCE loss，分母包含正例本身和所有负例)

公式里各项的含义：

- `anchor`：基准样本（一段文本）
- `positive`：和 anchor 语义相似的样本（同义改写、翻译、QA pair）
- `negative`：和 anchor 不相关的样本
- `τ`（temperature）：控制对比的陡峭程度，常用 0.05

训练的目标是让 anchor 和 positive 的相似度尽量高，和所有 negative 的相似度尽量低。

---

## 2. 简史：从 word2vec 到现代

### 2013 — word2vec（Mikolov）

```
"The cat sat on the mat"

预测 anchor "cat" 的 context 词 ["the", "sat", "on", "mat"]
```

无监督，直接从大量文本里学。dense embedding 这条路就是从它开始的。

### 2018 — BERT

BERT 本身不是 embedding 模型，但它的 encoder 提供了通用的文本表示。早期大家拿 BERT 的 [CLS] 或 mean pooling 当 embedding 用，效果比不上专门训练的 embedding 模型。

### 2019 — Sentence-BERT (SBERT)

> 论文：Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks

这是第一个把 BERT 改造成句子级 embedding 的工作：

- siamese 结构（两个共享权重的 BERT）
- 先在 NLI 数据上用句对分类目标训练
- 再在 STS 数据上用 cosine 相似度回归目标微调

`sentence-transformers` 这个 Python 库也是从这里火起来的。

### 2020 — SimCSE

> Simple Contrastive Learning of Sentence Embeddings

它的巧思是把同一句话过两次 BERT，因为 dropout 不同，会得到两个略有差别的向量，把这两个向量当正例。这样不需要标注数据也能训出很强的 embedding。

### 2022-2023 — E5 / BGE / GTE

> Microsoft E5 / 北京智源 BGE / 阿里 GTE

这一代的做法是加大规模、分阶段训练：

1. 预训练：在海量文本上做 contrastive
2. 微调：用 NLI、QA、网页文本对
3. 任务感知：区分 query 和文档，比如 E5 在 query 前加 "query: "、doc 前加 "passage: "，BGE 给 query 加一句检索指令

BGE 和 GTE 在 MTEB 榜单上长期排在前面。

### 2024+ — Matryoshka / GritLM / Nomic-Embed

- Matryoshka（套娃）：训一个 768 维向量，它的前 64 / 256 / 512 维单独拿出来也能用，按需截取
- GritLM：一个模型同时做 embedding 和生成
- Nomic-Embed：权重、数据、训练代码全部开源

---

## 3. 训练数据从哪来

训练的难点在于去哪找海量语义相似的句子对。常见来源：

| 来源 | 例子 | 量级 |
|------|------|------|
| 翻译对 | (英→中、中→日) | 数亿 |
| 同义改写 | Quora duplicate / MS MARCO paraphrase | 数百万 |
| QA 对 | StackExchange / SQuAD / FAQ | 数百万 |
| Web 共现 | 同一页面里相邻段落 | 数十亿 |
| Wikipedia | section ↔ summary / hyperlink | 数千万 |
| SimCSE 的做法 | 同句过两次 dropout | 和预训练语料一样大 |

负例有三种取法：

- In-batch negatives：把同一个 batch 里的其他样本当负例，最常用
- Hard negatives：和 anchor 有点像、其实不对的样本，一般用 BM25 或旧模型挖出来
- Random：随便挑，信号比较弱

---

## 4. encoder vs decoder

embedding 模型现在有两条主流路线。一条是 encoder-only（BERT 一类），BGE、E5、GTE 都属于这一类：

```
Input:    [CLS] 这只 猫 真 可爱 [SEP]
           ↓ Transformer encoder
Output:   [CLS] 这只 猫 真 可爱 [SEP]  ← 取 [CLS] 或 mean pool 当向量
```

另一条是在 decoder 大模型上训练出来的，比如 Qwen3-Embedding、GritLM。decoder 只能看前文，前面的 token 拿不到后面的信息，所以这类模型一般取最后一个 token 的隐状态当向量（只有它看过整句话），再配合指令 prompt 把 embedding 引出来。它们参数量大，多语言和长文本表现好，在 MTEB 榜单上排得靠前，代价是推理更贵。

---

## 5. instruction-tuned embedding

另一个趋势是让 embedding 模型也能理解指令：

```python
# 不带 instruction（早期）：同一段文本永远是同一个向量，
# 不管你拿它做检索、做聚类还是做分类

# 带 query / passage 前缀（E5、Nomic 一类）：区分"提问的一方"和"被检索的文档"
embed("query: 猫有哪些特征")
embed("passage: 猫是小型食肉哺乳动物，……")

# 带任务指令（Qwen3-Embedding 一类）：告诉模型这个向量拿来做什么
embed("Instruct: 给定一个问题，检索能回答它的段落\nQuery: 猫有哪些特征")
embed("Instruct: 判断两句话是否表达同一个意思\nQuery: 猫有哪些特征")
# 同一句话，任务不同，得到的向量也不同
```

Cohere embed 系列（`input_type` 参数）、Nomic-Embed、Qwen3-Embedding、BGE 都有类似机制。OpenAI `text-embedding-3` 没有指令或前缀机制，query 和文档直接原样传。实际使用时，按各家官方文档的要求加 prefix。

---

## 6. fine-tune 你自己的 embedding

以下几种情况值得考虑自己微调：

- 领域很专（医学、法律、化学）
- 现成模型在你自己的 evalset 上表现不好，而你手里有领域数据
- 要支持小语种

工具用 sentence-transformers 就够了：

```python
# sentence-transformers 3.0 起的 Trainer 写法
from datasets import Dataset
from sentence_transformers import (
    SentenceTransformer, SentenceTransformerTrainer, SentenceTransformerTrainingArguments, losses,
)

model = SentenceTransformer("BAAI/bge-base-zh-v1.5")

# 正例对就够了：MultipleNegativesRankingLoss 会把同一个 batch 里的其他样本当负例
train_ds = Dataset.from_dict({
    "anchor":   ["这本书很好看", "怎么改密码"],
    "positive": ["这本书写得不错", "重置密码教程"],
    # ... 数千到数万对
})

args = SentenceTransformerTrainingArguments(
    output_dir="./my-finetuned-bge",
    num_train_epochs=3,
    per_device_train_batch_size=32,
    warmup_ratio=0.1,
)
trainer = SentenceTransformerTrainer(
    model=model, args=args, train_dataset=train_ds,
    loss=losses.MultipleNegativesRankingLoss(model),
)
trainer.train()
```

完整流程见[sentence-transformers 工具链](../02-models/04-sentence-transformers.md)。

微调之前先做一次 baseline 评测（方法见[评测](../06-evaluation)一章）。很多时候换一个更好的现成模型、再调一调 prompt，性价比比微调高。

---

## 7. 评测：MTEB 榜单

> Massive Text Embedding Benchmark — https://huggingface.co/spaces/mteb/leaderboard

这是业界默认的基准。最初的 MTEB 有 56 个数据集，MTEB v2 / MMTEB 已经扩展到上百个任务、覆盖几百种语言，榜单按任务算平均分，任务类型包括：

- Classification（情感 / 主题）
- Clustering
- Retrieval
- Reranking
- STS（句对相似度）
- Summarization

怎么读这个榜见[MTEB 怎么看 + 选型决策树](../02-models/06-mteb-selection.md)。

**不要只看 MTEB 总分。** 做检索就看 Retrieval 分，数据是中文就看 C-MTEB，要小模型就先按参数量过滤。

---

## 8. demo：复现"猫 ≈ kitten"

```python
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

## 9. 为什么同一模型不同语言效果差距大

```
英文：text-embedding-3-small 官方给的 MTEB（v1 英文榜）平均分 62.3
中文：同一个模型在中文任务上明显偏弱，不如专门用中文数据训过的模型
小语种：通常更弱，训练数据越少的语言掉得越多
```

原因有几方面：训练数据里英文占大头，小语种的翻译对少，文化和实体的覆盖也不均匀。

所以中文场景可以考虑 BGE-zh / GTE-zh 这类专门用中文训过的模型；跨语言场景考虑 Qwen3-Embedding、bge-m3、multilingual-e5、Cohere embed-v5.0 或千问 text-embedding-v4。展开讨论见[多语言 Embedding 的工作原理](./05-multilingual.md)。

---

## 10. 下一步

- 维度怎么选：[维度、精度与性能的权衡](./04-dimension-tradeoff.md)
- 多语言 embedding：[多语言 Embedding 的工作原理](./05-multilingual.md)
- 用 sentence-transformers 微调：[sentence-transformers 工具链](../02-models/04-sentence-transformers.md)
