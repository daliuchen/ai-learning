# Chroma / LanceDB：嵌入式向量库

> Chroma 和 LanceDB 都是装一个 pip 包就能用的本地向量库，做开发、原型、notebook 实验和单机小项目时很顺手。到了生产规模或高并发场景，我还是建议换成 Qdrant 或 pgvector。

---

## 1. 什么时候用嵌入式

适合的场景：

- 开发、演示、notebook 实验
- 桌面应用（Electron / Tauri / 客户端聊天工具）
- 单进程的小项目（100 万条文档以内）
- CI 测试，不想依赖外部服务

不适合的场景：

- 多进程或多台机器共享数据
- 高并发（QPS > 100）
- 数据量大（百万条文档以上）

---

## 2. Chroma

### 2.1 安装

```bash
pip install chromadb
```

以 `chromadb 1.5`、`lancedb 0.41` 为准。Chroma 1.x 的内核用 Rust 重写了，Python API 基本兼容。两处要留意：collection 名必须是 3–512 个字符（`[a-zA-Z0-9._-]`，首尾为字母或数字），`"d"` 这种单字符名会报错；距离类型推荐用 `configuration={"hnsw": {"space": "cosine"}}`，老写法 `metadata={"hnsw:space": "cosine"}` 仍然可用。

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
    configuration={"hnsw": {"space": "cosine"}},   # 距离类型（旧写法 metadata={"hnsw:space": "cosine"} 仍可用）
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

embeddings 也可以不传，Chroma 内置了默认的 embedding 函数（sentence-transformers/all-MiniLM）：

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

内置支持的有：

- OpenAI
- Cohere
- HuggingFace（sentence-transformers）
- Google
- 也可以自己实现

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
#   "documents": [["停止订阅的方法", "如何关闭自动续费"]],
#   "distances": [[0.1, 0.2]],
#   "metadatas": [[{"category": "billing"}, ...]],
# }
```

### 2.6 Server 模式

```bash
chroma run --path ./chroma_db --port 8000
```

```python
client = chromadb.HttpClient(host="localhost", port=8000)
```

这样多个进程或多台机器可以共用一个 Chroma server，不过 server 本身仍是单节点。

---

## 3. LanceDB

### 3.1 安装

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

也可以从 dataframe 或 list 推断 schema：

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
)   # 默认建 IVF_PQ，训练至少要 256 行；官方建议十万行量级以上再建，之前暴力扫描就够快
```

---

## 4. Chroma vs LanceDB

| | Chroma | LanceDB |
|---|---|---|
| 数据格式 | 自有存储（1.x 为 Rust 内核） | Lance（列式） |
| Schema | 动态 | 静态（严格类型） |
| 性能（大量级） | 一般 | 强（向量化列存） |
| 多模态 | 有限 | 强 |
| 多向量列 | 有限 | 支持 |
| 增量索引 | 支持 | 支持 |
| 部署 server 模式 | 支持 | 开源版不支持（纯嵌入，托管的 LanceDB Cloud 另算） |
| 生态成熟度 | 高（先发） | 中（Lance 团队） |
| LangChain / LlamaIndex 支持 | 双方都好 | 双方都好 |

怎么选：入门和简单演示用 Chroma，API 最直观；需要多向量列、大文件或列式分析时用 LanceDB；到了生产规模，两个都不合适，换 Qdrant 或 pgvector。

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

多个进程访问同一个目录时，要注意加锁。

---

## 6. 完整的 Chroma 示例

```python
import chromadb
from openai import OpenAI


oai = OpenAI()
client = chromadb.PersistentClient(path="./chroma_demo")


# 自定义 embedding：用 OpenAI（继承 EmbeddingFunction，Chroma 1.x 会校验类型）
from chromadb import Documents, EmbeddingFunction, Embeddings


class OpenAIEmbedder(EmbeddingFunction):
    def __call__(self, input: Documents) -> Embeddings:
        resp = oai.embeddings.create(model="text-embedding-3-small", input=list(input))
        return [d.embedding for d in resp.data]


collection = client.get_or_create_collection(
    name="docs",
    embedding_function=OpenAIEmbedder(),
    configuration={"hnsw": {"space": "cosine"}},
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

## 7. 什么时候该换成专门的向量库

```
数据量 > 100 万 → Qdrant / pgvector
QPS 上百 → Qdrant / Pinecone
要多进程共享 → Server 模式（Chroma server / Qdrant）
要事务 → pgvector
要高可用 → Qdrant 集群 / Milvus
```

迁移不麻烦：导出数据，再 upsert 到新库就行。

---

## 8. 常见坑

| 坑 | 解 |
|----|----|
| 多个进程同时写同一个 PersistentClient 目录 | 不支持，改用 Chroma server 模式或专门的向量库 |
| LanceDB schema 不匹配 | 类型是严格的，提前定义清楚 |
| Chroma 在生产环境扛不住 | 100 万条以内没问题，再大就别用 |
| 数据目录要搬家 | 直接 cp -r，不用导出 |

---

## 9. 下一步

- [HNSW / IVF / PQ 索引原理](./06-index-algorithms.md)
- [混合存储：Vector + Metadata + Filter](./07-hybrid-storage.md)
- 切分策略：从[为什么 Chunking 决定 RAG 的上限](../04-chunking/01-why-chunking.md)开始
