# LangChain 12：Retrievers 检索器

> **一句话**：Retriever 是 LangChain 对"给定 query 返回相关文档"的统一抽象，向量检索、全文检索、混合检索、Self-Query、压缩重排，都是它的不同实现。生产 RAG 的检索质量上限，90% 决定于 Retriever 设计。

---

## 1. 接口与最小用法

```python
class BaseRetriever:
    def invoke(self, query: str, config=None) -> List[Document]: ...
    async def ainvoke(self, query: str, config=None) -> List[Document]: ...
```

`Retriever` 实现 `Runnable`，可以无缝接 LCEL：

```python
retriever = vs.as_retriever(search_kwargs={"k": 3})
chain = retriever | format_docs | prompt | model | parser
```

---

## 2. 八大 Retriever 类型

### 2.1 VectorStoreRetriever

最常用，由 VectorStore 转出：

```python
retriever = vs.as_retriever(
    search_type="similarity",     # similarity / mmr / similarity_score_threshold
    search_kwargs={"k": 4, "filter": {"team": "infra"}},
)
```

`search_type="similarity_score_threshold"` 加 `score_threshold=0.6` 可以过滤低分结果。

### 2.2 BM25 Retriever（全文检索）

```python
from langchain_community.retrievers import BM25Retriever

bm25 = BM25Retriever.from_documents(chunks, k=4)
bm25.invoke("LCEL 是什么")
```

不需要 Embedding，关键词强项。

### 2.3 EnsembleRetriever（混合检索）

把多个 retriever 结果按权重融合：

```python
from langchain.retrievers import EnsembleRetriever

ensemble = EnsembleRetriever(
    retrievers=[bm25, vector_retriever],
    weights=[0.4, 0.6],
)
```

内部用 RRF (Reciprocal Rank Fusion) 算法。**实际项目最稳的检索方案就是 BM25 + 向量 Ensemble**。

### 2.4 MultiQueryRetriever（多查询扩展）

用 LLM 把一个 query 改写成多个，分别检索再合并：

```python
from langchain.retrievers.multi_query import MultiQueryRetriever

mq = MultiQueryRetriever.from_llm(
    retriever=vector_retriever,
    llm=ChatOpenAI(model="gpt-4o-mini"),
)
mq.invoke("如何流式输出？")
# 内部生成 3 个改写问题：
#   "LangChain 如何流式输出？"
#   "stream 与 astream 的区别？"
#   "怎么实现 token-by-token 输出？"
# 都查一遍，合并去重
```

适合用户表述模糊或同义词多的场景。

### 2.5 SelfQueryRetriever（结构化过滤）

让 LLM 自动从 query 里抽出 metadata filter：

```python
from langchain.retrievers.self_query.base import SelfQueryRetriever
from langchain.chains.query_constructor.base import AttributeInfo

metadata_field_info = [
    AttributeInfo(name="year", description="发表年份", type="integer"),
    AttributeInfo(name="author", description="作者", type="string"),
    AttributeInfo(name="rating", description="评分 0-10", type="float"),
]

sq = SelfQueryRetriever.from_llm(
    llm=ChatOpenAI(model="gpt-4o-mini"),
    vectorstore=vs,
    document_contents="电影简介",
    metadata_field_info=metadata_field_info,
    verbose=True,
)

sq.invoke("2020 年后评分 8 分以上的科幻片")
# 自动构造 filter: year>2020 AND rating>=8 AND content~"科幻"
```

适合用户问题里有结构化条件的场景（电影/商品/简历）。

### 2.6 ParentDocumentRetriever（小切大用）

切小块用于检索（精确），返回大块用于回答（上下文）：

```python
from langchain.retrievers import ParentDocumentRetriever
from langchain.storage import InMemoryStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

child_splitter = RecursiveCharacterTextSplitter(chunk_size=200)
parent_splitter = RecursiveCharacterTextSplitter(chunk_size=2000)

retriever = ParentDocumentRetriever(
    vectorstore=vs,        # 存子 chunk 向量
    docstore=InMemoryStore(), # 存父 chunk
    child_splitter=child_splitter,
    parent_splitter=parent_splitter,
)
retriever.add_documents(docs)
```

### 2.7 ContextualCompressionRetriever（压缩 / 重排）

先粗检索 fetch_k=20，再用 reranker 精选：

```python
from langchain.retrievers import ContextualCompressionRetriever
from langchain.retrievers.document_compressors import LLMChainExtractor

compressor = LLMChainExtractor.from_llm(ChatOpenAI(model="gpt-4o-mini"))
retriever = ContextualCompressionRetriever(
    base_compressor=compressor,
    base_retriever=vs.as_retriever(search_kwargs={"k": 20}),
)
```

用 Cohere Reranker / BGE Reranker 效果更好：

```python
from langchain_cohere import CohereRerank
compressor = CohereRerank(model="rerank-multilingual-v3.0", top_n=4)
```

Reranker 是 RAG 提升精度最关键的一步，强烈推荐加上。

### 2.8 TimeWeightedVectorStoreRetriever

按时间衰减加权：

```python
from langchain.retrievers import TimeWeightedVectorStoreRetriever
retriever = TimeWeightedVectorStoreRetriever(
    vectorstore=vs,
    decay_rate=0.01,
    k=4,
)
```

适合新闻、动态资讯类。

---

## 3. 自定义 Retriever

继承 `BaseRetriever`，实现 `_get_relevant_documents`：

```python
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.callbacks import CallbackManagerForRetrieverRun

class MyRetriever(BaseRetriever):
    k: int = 5

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        rows = my_db.search(query, limit=self.k)
        return [Document(page_content=r["text"], metadata={"id": r["id"]}) for r in rows]
```

---

## 4. 完整 RAG 检索管线（推荐组合）

```
用户 query
   ↓
[改写] MultiQueryRetriever        ← 可选，扩多问
   ↓
[召回] EnsembleRetriever(BM25 + Vector)  ← 粗召回 20 条
   ↓
[过滤] SelfQueryRetriever 抽出的 filter  ← 可选
   ↓
[精排] ContextualCompressionRetriever (Reranker) ← 精排 4 条
   ↓
返回给 LLM
```

代码框架：

```python
ensemble = EnsembleRetriever(retrievers=[bm25, vs.as_retriever(search_kwargs={"k": 20})], weights=[0.4, 0.6])
mq = MultiQueryRetriever.from_llm(retriever=ensemble, llm=cheap_llm)
final = ContextualCompressionRetriever(base_compressor=reranker, base_retriever=mq)
```

---

## 5. Retriever + LCEL：构造 RAG chain

```python
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

def format(docs):
    return "\n\n".join(f"[{i}] {d.page_content}" for i, d in enumerate(docs))

prompt = ChatPromptTemplate.from_messages([
    ("system", "根据资料回答：\n{ctx}"),
    ("human", "{q}"),
])

rag = (
    {"ctx": retriever | format, "q": RunnablePassthrough()}
    | prompt
    | model
    | StrOutputParser()
)
rag.invoke("LCEL 是什么")
```

---

## 6. Retriever 评估（指标）

LangSmith / 自己实现：

- **Recall@k**：相关文档在 top-k 内的比例
- **MRR (Mean Reciprocal Rank)**：第一个相关文档的位置倒数
- **NDCG**：考虑排序质量
- **Precision@k**：top-k 里相关比例

第 17 篇 LangSmith Eval 会演示用 LangSmith 跑 retrieval evaluation。

---

## 7. 工程实践建议

1. **永远先 BM25 + Vector 混合**，单独向量在中文很多场景下不行
2. **加 Reranker**，质量提升明显（推荐 BGE-reranker-v2-m3 / Cohere）
3. **k 不要默认 4**，根据 chunk 大小、prompt 预算调整（典型 6-12）
4. **检索结果带 metadata 引用**，把 source 透给用户
5. **失败时 fallback**：检索 0 条时返回"未找到资料"而不是让 LLM 编

---

## 8. demo

```python
# demos/langchain/12_retrievers.py
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.retrievers import BM25Retriever
from langchain.retrievers import EnsembleRetriever
from langchain.retrievers.multi_query import MultiQueryRetriever

load_dotenv()

emb = OpenAIEmbeddings(model="text-embedding-3-small")
vs = Chroma(persist_directory="./chroma_kb", collection_name="lc", embedding_function=emb)

vector = vs.as_retriever(search_kwargs={"k": 6})

# BM25
all_docs = vs.get(include=["documents", "metadatas"])
from langchain_core.documents import Document
docs = [Document(page_content=t, metadata=m or {}) for t, m in zip(all_docs["documents"], all_docs["metadatas"])]
bm25 = BM25Retriever.from_documents(docs); bm25.k = 6

ensemble = EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6])
mq = MultiQueryRetriever.from_llm(retriever=ensemble, llm=ChatOpenAI(model="gpt-4o-mini"))

for d in mq.invoke("流式输出方法有哪些"):
    print("-", d.metadata.get("source"), d.page_content[:80])
```

---

## 9. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 检索召回好但答非所问 | LLM 看不懂资料 | 加 Reranker / 排序 |
| MultiQueryRetriever 慢且贵 | 多生成 3 query 都查 | 用 cheap_llm；或换条件触发 |
| SelfQuery 抽 filter 错误 | metadata 描述不清 | 完善 `AttributeInfo.description` |
| Ensemble 权重难调 | 经验值 | 用 LangSmith Eval 数据驱动调 |
| Reranker 太慢 | 模型大 | 用 BGE-reranker-base 或服务化部署 |

---

## 10. 本章 demo

[`demos/langchain/12_retrievers.py`](../../demos/langchain/12_retrievers.py)

下一篇：[13-rag.md](13-rag.md) — RAG 完整实战。
