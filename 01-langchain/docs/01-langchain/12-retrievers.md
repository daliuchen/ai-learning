# LangChain 12：Retrievers 检索器

> Retriever 是 LangChain 对「输入 query、返回相关文档」这件事的统一抽象，向量检索、全文检索、混合检索、Self-Query、压缩重排都只是它的不同实现。生产环境里 RAG 答得好不好，大部分取决于检索这一步怎么设计。

版本：以 `langchain 1.4`、`langchain-classic 1.0` 为准；Ensemble、MultiQuery 等高级检索器在 `langchain_classic.retrievers`，BM25 仍在 `langchain_community`。

---

## 1. 接口与最小用法

```python
class BaseRetriever:
    def invoke(self, query: str, config=None) -> List[Document]: ...
    async def ainvoke(self, query: str, config=None) -> List[Document]: ...
```

`Retriever` 实现了 `Runnable`，可以直接接进 LCEL：

```python
retriever = vs.as_retriever(search_kwargs={"k": 3})
chain = retriever | format_docs | prompt | model | parser
```

---

## 2. 常用的 Retriever 类型

### 2.1 VectorStoreRetriever

最常用的一种，由 VectorStore 直接转出来：

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

不需要 Embedding，擅长精确的关键词匹配。

### 2.3 EnsembleRetriever（混合检索）

把多个 retriever 的结果按权重融合：

```python
from langchain_classic.retrievers import EnsembleRetriever

ensemble = EnsembleRetriever(
    retrievers=[bm25, vector_retriever],
    weights=[0.4, 0.6],
)
```

内部用 RRF（Reciprocal Rank Fusion）算法合并排名。实际项目里最稳的方案就是 BM25 + 向量的 Ensemble。

### 2.4 MultiQueryRetriever（多查询扩展）

用 LLM 把一个 query 改写成几个，分别检索后再合并：

```python
from langchain_classic.retrievers.multi_query import MultiQueryRetriever

mq = MultiQueryRetriever.from_llm(
    retriever=vector_retriever,
    llm=ChatOpenAI(model="gpt-6-luna"),
)
mq.invoke("如何流式输出？")
# 内部生成 3 个改写问题：
#   "LangChain 如何流式输出？"
#   "stream 与 astream 的区别？"
#   "怎么实现 token-by-token 输出？"
# 都查一遍，合并去重
```

用户表述模糊、同义说法多的时候用它。

### 2.5 SelfQueryRetriever（结构化过滤）

让 LLM 从 query 里自动抽出 metadata filter：

```python
from langchain_classic.retrievers.self_query.base import SelfQueryRetriever
from langchain_classic.chains.query_constructor.base import AttributeInfo

metadata_field_info = [
    AttributeInfo(name="year", description="发表年份", type="integer"),
    AttributeInfo(name="author", description="作者", type="string"),
    AttributeInfo(name="rating", description="评分 0-10", type="float"),
]

sq = SelfQueryRetriever.from_llm(
    llm=ChatOpenAI(model="gpt-6-luna"),
    vectorstore=vs,
    document_contents="电影简介",
    metadata_field_info=metadata_field_info,
    verbose=True,
)

sq.invoke("2020 年后评分 8 分以上的科幻片")
# 自动构造 filter: year>2020 AND rating>=8 AND content~"科幻"
```

适合问题里带结构化条件的场景，比如电影、商品、简历。

### 2.6 ParentDocumentRetriever（小切大用）

用小块做检索，命中更精确；返回所在的大块给模型，上下文更完整：

```python
from langchain_classic.retrievers import ParentDocumentRetriever
from langchain_classic.storage import InMemoryStore
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

先粗检索 20 条，再用压缩器精选。下面的 `LLMChainExtractor` 让 LLM 从每篇文档里抽出和问题相关的片段：

```python
from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_classic.retrievers.document_compressors import LLMChainExtractor

compressor = LLMChainExtractor.from_llm(ChatOpenAI(model="gpt-6-luna"))
retriever = ContextualCompressionRetriever(
    base_compressor=compressor,
    base_retriever=vs.as_retriever(search_kwargs={"k": 20}),
)
```

换成 Cohere Reranker 或 BGE Reranker 效果更好：

```python
from langchain_cohere import CohereRerank
compressor = CohereRerank(model="rerank-v3.5", top_n=4)
```

在我看来，Reranker 是提升 RAG 精度收益最大的一步，建议默认加上。

### 2.8 TimeWeightedVectorStoreRetriever

按时间衰减加权：

```python
from langchain_classic.retrievers import TimeWeightedVectorStoreRetriever
retriever = TimeWeightedVectorStoreRetriever(
    vectorstore=vs,
    decay_rate=0.01,
    k=4,
)
```

适合新闻、资讯这类时效性强的内容。

---

### 2.9 把检索器变成 agent 的工具（1.x 的 Agentic RAG）

1.x 更推荐的做法是不再写死「检索 → 回答」，而是把检索器包成工具交给 agent，让模型自己决定要不要检索、检索几次、用什么 query：

```python
from langchain_core.tools import create_retriever_tool
from langchain.agents import create_agent

kb_tool = create_retriever_tool(
    vs.as_retriever(search_kwargs={"k": 3}),
    name="search_kb",
    description="检索 LangChain 知识库，回答技术问题前先调用它。",
)
agent = create_agent(model, tools=[kb_tool], system_prompt="先检索再回答；资料里没有就说不知道。")
```

[LangChain 13：RAG 完整实战](./13-rag.md) 和 [实战项目 1：RAG 知识库问答 Agent](../04-comparison/02-project-rag-agent.md) 会完整展开。

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

- Recall@k：相关文档落在 top-k 内的比例
- MRR（Mean Reciprocal Rank）：第一个相关文档排名的倒数
- NDCG：把排序先后也算进去
- Precision@k：top-k 里相关文档的比例

LangSmith 03 篇（Evaluation）会演示怎么用 LangSmith 跑检索评估。

---

## 7. 工程实践建议

1. 先上 BM25 + 向量混合检索。中文场景下只靠向量，很多时候效果不够。
2. 加 Reranker，质量提升明显，可以用 BGE-reranker-v2-m3 或 Cohere。
3. k 别直接用默认的 4，按 chunk 大小和 prompt 预算调整，一般在 6 到 12 之间。
4. 检索结果带上 metadata，把 source 作为引用给用户看。
5. 准备兜底：检索结果为 0 条时直接返回「未找到资料」，别让 LLM 自己编。

---

## 8. 本章 demo

完整代码：[`demos/langchain/12_retrievers.py`](../../demos/langchain/12_retrievers.py)

```python
# demos/langchain/12_retrievers.py
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever
from langchain_classic.retrievers.multi_query import MultiQueryRetriever

load_dotenv()

emb = OpenAIEmbeddings(model="text-embedding-3-small")
vs = Chroma(persist_directory="./chroma_kb", collection_name="lc_demo", embedding_function=emb)

vector = vs.as_retriever(search_kwargs={"k": 6})

# BM25
all_docs = vs.get(include=["documents", "metadatas"])
from langchain_core.documents import Document
docs = [Document(page_content=t, metadata=m or {}) for t, m in zip(all_docs["documents"], all_docs["metadatas"])]
bm25 = BM25Retriever.from_documents(docs); bm25.k = 6

ensemble = EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6])
mq = MultiQueryRetriever.from_llm(retriever=ensemble, llm=ChatOpenAI(model="gpt-6-luna"))

for d in mq.invoke("流式输出方法有哪些"):
    print("-", d.metadata.get("source"), d.page_content[:80])
```

---

## 9. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| `from langchain.retrievers import EnsembleRetriever` → `ModuleNotFoundError` | 1.x 已移走 | 改 `langchain_classic.retrievers` |
| BM25Retriever 报 `rank_bm25` 缺失 | 可选依赖 | `pip install rank-bm25` |
| 召回不错但答非所问 | 有用的资料没排在前面，LLM 抓不住重点 | 加 Reranker 重新排序 |
| MultiQueryRetriever 慢且贵 | 额外生成 3 个 query，每个都要查 | 改写用便宜模型；或只在特定条件下触发 |
| SelfQuery 抽 filter 错误 | metadata 描述不清 | 完善 `AttributeInfo.description` |
| Ensemble 权重难调 | 权重全凭经验 | 用 LangSmith Eval 的数据来调 |
| Reranker 太慢 | 模型大 | 用 BGE-reranker-base 或服务化部署 |

---

下一篇：[LangChain 13：RAG 完整实战](13-rag.md)
