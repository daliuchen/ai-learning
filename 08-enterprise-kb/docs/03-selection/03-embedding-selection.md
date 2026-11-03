# EKB 13：中文知识库的 Embedding 选型

> 中文为主的知识库，embedding 不要默认选最出名的那个。商用 API（OpenAI、百炼等）和开源模型（BGE 系等）各有取舍：开源模型能本地部署，文档不出内网；商用 API 省去运维。至于哪个效果最好，得在你自己的评估集上测，所以本项目把 embedding 做成了可切换的一层。
>
> embedding 模型更新很快，本篇只列有官方文档出处的参数，不评判谁最强。本手册的 demo 用的是阿里云百炼的 `text-embedding-v4`。

---

## 1. 为什么 embedding 选型这么重要

embedding 决定了系统怎么判断两段文字语义相近。它选得不好，该召回的内容根本没召回，后面 rerank 和 prompt 再怎么优化也补不回来。它是 RAG 的地基（详见 06 手册的 [MTEB 怎么看 + 选型决策树](/docs/06-embedding/02-models/06-mteb-selection)）。

中文场景还有一个坑：不少 embedding 模型以英文语料为主训练，中文效果会打折扣。

---

## 2. 候选模型

| 模型 | 类型 | 维度 / 长度（官方文档） | 部署 | 成本 |
|------|------|------------------------|------|------|
| 百炼 `text-embedding-v4`（本手册 demo） | 商用 API | 默认 1024，可选 2048/1536/1024/768/512/256/128/64；单条 ≤ 8192 token；100+ 语言；单次请求最多 10 条 | 调 API | 北京地域 0.0005 元/千 token（约 0.5 元/百万 token，百炼帮助中心 2026-11 所见；批量调用另有折扣） |
| 百炼 `text-embedding-v3` | 商用 API | 默认 1024，可选到 64；单条 ≤ 8192 token；50+ 语言 | 调 API | 以百炼定价页为准 |
| OpenAI `text-embedding-3-small` / `-large` | 商用 API | 默认 1536 / 3072，都支持 `dimensions` 参数缩短；单条 ≤ 8192 token | 调 API | 以 OpenAI 定价页为准 |
| BGE 系（`bge-m3` / `bge-large-zh` 等，智源） | 开源 | 1024 | 本地/自托管 | 免费（自己出算力） |
| Qwen3-Embedding（0.6B / 4B / 8B，阿里开源） | 开源（基于 LLM 的 decoder 路线） | 因规模而异，支持自定义输出维度 | 本地/自托管 | 免费（自己出算力） |
| multilingual-e5 | 开源 | 因型号而异 | 本地 | 免费 |

怎么选：别照抄榜单第一名。中文为主的知识库，建议把百炼 `text-embedding-v4`、开源模型（BGE 系、Qwen3-Embedding）、OpenAI 3 系这几类各放进评估集跑一遍 recall@k；技术文档里常见中英混杂，更需要实测。价格只作量级参考，上线前以官方定价页为准。

---

## 3. 选型的三个维度

只看准不准是不够的，下面三个维度要一起权衡。

### 3.1 检索质量（最重要）

在你自己的评估集上测 recall@k。通用榜单的参考价值有限，你的文档领域和员工的问法，和榜单数据的分布并不一样。第 04 章的评估集除了用来打磨检索，也正好用来选 embedding。

### 3.2 部署与保密

企业文档常含敏感信息。开源模型可以本地部署，文档不离开公司网络，这对很多企业是硬性要求。用 API 模型则要考虑数据合规。

### 3.3 成本

API 模型按 token 收费，大批量 ingest 时一次性成本不低，每次查询也要做一次 embed。本地模型前期要投入算力，但长期看边际成本接近零。

---

## 4. 把 embedding 做成可切换的一层

既然哪个最好要靠实测，我们就不把模型写死，而是抽象成统一接口：

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

# 实现二：OpenAI 兼容端点（OpenAI 本身，或百炼 DashScope 的 OpenAI 兼容模式）
class OpenAICompatEmbedder:
    def __init__(self, model="text-embedding-v4", dim=1024, batch=10,
                 base_url=None, api_key=None):
        from openai import OpenAI
        # 百炼：base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.model, self.dim, self.batch = model, dim, batch
    def embed(self, texts):
        out = []
        for i in range(0, len(texts), self.batch):      # 百炼单次最多 10 条，必须分批
            r = self.client.embeddings.create(
                model=self.model, input=texts[i:i + self.batch],
                dimensions=self.dim)                    # v4 / OpenAI 3 系都支持指定维度
            out += [d.embedding for d in sorted(r.data, key=lambda d: d.index)]
        return out

# 切换只改一行
embedder: Embedder = OpenAICompatEmbedder()    # 或 BGEEmbedder()
```

各模型的 `dim` 不一样（BGE 1024，OpenAI 3-small 默认 1536，v4 默认 1024），换模型就要重建 embedding 列并重新 ingest。`text-embedding-v4` 和 OpenAI 3 系可以用 `dimensions` 参数把维度调成和表一致（本手册统一用 1024），但向量空间仍然变了，旧向量不能和新模型生成的查询向量混用。所以最好在大批量 ingest 之前，就用评估集把模型选定。

> 另外别忘了索引的硬限制：pgvector 的 `vector` 列建索引最多 2000 维。`text-embedding-3-large` 默认 3072 维，v4 选 2048 维时也超了。要么用 `dimensions` 降到 2000 以内，要么改存 `halfvec`（索引上限 4000 维）。

---

## 5. 查询和文档用同一个模型

检索时，查询和文档必须用同一个 embedding 模型编码，否则两边不在一个向量空间里，检索结果会一团乱。道理很简单，但换模型时很容易只重新 ingest 了文档，忘了查询这边也要换。把模型选择收在一处（上面的 `embedder`），就不会犯这个错。

有些模型还区分查询和文档的写法，比如 bge 中文模型建议检索时在查询前面加 `"为这个句子生成表示以用于检索相关文章："`，文档不加。用之前先看模型卡的说明。

---

## 6. 常见坑

| 坑 | 后果 | 正确做法 |
|----|------|----------|
| 中文知识库只用最出名的模型 | 中文召回可能不如其他候选 | 用评估集实测再定 |
| 照着通用榜单选模型 | 你的领域和榜单未必一致 | 在自己的评估集上测 |
| embedding 写死在代码里 | 想换模型要大改 | 抽象成可切换的接口 |
| 查询和文档用了不同模型 | 向量空间错位，检索结果混乱 | 模型选择收在一处 |
| 没选定就 ingest 全部文档 | 换模型要全部重来 | 先用小样本选型，再全量 |

---

## 下一步

数据层和向量层定了，接下来看生成层为什么选 Pydantic AI：

→ [生成框架选型](./04-framework-pydantic-ai.md)
