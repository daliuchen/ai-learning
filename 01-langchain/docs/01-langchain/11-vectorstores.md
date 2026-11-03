# LangChain 11：Embeddings 与 Vector Stores

> Embedding 把文本变成向量，Vector Store 把向量存起来并按相似度检索。这是 RAG 底下的两块基础设施，选哪个模型、哪个库，直接决定了检索质量、成本和能撑多大的数据量。

---

## 1. Embedding 模型概览

每个 Embedding 模型把字符串映射到固定维度的浮点数组。常见模型：

| 模型 | 维度 | 价格/性能 | 中文支持 |
|------|------|-----------|----------|
| OpenAI `text-embedding-3-small` | 1536（可缩） | $0.02 / 1M tokens | 中等 |
| OpenAI `text-embedding-3-large` | 3072（可缩） | $0.13 / 1M | 好 |
| `bge-large-zh-v1.5` (HF) | 1024 | 本地免费 | 优秀 |
| `bge-m3` (HF) | 1024（多向量） | 本地免费 | 优秀 + 多语言 |
| Cohere `embed-multilingual-v3` | 1024 | 付费 | 好 |
| `nomic-embed-text` (Ollama) | 768 | 本地免费 | 一般 |
| Voyage AI `voyage-3` | 1024 | 付费 | 好 |
| 通义千问 `text-embedding-v4` | 默认 1024（可选 64–2048） | 阿里云按量，便宜 | 优秀 |

中文项目我会优先考虑本地部署 bge-large-zh-v1.5 或 bge-m3；不想自己部署，就用通义 `text-embedding-v4` 或 OpenAI 3-large。

---

## 2. 在 LangChain 中使用 Embedding

所有 Embedding 实现 `Embeddings` 接口：

```python
class Embeddings:
    def embed_documents(self, texts: List[str]) -> List[List[float]]: ...
    def embed_query(self, text: str) -> List[float]: ...
```

`embed_documents` 用来编码文档，`embed_query` 用来编码查询。两者别混用：有些模型（BGE、Qwen3-Embedding 等）要求查询加指令前缀、文档不加，分成两个方法才能在实现里处理这种差异。

### 2.1 OpenAI

```python
from langchain_openai import OpenAIEmbeddings

emb = OpenAIEmbeddings(model="text-embedding-3-small")
v = emb.embed_query("你好")
print(len(v))   # 1536

# 缩短维度（OpenAI 3 系列支持）
emb_short = OpenAIEmbeddings(model="text-embedding-3-small", dimensions=512)
```

接千问这类 OpenAI 兼容的 Embedding 服务时有两个坑，用 `text-embedding-v4` 时都会碰到：

```python
emb = OpenAIEmbeddings(
    model="text-embedding-v4",
    check_embedding_ctx_length=False,   # 关掉 tiktoken 预切分：兼容接口不接受 token 数组，只接受字符串
    chunk_size=10,                      # 千问一次请求最多 10 条文本，超了会报错
)
```

`base_url` / `api_key` 同样读 `OPENAI_BASE_URL` / `OPENAI_API_KEY`。统一初始化也可以用 `from langchain.embeddings import init_embeddings`。

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

继承 `Embeddings` 类，实现上面两个方法就行，底层用 sentence-transformers 还是 triton-server 都可以。

---

## 3. Vector Store 接口

所有 VectorStore 都实现了这些方法：

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

适合文档量在百万以下的本地或小型项目。

### 4.2 FAISS（内存级，很快）

```python
from langchain_community.vectorstores import FAISS

vs = FAISS.from_documents(chunks, emb)
vs.save_local("./faiss_index")

# 加载
vs2 = FAISS.load_local("./faiss_index", emb, allow_dangerous_deserialization=True)
```

它本身只做向量检索，LangChain 的 `FAISS` 支持的 `filter`（dict 或函数）是取回候选后再在 Python 里过滤，做不了复杂条件，数据量大时也不高效。

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

能用 SQL 对 metadata 做复杂过滤，已经在用 Postgres 的团队基本不用再引入新组件。

### 4.4 Milvus / Weaviate / Qdrant

```python
from langchain_milvus import Milvus
vs = Milvus.from_documents(chunks, emb, connection_args={"uri": "http://localhost:19530"})

# Weaviate / Qdrant 已拆成独立包：pip install langchain-weaviate / langchain-qdrant
from langchain_qdrant import QdrantVectorStore
```

数据到了百万到千万级、schema 也复杂时，再上这些专门的向量库。

### 4.5 Elasticsearch / OpenSearch

适合做 BM25 + 向量的混合检索：

```python
from langchain_elasticsearch import ElasticsearchStore

vs = ElasticsearchStore.from_documents(
    documents=chunks,
    embedding=emb,
    es_url="http://localhost:9200",
    index_name="kb",
)
```

### 4.6 内存（InMemoryVectorStore）

上手最快，适合做原型：

```python
from langchain_core.vectorstores import InMemoryVectorStore
vs = InMemoryVectorStore.from_documents(chunks, emb)
```

进程退出数据就没了。

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

工程上建议给每个 doc 一个稳定的 id（比如内容 hash），重启或重建索引时才能去重。

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

过滤语法各家不同：Chroma 支持 `{"source": {"$in": ["a","b"]}}`，PGVector 用 SQL 表达式，Pinecone / Milvus 用 schema 字段，具体以各自的文档为准。

`as_retriever` 可以把过滤条件固定下来：

```python
retriever = vs.as_retriever(
    search_kwargs={"k": 4, "filter": {"team": "infra"}},
)
```

---

## 7. MMR：缓解结果同质化

向量检索经常返回几条内容几乎一样的结果。MMR（Maximal Marginal Relevance）在相关性和多样性之间做权衡：

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

不同 VectorStore 返回的 score 含义不一样：
- Chroma: distance（越小越像）
- FAISS: distance（越小越像）
- PGVector: cosine distance（越小越像）
- Pinecone: similarity（越大越像）

所以在做归一化或定绝对阈值之前，先拿自己的数据跑一遍看看分数分布。

---

## 9. 索引参数与性能

| 库 | 索引参数 |
|----|----------|
| FAISS | `IndexFlat` / `IVF` / `HNSW`，构建时选 |
| Chroma | 内置 HNSW，参数 `hnsw:space` |
| PGVector | `vector_l2_ops` / `vector_cosine_ops`，`HNSW` 或 `IVFFlat` |
| Milvus | `IVF_FLAT` / `HNSW` / `GPU_IVF_FLAT` |

到了百万级以上就必须用 ANN（近似最近邻）索引，目前最通用的是 HNSW。

---

## 10. 实战 demo：构建一个本地知识库

完整代码：[`demos/langchain/11_vectorstore.py`](../../demos/langchain/11_vectorstore.py)

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
    vs = Chroma.from_documents(chunks, emb, persist_directory=PERSIST, collection_name="lc_demo")
else:
    vs = Chroma(persist_directory=PERSIST, collection_name="lc_demo", embedding_function=emb)

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
| 重复入库后同一文档存了多份 | 不设 ids 时每次都自动生成新的 UUID | 用内容哈希做 id，重复写入会覆盖而不是新增 |
| `filter` 不生效 | 不同 VS 语法不同 | 查文档 / 用 `as_retriever` 试 |
| 加载 FAISS 报安全错误 | pickle 安全开关 | `allow_dangerous_deserialization=True`（只加载自己生成的文件） |
| `from langchain_community.vectorstores import Chroma` 有弃用警告 | 已有专用包 | `pip install langchain-chroma`，`from langchain_chroma import Chroma` |
| Chroma 报 `Expected a name containing 3-512 characters` | Chroma 1.x 要求 collection 名至少 3 个字符 | 名字别用 `kb` 这种 2 字符短名 |
| 千问 embedding 报 batch size / 参数类型错误 | 兼容接口限制 | `check_embedding_ctx_length=False, chunk_size=10` |

---

下一篇：[LangChain 12：Retrievers 检索器](12-retrievers.md)
