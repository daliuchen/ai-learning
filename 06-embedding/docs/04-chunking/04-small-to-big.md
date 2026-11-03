# 多粒度切分：Small-to-Big / Parent Document

> 召回用小 chunk，因为信息密度高、检索准；交给 LLM 的用大 chunk，因为上下文完整、答案才对。这种多粒度的做法几乎不增加 embed 费用，代价主要是向量数变多，是我认为 RAG 进阶阶段最值得先做的一项优化。

---

## 1. 问题：单粒度的两难

```
chunk_size=200：
  ✅ 召回精准（信息密度高）
  ❌ LLM 拿到的上下文不够，"那是什么意思我不知道"
  
chunk_size=1000：
  ✅ LLM 上下文足
  ❌ 召回偏（1000 字里关键信息被稀释）
```

---

## 2. Small-to-Big 的做法

```
1. 索引阶段：
   一个大段落（1000 字）→ 切成 5 个小 chunk（200 字每个）
   - 5 个小 chunk 都 embed 进向量库
   - 但都带一个 metadata "parent_id" 指向大段落
   
2. 检索阶段：
   query → 在小 chunk 里 ANN 搜
   找到 top-N 小 chunk
   → 通过 parent_id 拿回对应的大段落
   → 把"大段落"喂给 LLM
```

```
Embedding: 小 chunk（精准）
LLM 输入: 大 chunk / 整段（完整）
```

---

## 3. 实现

```python
import uuid
from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_with_parent(text, parent_size=1000, child_size=200, child_overlap=20):
    # 切大块
    parent_splitter = RecursiveCharacterTextSplitter(chunk_size=parent_size, chunk_overlap=0)
    parents = parent_splitter.split_text(text)
    
    # 每大块再切小
    child_splitter = RecursiveCharacterTextSplitter(chunk_size=child_size, chunk_overlap=child_overlap)
    
    items = []
    parent_store = {}
    for p_text in parents:
        p_id = str(uuid.uuid4())
        parent_store[p_id] = p_text
        
        children = child_splitter.split_text(p_text)
        for c_text in children:
            items.append({
                "text": c_text,         # 给 embed 用
                "parent_id": p_id,      # 检索后用来取 parent
            })
    return items, parent_store
```

### 索引

```python
items, parent_store = chunk_with_parent(my_long_doc)

# embed 小 chunk
for item in items:
    item["embedding"] = embed(item["text"])
    
# 写向量库（含 parent_id 在 metadata）
upsert_to_db(items)

# parent_store 单独存（Redis / DB / 文件）
save_parents(parent_store)
```

### 检索

```python
def retrieve(query, top_k=5):
    q_emb = embed(query)
    
    # 1. 在小 chunk 上召回
    hits = vector_db.search(q_emb, limit=top_k * 3)  # 多召回点
    
    # 2. 去重 parent
    seen = set()
    parents = []
    for h in hits:
        pid = h.payload["parent_id"]
        if pid not in seen:
            seen.add(pid)
            parents.append(parent_store[pid])
        if len(parents) >= top_k:
            break
    
    return parents
```

---

## 4. 用 LangChain 的 ParentDocumentRetriever

```python
# langchain 1.x：这类老的链和检索器已搬到 langchain-classic（pip install langchain-classic）
from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_core.stores import InMemoryStore
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


# 子 chunk splitter（小）
child_splitter = RecursiveCharacterTextSplitter(chunk_size=200, chunk_overlap=20)


# 向量库（只存小 chunk）
vectorstore = Chroma(embedding_function=OpenAIEmbeddings(model="text-embedding-3-small"))


# parent 存哪
docstore = InMemoryStore()  # 生产用 Redis / DB


retriever = ParentDocumentRetriever(
    vectorstore=vectorstore,
    docstore=docstore,
    child_splitter=child_splitter,
    parent_splitter=RecursiveCharacterTextSplitter(chunk_size=1000),
)


retriever.add_documents(my_documents)


# 查
results = retriever.invoke("如何取消订阅")
# 返回的是 parent docs（大段落）
```

---

## 5. LlamaIndex 的等价用法

```python
from llama_index.core import VectorStoreIndex, Document, StorageContext
from llama_index.core.node_parser import HierarchicalNodeParser, get_leaf_nodes


# 多粒度切
parser = HierarchicalNodeParser.from_defaults(
    chunk_sizes=[2048, 512, 128],
    chunk_overlap=20,
)


nodes = parser.get_nodes_from_documents([Document(text=my_long_text)])

# 所有层级的 node 进 docstore，只有最底层的叶子 node 建向量索引
storage_context = StorageContext.from_defaults()
storage_context.docstore.add_documents(nodes)
vector_index = VectorStoreIndex(get_leaf_nodes(nodes), storage_context=storage_context)


# 用 AutoMergingRetriever：召回小 node，自动合并到 parent
from llama_index.core.retrievers import AutoMergingRetriever


retriever = AutoMergingRetriever(vector_index.as_retriever(), storage_context, verbose=True)
```

---

## 6. Sentence Window

这是 small-to-big 的一个变种：召回时只匹配单句，交给 LLM 时带上前后几句作为窗口。

```python
def index_with_window(sentences, window=2):
    chunks = []
    for i, sent in enumerate(sentences):
        chunks.append({
            "text": sent,           # embed 用单句
            "context": " ".join(sentences[max(0, i-window): i+window+1]),  # LLM 用窗口
        })
    return chunks


# 检索后
hits = vector_db.search(q_emb, ...)
for h in hits:
    print(h.payload["context"])  # 给 LLM 的是 window
```

LlamaIndex 内置了 `SentenceWindowNodeParser`：

```python
from llama_index.core.node_parser import SentenceWindowNodeParser


parser = SentenceWindowNodeParser.from_defaults(
    window_size=3,
    window_metadata_key="window",
    original_text_metadata_key="original_text",
)


nodes = parser.get_nodes_from_documents([Document(text=my_text)])
```

---

## 7. 何时用哪种

```
普通文档 RAG：
  → Parent Document Retriever
  → 200 字子 chunk + 1000 字 parent

短文本（FAQ）：
  → 不需要多粒度，直接 chunk

长文档 / 论文：
  → Hierarchical 多层（128/512/2048）

对话历史：
  → Sentence Window

代码：
  → 子 chunk = 函数体一行，parent = 函数 / 类
```

---

## 8. 性能 / 成本影响

```
单粒度（500 字 chunk，共 N 块）：
  embed cost: T tokens（整个语料过一遍）
  存储: N vec

Small-to-Big（200 字子块 + 1000 字 parent）：
  embed cost: 约 T tokens  (只 embed 子块，语料还是过一遍，overlap 会多一点)
  存储: 约 2.5N vec  (500 / 200 = 2.5) + parent text（只存文本，不 embed）
  Recall@5: 通常有提升，幅度看语料，用自己的 evalset 测
```

embed 的 token 费用基本不变，多出来的是向量库里约 2.5 倍的向量数，存储和索引内存跟着涨。在我知道的 RAG 优化里，它的性价比排得很靠前。

---

## 9. 完整 demo

```python
import uuid
import numpy as np
from openai import OpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter


client = OpenAI()


def embed(text):
    resp = client.embeddings.create(model="text-embedding-3-small", input=[text])
    return np.array(resp.data[0].embedding)


long_doc = """
# 订阅管理

我们提供 Free / Pro / Enterprise 三种套餐。Free 永久免费，含基础功能。Pro 每月 $20，含全部 AI 功能。Enterprise 联系销售获取报价。

## 如何取消订阅

要取消订阅，请按以下步骤操作：
1. 登录您的账户
2. 进入"设置"页面
3. 点击"账户"标签
4. 在"订阅"部分点击"取消订阅"按钮
确认后，订阅将在当前周期结束后停止。

## 退款政策

退款仅适用于年付订阅，月付不退。首次订阅后 7 天内可申请全额退款。超过 7 天将按比例退款剩余月份。
"""


# 多粒度切
parent_splitter = RecursiveCharacterTextSplitter(chunk_size=400, chunk_overlap=0, separators=["\n\n"])
child_splitter = RecursiveCharacterTextSplitter(chunk_size=120, chunk_overlap=20)


items = []
parent_store = {}


for p_text in parent_splitter.split_text(long_doc):
    p_id = str(uuid.uuid4())
    parent_store[p_id] = p_text
    
    for c_text in child_splitter.split_text(p_text):
        items.append({
            "text": c_text,
            "parent_id": p_id,
            "embedding": embed(c_text),
        })


print(f"切出 {len(items)} 个小 chunk, {len(parent_store)} 个 parent")


# 查询
query = "如何取消订阅"
q_emb = embed(query)


# 在小 chunk 找
scored = sorted([(item, float(q_emb @ item["embedding"])) for item in items], key=lambda x: -x[1])


# 去重 parent，取前 1 个
seen = set()
for item, score in scored:
    pid = item["parent_id"]
    if pid in seen:
        continue
    seen.add(pid)
    print(f"\n[match score {score:.4f}]")
    print(f"小 chunk: {item['text'][:60]}")
    print(f"\n大段落给 LLM:")
    print(parent_store[pid])
    break
```

---

## 10. parent 存哪

| 选项 | 说明 |
|------|------|
| Redis | 快，按 key 取，跨进程 |
| Postgres / MySQL | 跟业务表一起 |
| Vector DB metadata | 直接塞 metadata 里（如果 parent 不大）|
| 文件系统 | demo / 小项目 |
| KV-store (DynamoDB) | 云原生 |

```python
# 简单 Redis 方案
import redis

r = redis.Redis()


def save_parent(parent_id, text):
    r.set(f"parent:{parent_id}", text)


def get_parent(parent_id):
    return r.get(f"parent:{parent_id}").decode()
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| 召回的子 chunk 都属于同一个 parent | 交给 LLM 的内容会重复，按 parent_id 去重 |
| parent 太长，超出 LLM context | 把 parent 控制在 500-1500 字 |
| parent_store 的数据丢了 | 和向量库一起备份 |
| metadata 里没写 parent_id | 只能重建索引 |

---

## 12. 下一步

- chunk 的 metadata 怎么设计：[Chunk Metadata 设计](./05-metadata.md)
- 检索阶段怎么配合：[重排 Pipeline：召回 → 精排](../05-retrieval/05-rerank-pipeline.md)
- 完整 RAG 实战：[完整 RAG Pipeline 实战](../08-applications/01-full-rag.md)
