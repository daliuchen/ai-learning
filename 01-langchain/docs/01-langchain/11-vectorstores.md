# LangChain 11：Embeddings 与 Vector Stores

> **一句话**：Embedding 把文本变成向量，Vector Store 把向量存进库并支持相似度检索。这两件事是 RAG 的"基础设施层"，选择直接影响成本与性能。

---

## 1. Embedding 模型概览

每个 Embedding 模型把字符串映射到固定维度的浮点数组。常见模型：

| 模型 | 维度 | 价格/性能 | 中文支持 |
|------|------|-----------|----------|
| OpenAI `text-embedding-3-small` | 1536（可缩） | $0.02 / 1M tokens | 中等 |
| OpenAI `text-embedding-3-large` | 3072（可缩） | $0.13 / 1M | 好 |
| `bge-large-zh-v1.5` (HF) | 1024 | 本地免费 | **优秀** |
| `bge-m3` (HF) | 1024（多向量） | 本地免费 | 优秀 + 多语言 |
| Cohere `embed-multilingual-v3` | 1024 | 付费 | 好 |
| `nomic-embed-text` (Ollama) | 768 | 本地免费 | 一般 |
| Voyage AI `voyage-3` | 1024 | 付费 | 好 |

**中文项目首选**：bge-large-zh-v1.5 / bge-m3 本地部署，或 OpenAI 3-large。

---

## 2. 在 LangChain 中使用 Embedding

所有 Embedding 实现 `Embeddings` 接口：

```python
class Embeddings:
    def embed_documents(self, texts: List[str]) -> List[List[float]]: ...
    def embed_query(self, text: str) -> List[float]: ...
```

**注意**：`embed_documents` 是给文档用的，`embed_query` 是给查询用的，部分模型（BGE 系列）两者 prompt 不一样。

### 2.1 OpenAI

```python
from langchain_openai import OpenAIEmbeddings

emb = OpenAIEmbeddings(model="text-embedding-3-small")
v = emb.embed_query("你好")
print(len(v))   # 1536

# 缩短维度（OpenAI 3 系列支持）
emb_short = OpenAIEmbeddings(model="text-embedding-3-small", dimensions=512)
```

### 2.2 本地 HuggingFace

```python
from langchain_huggingface import HuggingFaceEmbeddings

emb = HuggingFaceEmbeddings(
    model_name="BAAI/bge-large-zh-v1.5",
    model_kwargs={"device": "cpu"},   # 或 "cuda"
    encode_kwargs={"normalize_embeddings": True},
)
```

第一次运行会自动下载模型（约 1.3 GB）。

### 2.3 Ollama（本地）

```python
from langchain_ollama import OllamaEmbeddings

emb = OllamaEmbeddings(model="nomic-embed-text")
```

需先 `ollama pull nomic-embed-text`。

### 2.4 自定义

继承 `Embeddings` 类实现两个方法即可，用 sentence-transformers / triton-server 都行。

---

## 3. Vector Store 接口

所有 VectorStore 实现这些方法：

```python
.add_documents(docs: List[Document]) -> List[str]
.add_texts(texts, metadatas) -> List[str]
.similarity_search(query, k=4, filter=None) -> List[Document]
.similarity_search_with_score(query, k=4) -> List[Tuple[Document, float]]
.max_marginal_relevance_search(query, k=4, fetch_k=20) -> List[Document]
.as_retriever(search_type="similarity", search_kwargs={...}) -> Retriever
.delete(ids: List[str]) -> bool
```

---

## 4. 常见 Vector Store

### 4.1 Chroma（本地，最简单）

```python
from langchain_chroma import Chroma   # 新包

vs = Chroma.from_documents(
    documents=chunks,
    embedding=emb,
    persist_directory="./chroma_db",
    collection_name="my_kb",
)
vs.similarity_search("LCEL 是什么", k=3)
```

下次启动：

```python
vs = Chroma(
    collection_name="my_kb",
    embedding_function=emb,
    persist_directory="./chroma_db",
)
```

适合 < 1M 文档的本地 / 小型项目。

### 4.2 FAISS（内存级，超快）

```python
from langchain_community.vectorstores import FAISS

vs = FAISS.from_documents(chunks, emb)
vs.save_local("./faiss_index")

# 加载
vs2 = FAISS.load_local("./faiss_index", emb, allow_dangerous_deserialization=True)
```

不支持 metadata 高级过滤，纯向量检索。

### 4.3 PGVector（Postgres 扩展，生产推荐）

```python
from langchain_postgres.vectorstores import PGVector

vs = PGVector.from_documents(
    documents=chunks,
    embedding=emb,
    collection_name="kb",
    connection="postgresql+psycopg://user:pass@localhost/db",
)
```

支持复杂 metadata SQL 过滤，企业首选之一。

### 4.4 Milvus / Weaviate / Qdrant

```python
from langchain_milvus import Milvus
vs = Milvus.from_documents(chunks, emb, connection_args={"uri": "http://localhost:19530"})

from langchain_community.vectorstores import Weaviate, Qdrant
```

百万-千万级 + 复杂 schema 时上这些专业向量库。

### 4.5 Elasticsearch / OpenSearch

混合检索（BM25 + 向量）友好：

```python
from langchain_elasticsearch import ElasticsearchStore

vs = ElasticsearchStore.from_documents(
    documents=chunks,
    embedding=emb,
    es_url="http://localhost:9200",
    index_name="kb",
)
```

### 4.6 内存 + DataFrame

最快上手，适合 prototype：

```python
from langchain_core.vectorstores import InMemoryVectorStore
vs = InMemoryVectorStore.from_documents(chunks, emb)
```

不持久化。

---

## 5. 添加 / 更新 / 删除

```python
ids = vs.add_documents([
    Document(page_content="新文档", metadata={"source": "x"}),
])

# 更新（重新 add + delete 老 id）
vs.delete(ids)

# 按条件删除
vs.delete(filter={"source": "x"})  # 是否支持取决于实现
```

**关键工程实践**：给 doc 一个稳定 id（如 hash），重启重建索引时去重。

---

## 6. similarity_search 的过滤

带 metadata 过滤：

```python
vs.similarity_search(
    "LCEL",
    k=3,
    filter={"source": "guide.md"},
)
```

Chroma 支持 `{"source": {"$in": ["a","b"]}}`，PGVector 用 SQL 表达式，Pinecone / Milvus 用 schema 字段，**具体语法看各实现文档**。

`as_retriever` 可以把过滤"绑死"：

```python
retriever = vs.as_retriever(
    search_kwargs={"k": 4, "filter": {"team": "infra"}},
)
```

---

## 7. MMR：缓解结果同质化

向量检索常返回非常相似的几条，丢失多样性。MMR (Maximal Marginal Relevance) 在相关性和多样性之间权衡：

```python
vs.max_marginal_relevance_search(
    query="LangChain 是什么",
    k=4,          # 返回多少
    fetch_k=20,   # 候选池大小
    lambda_mult=0.5,  # 0~1，越大越偏相关，越小越偏多样
)
```

也支持 retriever 形式：

```python
retriever = vs.as_retriever(search_type="mmr", search_kwargs={"k": 5, "fetch_k": 30})
```

---

## 8. similarity_search_with_score

```python
for doc, score in vs.similarity_search_with_score("...", k=3):
    print(score, doc.page_content[:80])
```

注意**不同 VectorStore 的 score 语义不一样**：
- Chroma: distance（越小越像）
- FAISS: distance（越小越像）
- PGVector: cosine distance（越小越像）
- Pinecone: similarity（越大越像）

建议自己做归一化或绝对阈值前先实测。

---

## 9. 索引参数与性能

| 库 | 索引参数 |
|----|----------|
| FAISS | `IndexFlat` / `IVF` / `HNSW`，构建时选 |
| Chroma | 内置 HNSW，参数 `hnsw:space` |
| PGVector | `vector_l2_ops` / `vector_cosine_ops`，`HNSW` 或 `IVFFlat` |
| Milvus | `IVF_FLAT` / `HNSW` / `GPU_IVF_FLAT` |

百万级以上必须用 ANN（近似最近邻），HNSW 是当前最通用选择。

---

## 10. 实战 demo：构建一个本地知识库

```python
# demos/langchain/11_vectorstore.py
import os
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader, TextLoader

DOCS = "docs/01-langchain"
PERSIST = "./chroma_kb"

emb = OpenAIEmbeddings(model="text-embedding-3-small")

if not os.path.exists(PERSIST):
    docs = DirectoryLoader(DOCS, glob="*.md", loader_cls=lambda p: TextLoader(p, encoding="utf-8")).load()
    chunks = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=80).split_documents(docs)
    vs = Chroma.from_documents(chunks, emb, persist_directory=PERSIST, collection_name="lc")
else:
    vs = Chroma(persist_directory=PERSIST, collection_name="lc", embedding_function=emb)

for d, s in vs.similarity_search_with_score("LCEL 是什么", k=3):
    print(f"[{s:.4f}] {d.metadata.get('source')}\n{d.page_content[:200]}\n")
```

---

## 11. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 中文检索质量差 | 用了英文 embedding | 换 bge-zh / OpenAI 3-large |
| 同一查询返回完全一样的 K 条 | 文档高度重复 | 用 MMR / 去重 |
| 持久化文件越用越大 | Chroma 不会自动 compact | 定期重建索引 |
| add 时 id 冲突 | 没设 ids | 显式 hash 算 id |
| `filter` 不生效 | 不同 VS 语法不同 | 查文档 / 用 `as_retriever` 试 |
| 加载 FAISS 报安全错误 | pickle 安全开关 | `allow_dangerous_deserialization=True` |

---

## 12. 本章 demo

[`demos/langchain/11_vectorstore.py`](../../demos/langchain/11_vectorstore.py)

下一篇：[12-retrievers.md](12-retrievers.md)
