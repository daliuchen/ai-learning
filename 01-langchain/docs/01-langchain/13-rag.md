# LangChain 13：RAG 完整实战

> **一句话**：把前面 11/12 篇组装起来，做一个从加载文档到对话式 RAG 的完整流水线，并演示一系列生产优化（混合检索、Reranker、引用来源、多轮记忆、流式）。

---

## 1. RAG 流程拆解

```
[原始文档]
   │  Loader
   ▼
[Document 列表]
   │  Splitter
   ▼
[Chunk 列表]
   │  Embeddings
   ▼
[向量化 chunk]
   │  VectorStore（持久化）
   ▼
[索引]                       ┌────────────────────────────┐
   │  Retriever              │ 用户提问                    │
   ▼                         │   │                        │
[相关 chunk] ← MultiQuery ← │   │                        │
   │   ↓                     │  Memory                    │
   │   重排                  │   │                        │
   ▼                         ▼                            │
[Prompt 拼接] ────────────────────────────────────────────┘
   │
   ▼
[ChatModel]
   │
   ▼
[OutputParser]
   │
   ▼
最终答案
```

我们分两层实现：

- **离线（建索引）**：Loader → Splitter → Embedding → VectorStore
- **在线（问答）**：Query → MultiQuery/Ensemble/Reranker → Prompt → LLM → Parser

---

## 2. 离线建索引

```python
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

def build_index():
    loader = DirectoryLoader(
        "./docs", glob="**/*.md",
        loader_cls=lambda p: TextLoader(p, encoding="utf-8"),
        show_progress=True,
    )
    docs = loader.load()
    chunks = RecursiveCharacterTextSplitter(
        chunk_size=600,
        chunk_overlap=80,
        separators=["\n\n", "\n", "。", "！", "？", " ", ""],
    ).split_documents(docs)
    Chroma.from_documents(
        chunks,
        OpenAIEmbeddings(model="text-embedding-3-small"),
        persist_directory="./rag_db",
        collection_name="kb",
    )
    print(f"index built: {len(chunks)} chunks")
```

把这一段做成命令行 `python build_index.py`，文档变化时重跑。

---

## 3. 在线 RAG Chain（v1：最简）

```python
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

emb = OpenAIEmbeddings(model="text-embedding-3-small")
vs = Chroma(persist_directory="./rag_db", collection_name="kb", embedding_function=emb)
retriever = vs.as_retriever(search_kwargs={"k": 4})

def format_docs(docs):
    return "\n\n".join(f"[{i+1}] {d.page_content}" for i, d in enumerate(docs))

prompt = ChatPromptTemplate.from_messages([
    ("system", "根据以下资料回答问题。如资料未提及，请直接说不知道。\n\n{ctx}"),
    ("human", "{q}"),
])
model = ChatOpenAI(model="gpt-4o-mini", temperature=0)

rag = (
    {"ctx": retriever | format_docs, "q": RunnablePassthrough()}
    | prompt | model | StrOutputParser()
)
print(rag.invoke("LCEL 是什么？"))
```

这就是一个可用的 RAG！但生产里我们要更稳。

---

## 4. v2：混合检索 + Reranker

```python
from langchain.retrievers import EnsembleRetriever, ContextualCompressionRetriever
from langchain_community.retrievers import BM25Retriever
# from langchain_cohere import CohereRerank   # 如果有 Cohere key
from langchain.retrievers.document_compressors import LLMChainExtractor
from langchain_core.documents import Document

# 1) BM25 + 向量混合
raw = vs.get(include=["documents", "metadatas"])
all_docs = [Document(page_content=t, metadata=m or {}) for t, m in zip(raw["documents"], raw["metadatas"])]
bm25 = BM25Retriever.from_documents(all_docs); bm25.k = 8
vector = vs.as_retriever(search_kwargs={"k": 8})
ensemble = EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6])

# 2) 精排（这里用 LLM 抽取，生产推荐用 reranker 模型）
compressor = LLMChainExtractor.from_llm(ChatOpenAI(model="gpt-4o-mini"))
retriever_v2 = ContextualCompressionRetriever(
    base_compressor=compressor,
    base_retriever=ensemble,
)
```

---

## 5. v3：带引用 / Citation

让模型回答时强制标 `[1][2]` 引用号：

```python
prompt = ChatPromptTemplate.from_messages([
    ("system",
     "根据以下资料回答问题，每个事实后用 [编号] 标注来源，仅引用真实编号。"
     "如资料未提及，请说"未在资料中提到"。\n\n资料：\n{ctx}"),
    ("human", "{q}"),
])

from langchain_core.runnables import RunnableParallel

def format_with_id(docs):
    return "\n\n".join(f"[{i+1}] (来自 {d.metadata.get('source', '?')})\n{d.page_content}" for i, d in enumerate(docs))

rag_with_cite = (
    RunnableParallel(
        ctx=retriever_v2 | format_with_id,
        q=RunnablePassthrough(),
        docs=retriever_v2,                    # 透传原始 docs 给前端展示
    )
    | RunnableParallel(
        answer=prompt | model | StrOutputParser(),
        docs=lambda x: x["docs"],
    )
)

out = rag_with_cite.invoke("流式 API 有哪些")
print(out["answer"])
for i, d in enumerate(out["docs"]):
    print(f"[{i+1}] {d.metadata.get('source')}")
```

输出像：

```
LangChain 提供 stream/astream [1]、astream_events [2]、astream_log [3] 四种流式 API。
[1] docs/01-langchain/06-streaming.md
[2] docs/01-langchain/06-streaming.md
[3] docs/01-langchain/06-streaming.md
```

---

## 6. v4：对话式 RAG（带历史）

朴素 RAG 单轮没问题，多轮会出问题：

> 用户：LCEL 是什么？
> 助手：（讲完）
> 用户：它和老版本有什么区别？

第二条 query "它和老版本有什么区别"单独检索时**完全找不到 LCEL 相关文档**。

**解决方案**：用 LLM 改写 query。

```python
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import MessagesPlaceholder

# 改写器
rewrite_prompt = ChatPromptTemplate.from_messages([
    ("system", "把用户最新提问改写成可独立检索的完整问题，无需新增信息。"),
    MessagesPlaceholder("history"),
    ("human", "{q}"),
])
rewriter = rewrite_prompt | ChatOpenAI(model="gpt-4o-mini") | StrOutputParser()

# 历史感知 retriever
def history_aware_retriever(inputs):
    if inputs["history"]:
        new_q = rewriter.invoke({"history": inputs["history"], "q": inputs["q"]})
    else:
        new_q = inputs["q"]
    return retriever_v2.invoke(new_q)

# 回答 prompt
answer_prompt = ChatPromptTemplate.from_messages([
    ("system", "你是助手，根据资料回答。\n\n资料：\n{ctx}"),
    MessagesPlaceholder("history"),
    ("human", "{q}"),
])

from langchain_core.runnables import RunnableLambda

conversational_rag = (
    RunnablePassthrough.assign(docs=RunnableLambda(history_aware_retriever))
    .assign(ctx=lambda x: format_docs(x["docs"]))
    | answer_prompt
    | model
    | StrOutputParser()
)
```

加上 `RunnableWithMessageHistory` 自动注入历史：

```python
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.chat_history import InMemoryChatMessageHistory

store = {}
def get_history(sid):
    return store.setdefault(sid, InMemoryChatMessageHistory())

bot = RunnableWithMessageHistory(
    conversational_rag,
    get_history,
    input_messages_key="q",
    history_messages_key="history",
)

cfg = {"configurable": {"session_id": "u1"}}
print(bot.invoke({"q": "LCEL 是什么？"}, config=cfg))
print(bot.invoke({"q": "它和老版本有什么区别？"}, config=cfg))
```

---

## 7. v5：流式 + 事件流

```python
async for ev in bot.astream_events({"q": "讲讲 LCEL"}, config=cfg, version="v2"):
    kind = ev["event"]
    if kind == "on_retriever_end":
        print(f"\n>> 检索到 {len(ev['data']['output'])} 段资料")
    elif kind == "on_chat_model_stream":
        print(ev["data"]["chunk"].content, end="", flush=True)
```

可以拿到"正在检索/正在回答"的细粒度状态，前端 UX 直接起飞。

---

## 8. 把整个 RAG 改用 LangGraph 实现

LangGraph 写 RAG 更适合做"多步检索 + 反思 + 工具调用"。简版：

```python
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

class State(TypedDict):
    question: str
    docs: list
    answer: str

def retrieve(s):
    return {"docs": retriever_v2.invoke(s["question"])}

def generate(s):
    chain = (lambda _: {"ctx": format_docs(s["docs"]), "q": s["question"]}) | prompt | model | StrOutputParser()
    return {"answer": chain.invoke({})}

g = StateGraph(State)
g.add_node("retrieve", retrieve)
g.add_node("generate", generate)
g.set_entry_point("retrieve")
g.add_edge("retrieve", "generate")
g.add_edge("generate", END)

app = g.compile()
app.invoke({"question": "LCEL 是什么"})
```

后续 LangGraph 章节会演示 **Self-RAG / Corrective RAG / Agentic RAG** 这些高级模式。

---

## 9. 评估 RAG 质量

用 LangSmith Eval（详见 17 篇），关键指标：

- **Retrieval Recall / Precision**：检索质量
- **Faithfulness**：答案是否忠实于资料（无幻觉）
- **Relevance**：答案是否切题
- **Context Relevance**：检索内容是否相关

经典开源工具 **RAGAS** 也很好用：

```python
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_recall
```

---

## 10. 工程清单

构建生产 RAG 必查清单：

- [ ] 文档源是否完整 + 增量更新机制
- [ ] Chunk 大小是否经验证（A/B 不同尺寸）
- [ ] BM25 + Vector 混合
- [ ] 加 Reranker（Cohere / BGE）
- [ ] 中文：embedding 用 bge-zh / OpenAI 3-large
- [ ] 多轮：query 改写
- [ ] 引用：强制 `[编号]` 输出 + 后端拼 source
- [ ] 兜底：检索 0 条 → 不让 LLM 编
- [ ] 缓存：相同 query 命中缓存
- [ ] 监控：LangSmith 上 trace + 评估指标
- [ ] 越界：用户问与知识库无关的问题如何处理（路由 LLM 兜底）

---

## 11. 本章 demo

[`demos/langchain/13_rag.py`](../../demos/langchain/13_rag.py)：单文件版完整 RAG（含构建索引、混合检索、对话式）。

下一篇：[14-callbacks.md](14-callbacks.md)
