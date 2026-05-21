# Chroma / LanceDB：嵌入式向量库

> **一句话**：Chroma 和 LanceDB 都是"装个 pip 包就能用"的本地向量库——开发、原型、笔记本、单机小项目首选；生产规模 / 高并发场景仍推荐 Qdrant / pgvector。

---

## 1. 何时用嵌入式

✅ 适合：

- 开发 / demo / 笔记本
- 桌面应用（Electron / Tauri / 客户端 chat）
- 单进程小项目（< 100 万 doc）
- CI 测试（不依赖外部服务）

❌ 不适合：

- 多进程 / 多机共享数据
- 高并发（QPS > 100）
- 量级大（> 1000 万 doc）

---

## 2. Chroma

### 2.1 装

```bash
pip install chromadb
```

### 2.2 基本用法

```python
import chromadb


# 持久化模式
client = chromadb.PersistentClient(path="./chroma_db")


# 也可以纯内存
# client = chromadb.Client()


# 创建 collection
collection = client.get_or_create_collection(
    name="docs",
    metadata={"hnsw:space": "cosine"},   # 距离类型
)
```

### 2.3 写入

```python
collection.add(
    documents=["如何关闭自动续费", "如何登录账号", "停止订阅的方法"],
    metadatas=[
        {"category": "billing"},
        {"category": "auth"},
        {"category": "billing"},
    ],
    ids=["1", "2", "3"],
    embeddings=[[...], [...], [...]],    # 你自己 embed 好
)
```

**也可以不传 embeddings**，Chroma 内置默认 embedding 函数（用 sentence-transformers/all-MiniLM）：

```python
collection.add(
    documents=["..."],   # Chroma 自动 embed
    ids=["1"],
)
```

### 2.4 用自定义 embedding 函数

```python
from chromadb.utils import embedding_functions


# OpenAI
openai_ef = embedding_functions.OpenAIEmbeddingFunction(
    api_key="sk-...",
    model_name="text-embedding-3-small",
)


collection = client.get_or_create_collection(
    name="docs",
    embedding_function=openai_ef,
)


collection.add(documents=[...], ids=[...])
# 自动调 OpenAI embed
```

支持：

- OpenAI
- Cohere
- HuggingFace（sentence-transformers）
- Google
- 自己实现

### 2.5 查询

```python
results = collection.query(
    query_texts=["如何取消订阅"],   # Chroma 自动 embed
    n_results=3,
    where={"category": "billing"},   # filter
)


# 或直接给 embedding
results = collection.query(
    query_embeddings=[[...]],
    n_results=3,
)


# 结果
print(results)
# {
#   "ids": [["3", "1"]],
#   "documents": [["停止订阅", "如何关闭自动续费"]],
#   "distances": [[0.1, 0.2]],
#   "metadatas": [[{"category": "billing"}, ...]],
# }
```

### 2.6 部署 Server 模式

```bash
chroma run --path ./chroma_db --port 8000
```

```python
client = chromadb.HttpClient(host="localhost", port=8000)
```

可以多进程 / 多机共用一个 Chroma server。但 server 模式还是单节点。

---

## 3. LanceDB

### 3.1 装

```bash
pip install lancedb
```

### 3.2 基本用法

```python
import lancedb


db = lancedb.connect("./lancedb_data")
```

### 3.3 创建表

```python
import pyarrow as pa


schema = pa.schema([
    pa.field("id", pa.string()),
    pa.field("text", pa.string()),
    pa.field("category", pa.string()),
    pa.field("vector", pa.list_(pa.float32(), list_size=1536)),
])


table = db.create_table("docs", schema=schema)
```

或者从 dataframe / list 推断：

```python
data = [
    {"id": "1", "text": "如何关闭自动续费", "category": "billing", "vector": [0.1, ...]},
    {"id": "2", "text": "登录帮助", "category": "auth", "vector": [0.2, ...]},
]

table = db.create_table("docs", data=data)
```

### 3.4 写入

```python
table.add(data)   # list of dicts
```

### 3.5 查询

```python
hits = table.search([0.1, 0.2, ...]).limit(5).to_list()
# 或加 filter
hits = table.search([0.1, ...]).where("category = 'billing'").limit(5).to_list()
```

### 3.6 建索引

```python
table.create_index(
    metric="cosine",
    vector_column_name="vector",
)
```

---

## 4. Chroma vs LanceDB 对比

| | Chroma | LanceDB |
|---|---|---|
| 数据格式 | SQLite + parquet | Lance（列式） |
| Schema | 动态 | 静态（严格类型） |
| 性能（大量级） | 一般 | 强（向量化列存） |
| 多模态 | ⚠️ | ✅ 强 |
| 多向量列 | ⚠️ | ✅ |
| 增量索引 | ✅ | ✅ |
| 部署 server 模式 | ✅ | ❌（纯嵌入） |
| 生态成熟度 | 高（先发） | 中（Lance 团队） |
| LangChain / LlamaIndex 支持 | 双方都好 | 双方都好 |

**实战推荐**：

- 简单 demo / 入门 → Chroma（API 最直观）
- 多向量列 / 大文件 / 列式分析 → LanceDB
- 生产规模 → 都不行，换 Qdrant / pgvector

---

## 5. 持久化注意

Chroma：

```python
client = chromadb.PersistentClient(path="./chroma_db")
# 每次操作自动写文件
```

LanceDB：

```python
db = lancedb.connect("./lancedb")
# 同上
```

跨进程访问同一目录要小心 lock。

---

## 6. 完整 Chroma demo

```python
# demos/vector_db/05_chroma.py
import chromadb
from openai import OpenAI


oai = OpenAI()
client = chromadb.PersistentClient(path="./chroma_demo")


# 自定义 embedding：用 OpenAI
class OpenAIEmbedder:
    def __call__(self, input):
        resp = oai.embeddings.create(model="text-embedding-3-small", input=input)
        return [d.embedding for d in resp.data]


collection = client.get_or_create_collection(
    name="docs",
    embedding_function=OpenAIEmbedder(),
    metadata={"hnsw:space": "cosine"},
)


# 第一次跑才插入
if collection.count() == 0:
    collection.add(
        documents=[
            "如何关闭自动续费",
            "如何登录账号",
            "停止订阅的方法",
            "重置密码教程",
            "退款流程",
        ],
        metadatas=[
            {"category": "billing"},
            {"category": "auth"},
            {"category": "billing"},
            {"category": "auth"},
            {"category": "billing"},
        ],
        ids=["1", "2", "3", "4", "5"],
    )


# 查
results = collection.query(
    query_texts=["如何取消订阅"],
    n_results=3,
    where={"category": "billing"},
)


for i in range(len(results["ids"][0])):
    print(f"  doc={results['documents'][0][i]}  dist={results['distances'][0][i]:.4f}")
```

---

## 7. 何时升级到正经向量库

```
数据量 > 100 万 → Qdrant / pgvector
QPS > 50 → Qdrant / Pinecone
要多进程共享 → Server 模式（Chroma server / Qdrant）
要事务 → pgvector
要高可用 → Qdrant 集群 / Milvus
```

迁移很容易：导出 → 重新 upsert 到新库。

---

## 8. 常见坑

| 坑 | 解 |
|----|----|
| Chroma 多线程并发 add | 加锁，否则数据损坏 |
| LanceDB schema 不匹配 | 严格类型，提前定义清楚 |
| Chroma 在 production 撑不住 | < 100 万 doc 没问题，更大就别用 |
| 数据目录搬家 | 直接 cp -r，不用 export |

---

## 9. 下一步

- 📖 HNSW / IVF 原理 → [06-index-algorithms.md](./06-index-algorithms.md)
- 📖 混合存储 → [07-hybrid-storage.md](./07-hybrid-storage.md)
- 📖 chunking 策略 → [04-chunking](../04-chunking)
