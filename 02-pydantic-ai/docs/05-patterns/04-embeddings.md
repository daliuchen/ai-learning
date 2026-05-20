# Pydantic AI 05-04：Embeddings 与 RAG 集成

> **一句话**：Pydantic AI **不内置**向量库和 embedding 抽象 —— 它把这事交给上游 Provider SDK 和成熟向量库（chromadb / faiss / qdrant），自己专注做 Agent；你用它做 RAG 的姿势是"embedding 函数 + 向量库 + 一个 retrieval tool"。

---

## 1. Pydantic AI 对 Embedding 的态度

很多人开始用 Pydantic AI 时会找"`from pydantic_ai.embeddings import ...`" —— 找不到。这是**有意为之**：

| 设计选择 | 理由 |
|----------|------|
| 不封装 embedding 客户端 | OpenAI / Cohere / Voyage 等 SDK 已经够好用，再包一层没价值 |
| 不内置 VectorStore 抽象 | LangChain 那一套 VectorStore 抽象大而全但易碎，Pydantic AI 主张直接用 chromadb 等专业库 |
| 不内置 Document loader | unstructured / llama-index 在这事上做得更好 |

所以 Pydantic AI 在 RAG 场景里只做一件事 —— **Agent 工具层** —— 你在 `@agent.tool` 里调你自己选的向量库就好了。

这种"克制"反而让事情变得简单：你保留对**所有依赖版本的控制权**，不用等 Pydantic AI 升级才能用 chromadb 新版本。

---

## 2. 三层架构速览

一个典型的 RAG Agent 由三层组成：

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

下面分别讲每一层的选型和典型代码。

---

## 3. Embedding Provider 速查

| Provider | 推荐模型 | 维度 | 价格档位 | 调用方式 |
|----------|---------|------|----------|----------|
| **OpenAI** | `text-embedding-3-small` | 1536（可裁剪） | 极低 | `openai.OpenAI().embeddings.create(...)` |
| **OpenAI** | `text-embedding-3-large` | 3072 | 低 | 同上 |
| **Cohere** | `embed-multilingual-v3.0` | 1024 | 低，多语言强 | `cohere.Client().embed(...)` |
| **Voyage AI** | `voyage-3` / `voyage-3-large` | 1024 / 1536 | 中，质量高 | `voyageai.Client().embed(...)` |
| **Jina AI** | `jina-embeddings-v3` | 1024 | 低 | HTTP POST |
| **HuggingFace 本地** | `bge-m3` / `bge-large-zh-v1.5` | 1024 | 0（自建） | `sentence-transformers` |
| **本地 Ollama** | `nomic-embed-text` | 768 | 0（自建） | `ollama` SDK |

**选型经验**：

- **English / 通用** → OpenAI `text-embedding-3-small` 性价比之王
- **多语言（含中文）** → Voyage `voyage-3` 或 Cohere `embed-multilingual-v3.0`
- **本地部署 / 数据敏感** → `bge-m3`（中英双强，开源）
- **预算无限 / 检索质量优先** → Voyage `voyage-3-large` 或 `text-embedding-3-large`

### 3.1 OpenAI 最小调用

```python
from openai import AsyncOpenAI

oai = AsyncOpenAI()

async def embed(texts: list[str]) -> list[list[float]]:
    resp = await oai.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    return [d.embedding for d in resp.data]
```

### 3.2 维度裁剪（OpenAI v3 系列特性）

```python
resp = await oai.embeddings.create(
    model="text-embedding-3-large",
    input=texts,
    dimensions=512,   # 把 3072 维裁到 512 维
)
```

存储成本和检索速度都能省 6 倍，**但向量库一开始用什么维度就要锁死**（见第 10 节常见坑）。

---

## 4. 选个向量库

| 库 | 单机 | 服务端 | 适合规模 | 典型用法 |
|----|------|--------|----------|----------|
| **chromadb** | ✅ | ✅ | < 100w 文档 | 嵌入式或独立 server |
| **faiss** | ✅ | ❌（要自己包） | 单机超大规模 | 纯 numpy 风格 |
| **qdrant** | ❌（也能本地跑） | ✅ | 中大型 | gRPC / HTTP |
| **pgvector** | ❌ | ✅ | 已用 Postgres | SQL 友好 |
| **lancedb** | ✅ | ✅ | 嵌入式 + 列式 | 类 SQLite 体验 |

**Demo 首选 chromadb** —— 零配置、纯 Python、API 简洁；上规模换 qdrant 或 pgvector。

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

把上面两块拼起来，**关键就一个工具函数**：

```python
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext

@dataclass
class RagDeps:
    collection: "chromadb.api.models.Collection.Collection"
    embed_fn: callable  # async def embed(texts) -> embeddings

agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=RagDeps,
    system_prompt=(
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
    [vec] = await ctx.deps.embed_fn([query])
    hit = ctx.deps.collection.query(query_embeddings=[vec], n_results=k)
    docs = hit["documents"][0]
    return "\n---\n".join(f"[{i+1}] {d}" for i, d in enumerate(docs))
```

**就这样**。一个能用的 RAG Agent 写完。完整 demo 见 [`demos/patterns/04_embeddings.py`](../../demos/patterns/04_embeddings.py)。

---

## 6. 5 分钟 mini RAG

完整流程拼起来：

```python
import asyncio
from openai import AsyncOpenAI
import chromadb
from pydantic_ai import Agent, RunContext

oai = AsyncOpenAI()

async def embed(texts: list[str]) -> list[list[float]]:
    r = await oai.embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in r.data]

async def main():
    client = chromadb.Client()
    coll = client.get_or_create_collection("mini", metadata={"hnsw:space": "cosine"})

    docs = [
        "Pydantic AI 是 Pydantic 团队推出的 LLM 框架。",
        "TestModel 让你免费跑单测。",
        "Agent.run_stream 用于 SSE 流式输出。",
    ]
    coll.add(ids=[f"d{i}" for i in range(len(docs))],
             documents=docs,
             embeddings=await embed(docs))

    agent = Agent("openai:gpt-4o-mini", deps_type=dict,
                  system_prompt="先 retrieve 再回答。")

    @agent.tool
    async def retrieve(ctx, query: str) -> str:
        [v] = await embed([query])
        hit = coll.query(query_embeddings=[v], n_results=3)
        return "\n".join(hit["documents"][0])

    r = await agent.run("Pydantic AI 怎么免费跑单测？", deps={})
    print(r.output)

asyncio.run(main())
```

---

## 7. Embedding 缓存策略

embedding 是 **纯函数**：相同输入 + 相同模型 → 相同输出。可以放心缓存：

```python
import hashlib, json
from functools import lru_cache

def _key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}::{text}".encode()).hexdigest()

# 简单的本地 KV 缓存
cache: dict[str, list[float]] = {}

async def embed_cached(texts: list[str], model="text-embedding-3-small"):
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
        new = await embed(miss)
        for i, v in zip(miss_idx, new):
            cache[_key(texts[i], model)] = v
            out[i] = v
    return out
```

生产环境换成 Redis / SQLite 持久化即可。**长期能省 60%-80% embedding 调用**。

更精细的策略：

| 策略 | 适用场景 |
|------|----------|
| **进程内 LRU** | 短时高频重复 query |
| **Redis 缓存** | 多实例共享 |
| **SQLite 持久化** | 单机长期 |
| **不缓存** | embedding 极便宜（OpenAI v3-small）+ 内容动态 |
| **向量库自带缓存** | 文档侧 embedding 只算一次（写入向量库即缓存） |

---

## 8. 增量更新与重建

文档变了怎么办？三种策略：

### 8.1 按 doc_id 删除重写（最常见）

```python
collection.delete(ids=["d1"])
collection.add(ids=["d1"], documents=[new_text], embeddings=[await embed([new_text])])
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
        embeddings=[await embed([text])][0],
        metadatas=[{"hash": h}],
    )
```

### 8.3 整库重建（升级 embedding 模型时）

换模型 = 维度变 / 语义空间变 → 必须**整库重算**。建议每个版本独立 collection：

```python
coll = client.get_or_create_collection(name=f"docs_v{EMBED_VERSION}")
```

切换流量时改 `EMBED_VERSION` 常量，旧的 collection 留几天作为回滚。

---

## 9. 与 LangChain VectorStore 对比

| 维度 | LangChain VectorStore | Pydantic AI（直接用 chromadb 等） |
|------|------------------------|------------------------------------|
| 抽象层 | 统一的 `VectorStore.add_documents / similarity_search` | 没有抽象层，直接调底层 API |
| 切换底层难度 | 低（理论上） | 中（要改几行代码） |
| 跟底层版本绑定 | 紧（LangChain 升级要等 partner 包） | 松（你自己锁版本） |
| 高级特性（filter / hybrid / rerank） | 抽象不够全，常常要绕开 | 直接用底层全部能力 |
| 代码量 | 少（5 行能跑） | 略多（10-20 行） |
| 心智负担 | 多一层 abstraction | 少一层但要懂底层 |

**结论**：

- 早期 demo / 教学：LangChain 那种 5 行起步更友好
- 生产 / 长期维护：Pydantic AI 风格更稳，底层升级你随便
- 已经在用 LangChain ：不矛盾，可以 LangChain 做检索 + Pydantic AI 跑 Agent

---

## 10. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 检索结果全是无关内容 | embedding 模型不适合中文 / 跨语言 | 换成 `bge-m3` / `voyage-3` / `embed-multilingual-v3.0` |
| query 维度对不上向量库 | 查询时换了模型 / 维度 | 把 model + dimensions 写成全局常量，所有写入/查询都引用 |
| chromadb 速度慢 | 默认距离是 `l2`，相似度排序不直观 | 建表时 `metadata={"hnsw:space": "cosine"}` |
| 更新一个文档要全库重算 | 没用 `upsert` / 没存 hash | 每条 doc 加 `content_hash` metadata，比对后再 upsert |
| 加了维度裁剪后检索质量崩 | 裁太狠（如 3072 → 64） | 一般不低于 256，效果损失会显著放大 |
| token 不够（一次 embed 太多） | 单次请求超过 API 限额 | 分批，OpenAI 单 request ≤ 2048 条 |
| Agent 不调 retrieve 工具 | 工具描述太模糊 / system prompt 没强调 | docstring 写清楚触发条件，system prompt 显式说"先检索再回答" |
| 多语言库混合检索差 | 一半中文一半英文，单一 embedding 不够强 | 用专门的 multilingual 模型，或分库分别检索后融合 |
| 检索召回够但排序不准 | 余弦相似度本身有限 | 加一层 reranker（`bge-reranker-v2-m3` / Cohere rerank API） |
| 升级 embedding 模型后向量库报错 | 维度不一致 | 新模型用新 collection，灰度切流量 |
| 文档太长被截断 | 没切 chunk 直接 embed | 切 chunk（500-1000 token 一段，10-20% overlap） |
| 用户问题太短，召回差 | 短 query 信息量不足 | 用 LLM 做 query 改写 / HyDE 假设性回答 |

---

## 11. 生产环境建议

1. **版本化 collection**：`docs_v3`、`docs_v4`，便于回滚和灰度
2. **embedding 异步并行**：用 `asyncio.gather` 批量请求，吞吐量 10 倍提升
3. **本地缓存 + 持久化**：所有 embedding 写 Redis / SQLite，长期省钱
4. **加 reranker**：召回 top 20 → rerank 出 top 3，质量提升一档
5. **chunk 策略要测**：500 / 1000 / 2000 token 都试一遍，看哪个召回最好
6. **观测 retrieval 指标**：用 Logfire 记录每次 retrieve 的 query / hit / score
7. **失败降级**：embedding API 挂了走全文检索兜底，别让整个 Agent 不可用
8. **隐私合规**：embedding 也可能泄漏原文信息，敏感数据用本地模型（`bge-m3` 等）

---

## 12. 本章 demo

完整可运行代码：[`demos/patterns/04_embeddings.py`](../../demos/patterns/04_embeddings.py)

跑：

```bash
pip install chromadb openai
python demos/patterns/04_embeddings.py
```

到这本章节结束。RAG 进阶（hybrid search / rerank / agentic RAG）留到后续章节。
