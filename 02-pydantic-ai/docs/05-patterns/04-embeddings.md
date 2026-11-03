# Pydantic AI 05-04：Embeddings 与 RAG 集成

> Pydantic AI 提供了统一的 `Embedder`（`embed_query` / `embed_documents`，可以对接 OpenAI、Cohere、Voyage、Google、Bedrock、本地 sentence-transformers 等），但不管向量库。用它做 RAG，就是 `Embedder` 加上你自己选的向量库，再加一个检索工具。

以 `pydantic-ai 2.64.0` 为准。V2 内置了 embedding 抽象 `Embedder`（`from pydantic_ai import Embedder`）。

---

## 1. Pydantic AI 对 Embedding 的态度

| 设计选择 | 现状 |
|----------|------|
| embedding 客户端 | 有：`Embedder`，统一接口、带用量与成本、可 OTel 观测；provider 前缀与模型一致（`openai:` / `google:` / `cohere:` / `voyageai:` / `bedrock:` / `sentence-transformers:`…） |
| VectorStore 抽象 | 没有：你选 FAISS / LanceDB / pgvector / 托管向量库，维度要与 embedding 输出一致 |
| Document loader | 没有：unstructured / llama-index 等在这方面做得更好 |

所以在 RAG 场景里，Pydantic AI 只做两件事：提供统一的 embedding 接口，以及 Agent 工具层，你在 `@agent.tool` 里调用自己选的向量库。这样向量库的版本始终由你自己掌控。

### 1.1 `Embedder` 速览

```python
import asyncio
from pydantic_ai import Embedder

embedder = Embedder("openai:text-embedding-v4")      # 千问：配 OPENAI_API_KEY + OPENAI_BASE_URL；官方 OpenAI 用 text-embedding-3-small

async def main():
    q = await embedder.embed_query("什么是机器学习？")                       # 查询侧
    d = await embedder.embed_documents(["机器学习是 AI 的子集。", "Python 是一门语言。"])   # 文档侧
    print(len(q.embeddings[0]), len(d.embeddings))     # 1024  2
    print(q.usage.input_tokens)                          # 用量；q.cost() 估算费用
    vec = d["Python 是一门语言。"]                          # 结果可按下标或原文取

asyncio.run(main())
```

几个要点：

- 查询和文档要分开调用 `embed_query` / `embed_documents`，有些模型对两者做了不同的优化；
- 同步版本 `embed_query_sync` / `embed_documents_sync` 不能在正在运行的事件循环里调用（比如 async 工具里），会报 `This event loop is already running`，工具里一律用 `await` 异步版；
- 支持维度控制（`EmbeddingSettings(dimensions=...)`，OpenAI v3 系列）；`Embedder(..., instrument=True)` 可以接 Logfire；
- 离线测试用 `TestEmbeddingModel(dimensions=32)`，它返回确定性的伪向量，没有语义，只用来跑通流程；
- 换 embedding 模型后语义空间会变，维度也可能变，**必须整库重算**。

---

## 2. 三层架构速览

一个 RAG Agent 通常由三层组成：

```
┌──────────────────────────────────────────┐
│              用户问题                     │
└──────────────────────────────────────────┘
                  │
                  ▼
        ┌──────────────────┐
        │  Pydantic AI     │   ← 这一层是 Agent
        │  Agent + tools   │
        └──────────────────┘
                  │ (retrieval tool)
                  ▼
        ┌──────────────────┐
        │   Vector Store   │   ← 这一层用 chromadb / qdrant / faiss
        └──────────────────┘
                  │
                  ▼
        ┌──────────────────┐
        │  Embedding API   │   ← 这一层直接调 OpenAI / Cohere / Voyage SDK
        └──────────────────┘
```

下面按层讲选型和代码。

---

## 3. Embedding Provider 速查

| Provider | 推荐模型 | 维度 | 价格档位 | 调用方式 |
|----------|---------|------|----------|----------|
| OpenAI | `text-embedding-3-small` | 1536（可裁剪） | 极低 | `openai.OpenAI().embeddings.create(...)` |
| OpenAI | `text-embedding-3-large` | 3072 | 低 | 同上 |
| Cohere | `embed-multilingual-v3.0` | 1024 | 低，多语言强 | `cohere.Client().embed(...)` |
| Voyage AI | `voyage-3` / `voyage-3-large` | 1024 / 1024（3-large 可选 256–2048） | 中，质量高 | `voyageai.Client().embed(...)` |
| Jina AI | `jina-embeddings-v3` | 1024 | 低 | HTTP POST |
| HuggingFace 本地 | `bge-m3` / `bge-large-zh-v1.5` | 1024 | 0（自建） | `sentence-transformers` |
| 本地 Ollama | `nomic-embed-text` | 768 | 0（自建） | `ollama` SDK |
| 阿里云千问 | `text-embedding-v4` | 1024 | 低 | `Embedder("openai:text-embedding-v4")` + `OPENAI_BASE_URL` |

> 表里「调用方式」列是各家原生 SDK 的写法；用 Pydantic AI 时统一走 `Embedder("<provider>:<model>")`。

选型上的经验：

- 英文或通用场景：OpenAI `text-embedding-3-small`，性价比最高
- 多语言（含中文）：Voyage `voyage-3` 或 Cohere `embed-multilingual-v3.0`
- 本地部署或数据敏感：`bge-m3`，开源，中英文效果都好
- 预算充足、检索质量优先：Voyage `voyage-3-large` 或 `text-embedding-3-large`

### 3.1 用 `Embedder` 的最小调用（推荐，取代手写 SDK 调用）

```python
from pydantic_ai import Embedder

embedder = Embedder("openai:text-embedding-3-small")     # 千问：Embedder("openai:text-embedding-v4")

async def embed(texts: list[str]) -> list[list[float]]:
    result = await embedder.embed_documents(texts)
    return [list(v) for v in result.embeddings]
```

官方给的选型建议：托管 API 选 `openai:` / `google:` / `voyageai:` / `cohere:`；无 key、私有、免费用 `sentence-transformers:<hf 模型>`（本手册不装 torch，示例里不跑）；多语言选 Cohere 多语言或 jina / snowflake 的多语言模型；领域专用有 voyage-code / law / finance。

### 3.2 维度裁剪（OpenAI v3 系列特性）

```python
from pydantic_ai.embeddings import EmbeddingSettings

embedder = Embedder("openai:text-embedding-3-large", settings=EmbeddingSettings(dimensions=512))   # 把 3072 维裁到 512 维
```

存储和检索开销都能降到原来的六分之一，但向量库的维度一开始就要定下来，之后不能随便改（见第 10 节常见坑）。

---

## 4. 选个向量库

| 库 | 单机 | 服务端 | 适合规模 | 常见用法 |
|----|------|--------|----------|----------|
| chromadb | 支持 | 支持 | < 100w 文档 | 嵌入式或独立 server |
| faiss | 支持 | 不支持（要自己包一层） | 单机超大规模 | 纯 numpy 风格 |
| qdrant | 主要用服务端（也能本地跑） | 支持 | 中大型 | gRPC / HTTP |
| pgvector | 不支持 | 支持 | 已用 Postgres | SQL 友好 |
| lancedb | 支持 | 支持 | 嵌入式 + 列式 | 类似 SQLite 的体验 |

写 demo 我首选 chromadb：不用配置、纯 Python、API 简单；规模上去了再换 qdrant 或 pgvector。

### 4.1 chromadb 快速上手

```python
import chromadb

client = chromadb.PersistentClient(path="./chroma_data")
collection = client.get_or_create_collection(
    name="docs",
    metadata={"hnsw:space": "cosine"},  # 余弦相似度
)

# 写入
collection.add(
    ids=["d1", "d2"],
    documents=["Python 是一门高级语言。", "Rust 强调内存安全。"],
    embeddings=[[0.1, 0.2, ...], [0.3, 0.4, ...]],   # 自己算好传进来
    metadatas=[{"source": "wiki"}, {"source": "blog"}],
)

# 检索
hit = collection.query(
    query_embeddings=[[0.1, 0.2, ...]],
    n_results=3,
)
print(hit["documents"])
```

---

## 5. 把检索包成 Agent 工具

把 embedding 和向量库拼起来，只需要一个工具函数：

```python
from dataclasses import dataclass
from pydantic_ai import Agent, Embedder, RunContext

embedder = Embedder("openai:text-embedding-v4")

@dataclass
class RagDeps:
    collection: "chromadb.api.models.Collection.Collection"   # 或你自己的向量索引

agent = Agent(
    "openai-chat:qwen-flash",
    deps_type=RagDeps,
    instructions=(
        "回答问题前，先用 retrieve 工具检索相关资料，"
        "只基于检索结果作答，找不到就说不知道。"
    ),
)

@agent.tool
async def retrieve(ctx: RunContext[RagDeps], query: str, k: int = 3) -> str:
    """从知识库检索相关片段。

    Args:
        query: 用户问题或关键词。
        k: 返回结果数。
    """
    vec = (await embedder.embed_query(query)).embeddings[0]      # 工具在事件循环里：用 await 的异步版
    hit = ctx.deps.collection.query(query_embeddings=[list(vec)], n_results=k)
    docs = hit["documents"][0]
    return "\n---\n".join(f"[{i+1}] {d}" for i, d in enumerate(docs))
```

一个能用的 RAG Agent 就写完了。完整 demo 见 [`demos/patterns/04_embeddings.py`](../../demos/patterns/04_embeddings.py)。

---

## 6. 5 分钟 mini RAG

完整流程拼起来：

```python
import asyncio
import math
from pydantic_ai import Agent, Embedder, RunContext

embedder = Embedder("openai:text-embedding-v4")

def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) or 1) * (math.sqrt(sum(y * y for y in b)) or 1))

DOCS = [
    "Pydantic AI 是 Pydantic 团队推出的 LLM 框架。",
    "TestModel 让你免费跑单测。",
    "Agent.run_stream 用于 SSE 流式输出。",
]

async def main():
    vectors = [list(v) for v in (await embedder.embed_documents(DOCS)).embeddings]   # 建索引：文档侧

    agent = Agent("openai-chat:qwen-flash", instructions="先 retrieve 再回答，只基于检索结果作答。")

    @agent.tool_plain
    async def retrieve(query: str) -> str:
        """从知识库检索相关片段。"""
        q = list((await embedder.embed_query(query)).embeddings[0])    # 查询侧
        ranked = sorted(zip(DOCS, vectors), key=lambda dv: cos(q, dv[1]), reverse=True)[:2]
        return "\n".join(d for d, _ in ranked)

    r = await agent.run("Pydantic AI 怎么免费跑单测？")
    print(r.output)

asyncio.run(main())
```

这里用 30 行的内存余弦索引代替了向量库，没有额外依赖，demo 就是这样跑的。规模上去之后换成 FAISS 或 pgvector，工具函数的写法不用变。

---

## 7. Embedding 缓存策略

embedding 是纯函数：输入和模型相同，输出就相同，所以可以放心缓存：

```python
import hashlib, json
from functools import lru_cache

def _key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}::{text}".encode()).hexdigest()

# 简单的本地 KV 缓存
cache: dict[str, list[float]] = {}

async def embed_cached(texts: list[str], model="text-embedding-v4"):
    out, miss, miss_idx = [], [], []
    for i, t in enumerate(texts):
        k = _key(t, model)
        if k in cache:
            out.append(cache[k])
        else:
            out.append(None)
            miss.append(t)
            miss_idx.append(i)
    if miss:
        new = [list(v) for v in (await embedder.embed_documents(miss)).embeddings]
        for i, v in zip(miss_idx, new):
            cache[_key(texts[i], model)] = v
            out[i] = v
    return out
```

生产环境换成 Redis 或 SQLite 持久化即可。重复文本越多（反复入库的文档、热门查询），省下的调用越多。

按场景选缓存方式：

| 策略 | 适用场景 |
|------|----------|
| 进程内 LRU | 短时高频重复 query |
| Redis 缓存 | 多实例共享 |
| SQLite 持久化 | 单机长期 |
| 不缓存 | embedding 极便宜（OpenAI v3-small）+ 内容动态 |
| 向量库自带缓存 | 文档侧 embedding 只算一次（写入向量库即缓存） |

---

## 8. 增量更新与重建

文档变了，有三种处理方式。

### 8.1 按 doc_id 删除重写（最常见）

```python
collection.delete(ids=["d1"])
collection.add(ids=["d1"], documents=[new_text], embeddings=await embed([new_text]))   # embed 返回 list[list[float]]
```

### 8.2 校验和驱动（更省）

为每个文档存一个 `content_hash` metadata，写入前比对：

```python
import hashlib

def hash_text(t: str) -> str:
    return hashlib.sha1(t.encode()).hexdigest()

for doc_id, text in docs.items():
    h = hash_text(text)
    existing = collection.get(ids=[doc_id])
    if existing["ids"] and existing["metadatas"][0]["hash"] == h:
        continue  # 没变，跳过
    collection.upsert(
        ids=[doc_id],
        documents=[text],
        embeddings=await embed([text]),
        metadatas=[{"hash": h}],
    )
```

### 8.3 整库重建（升级 embedding 模型时）

换模型后维度和语义空间都会变，必须整库重算。建议每个版本用独立的 collection：

```python
coll = client.get_or_create_collection(name=f"docs_v{EMBED_VERSION}")
```

切换流量时改 `EMBED_VERSION` 常量，旧的 collection 保留几天，方便回滚。

---

## 9. 与 LangChain VectorStore 对比

| 维度 | LangChain VectorStore | Pydantic AI（直接用 chromadb 等） |
|------|------------------------|------------------------------------|
| 抽象层 | 统一的 `VectorStore.add_documents / similarity_search` | 没有抽象层，直接调底层 API |
| 切换底层难度 | 低（理论上） | 中（要改几行代码） |
| 和底层版本的绑定 | 紧（底层升级要等 LangChain 的 partner 包跟进） | 松（版本你自己锁） |
| 高级特性（filter / hybrid / rerank） | 抽象不够全，常常要绕开 | 直接用底层全部能力 |
| 代码量 | 少（5 行能跑） | 略多（10-20 行） |
| 心智负担 | 多一层抽象 | 少一层，但要懂底层 |

早期 demo 或教学，LangChain 那种 5 行就能跑的写法更友好；生产环境、需要长期维护的，Pydantic AI 这种直接用底层的方式更稳，底层升级的节奏由你自己决定。已经在用 LangChain 的也不冲突，可以用 LangChain 做检索，用 Pydantic AI 跑 Agent。

---

## 10. 常见坑

> 先说三个和 V2 `Embedder` 相关的坑：① 在 async 工具里调 `embedder.embed_query_sync(...)` 会报 `RuntimeError: This event loop is already running`，要改用 `await embedder.embed_query(...)`；② `TestEmbeddingModel` 的向量没有语义，检索结果和问题无关，只能用来跑通流程；③ 千问兼容端点的 embedding 用 `openai:`（Embeddings API），对话模型用 `openai-chat:`，两者互不影响。


| 现象 | 原因 | 解决 |
|------|------|------|
| 检索结果全是无关内容 | embedding 模型不适合中文 / 跨语言 | 换成 `bge-m3` / `voyage-3` / `embed-multilingual-v3.0` |
| query 维度对不上向量库 | 查询时换了模型 / 维度 | 把 model + dimensions 写成全局常量，所有写入/查询都引用 |
| chromadb 返回的距离和预期的余弦相似度对不上 | 默认距离是 `l2` | 建表时 `metadata={"hnsw:space": "cosine"}`（建表后不能再改） |
| 更新一个文档要全库重算 | 没用 `upsert` / 没存 hash | 每条 doc 加 `content_hash` metadata，比对后再 upsert |
| 加了维度裁剪后检索质量大幅下降 | 裁得太狠（如 3072 → 64） | 一般不要低于 256，再低效果损失会明显变大 |
| token 不够（一次 embed 太多） | 单次请求超过 API 限额 | 分批，OpenAI 单 request ≤ 2048 条 |
| Agent 不调 retrieve 工具 | 工具描述太模糊 / system prompt 没强调 | docstring 写清楚触发条件，system prompt 显式说"先检索再回答" |
| 多语言库混合检索差 | 一半中文一半英文，单一 embedding 不够强 | 用专门的 multilingual 模型，或分库分别检索后融合 |
| 检索召回够但排序不准 | 余弦相似度本身有限 | 加一层 reranker（`bge-reranker-v2-m3` / Cohere rerank API） |
| 升级 embedding 模型后向量库报错 | 维度不一致 | 新模型用新 collection，灰度切流量 |
| 文档太长被截断 | 没切 chunk 直接 embed | 切 chunk（500-1000 token 一段，10-20% overlap） |
| 用户问题太短，召回差 | 短 query 信息量不足 | 用 LLM 做 query 改写 / HyDE 假设性回答 |

---

## 11. 生产环境建议

1. collection 按版本命名，比如 `docs_v3`、`docs_v4`，方便回滚和灰度
2. embedding 请求分批后用 `asyncio.gather` 并发发出（注意限流），比逐条串行快得多
3. 所有 embedding 都缓存到 Redis 或 SQLite，长期能省不少钱
4. 加一层 reranker：先召回 top 20，再 rerank 出 top 3，质量会明显好一些
5. chunk 大小要实测，500 / 1000 / 2000 token 都试一遍，看哪个召回最好
6. 用 Logfire 记录每次 retrieve 的 query、命中结果和分数
7. 做好降级：embedding API 不可用时，用全文检索兜底，别让整个 Agent 跟着挂掉
8. 注意隐私：embedding 也可能泄露原文信息，敏感数据用本地模型（`bge-m3` 等）

---

## 12. 本章 demo

完整可运行代码：[`demos/patterns/04_embeddings.py`](../../demos/patterns/04_embeddings.py)

跑：

```bash
pip install chromadb openai
python demos/patterns/04_embeddings.py
```

hybrid search、rerank、agentic RAG 这些进阶内容不在本篇展开，实战篇里会用一个完整的 RAG 项目把前面的内容串起来。
