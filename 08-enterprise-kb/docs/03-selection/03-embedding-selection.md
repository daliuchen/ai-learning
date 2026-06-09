# EKB 13：Embedding 选型——中文知识库不要无脑用 OpenAI

> **一句话**：中文为主的知识库，embedding 默认选 OpenAI 不一定最优。中文开源模型（BGE 系）在中文检索上常常更好、还能本地部署省钱保密。但「哪个最好」要靠你自己的评估集实测——所以本项目把 embedding 做成**可切换**的一层。

---

## 1. 为什么 embedding 选型这么关键

embedding 决定了「语义相似」的判断标准。它选差了，**后面 rerank 和 prompt 再优化也救不回来**——因为该召回的根本没召回。这是 RAG 的地基（详见 [06 手册 Embedding 选型](/docs/06-embedding/02-models/01-how-to-choose)）。

而中文场景有个坑：很多 embedding 模型是英文语料为主训练的，中文表现打折。

---

## 2. 候选模型

| 模型 | 类型 | 中文表现 | 部署 | 成本 |
|------|------|----------|------|------|
| **BGE-large-zh / bge-m3** | 开源（智源） | 中文强 | 本地/自托管 | 免费（自己出算力） |
| OpenAI text-embedding-3 | 商用 API | 中文尚可 | 调 API | 按量付费 |
| 通义/百川等国产商用 | 商用 API | 中文强 | 调 API | 按量付费 |
| multilingual-e5 | 开源 | 多语言均衡 | 本地 | 免费 |

经验：**纯中文知识库，BGE 系常常优于 OpenAI**；但如果文档里中英混杂（技术文档常见），要实测。

---

## 3. 选型的三个维度

别只看「哪个准」，三个维度一起权衡：

### 3.1 检索质量（最重要）

在**你自己的评估集**上测 recall@k。不要信通用榜单——你的文档领域、问法和榜单数据分布不同。这正是第 04 章「评估先行」的用途之一：评估集除了打磨检索，也用来选 embedding。

### 3.2 部署与保密

企业文档常含敏感信息。开源模型**本地部署**意味着文档不出公司网络，这对很多企业是硬要求。API 模型则要考虑数据合规。

### 3.3 成本

API 模型按 token 收费，ingest 大量文档时一次性成本不小，且每次 query 也要 embed。本地模型前期有算力投入，但长期边际成本近零。

---

## 4. 把 embedding 做成可切换的一层

因为「哪个最好」要实测，我们**不写死**，而是抽象成统一接口：

```python
# generate/embedder.py —— 统一接口，实现可换
from typing import Protocol

class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...

# 实现一：本地 BGE
class BGEEmbedder:
    dim = 1024
    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer("BAAI/bge-large-zh-v1.5")
    def embed(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()

# 实现二：OpenAI
class OpenAIEmbedder:
    dim = 1536
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI()
    def embed(self, texts):
        r = self.client.embeddings.create(
            model="text-embedding-3-small", input=texts)
        return [d.embedding for d in r.data]

# 切换只改一行
embedder: Embedder = BGEEmbedder()
```

注意 `dim` 不同（BGE 1024 / OpenAI 1536）——换模型要**重建 embedding 列和重新 ingest**。所以最好在正式 ingest 大量文档**之前**就用评估集选定。

---

## 5. 一个实战注意点：query 和 doc 用同一个模型

检索时，**查询和文档必须用同一个 embedding 模型**编码，否则向量空间对不上，检索全乱。这听起来理所当然，但换模型时容易只重 ingest 文档、忘了 query 侧也要换。把模型选择收敛到一处（上面的 `embedder`），就不会出这个错。

有些模型还区分「查询前缀」和「文档前缀」（如 bge 建议 query 加 `"为这个句子生成表示："`），用之前看模型卡说明。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 中文知识库无脑用 OpenAI | 中文召回可能不如 BGE | 用评估集实测再定 |
| 信通用榜单选模型 | 你的领域未必一致 | 在自己评估集上测 |
| embedding 写死 | 想换要大改 | 抽象成可切换接口 |
| query 和 doc 用不同模型 | 向量空间错位，检索乱 | 收敛到一处 |
| 选定前就 ingest 全部文档 | 换模型要全部重来 | 先小样本选型再全量 |

---

## 下一步

数据层和向量层定了，生成层选什么框架——为什么是 Pydantic AI：

→ [04-framework-pydantic-ai](./04-framework-pydantic-ai.md)
