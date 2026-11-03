# LangChain 13：RAG 完整实战

> 这篇把第 10 到 12 篇的零件拼起来，从加载文档一路做到对话式 RAG。先写一个最简单能跑的版本，再逐步加上混合检索、Reranker、引用来源、多轮记忆和流式输出，每一步解决一个上一版的具体问题。

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

实现上分成两部分：

- 离线建索引：Loader → Splitter → Embedding → VectorStore
- 在线问答：Query → MultiQuery/Ensemble/Reranker → Prompt → LLM → Parser

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
        collection_name="rag_kb",
    )
    print(f"index built: {len(chunks)} chunks")
```

把这段做成命令行脚本 `python build_index.py`，文档有变化就重跑一次。

---

## 3. 在线 RAG Chain（v1：最简）

```python
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

emb = OpenAIEmbeddings(model="text-embedding-3-small")
vs = Chroma(persist_directory="./rag_db", collection_name="rag_kb", embedding_function=emb)
retriever = vs.as_retriever(search_kwargs={"k": 4})

def format_docs(docs):
    return "\n\n".join(f"[{i+1}] {d.page_content}" for i, d in enumerate(docs))

prompt = ChatPromptTemplate.from_messages([
    ("system", "根据以下资料回答问题。如资料未提及，请直接说不知道。\n\n{ctx}"),
    ("human", "{q}"),
])
model = ChatOpenAI(model="gpt-6-luna", temperature=0)

rag = (
    {"ctx": retriever | format_docs, "q": RunnablePassthrough()}
    | prompt | model | StrOutputParser()
)
print(rag.invoke("LCEL 是什么？"))
```

到这里已经是一个能用的 RAG 了，不过离上生产还差几步。

---

## 4. v2：混合检索 + Reranker

```python
from langchain_classic.retrievers import EnsembleRetriever, ContextualCompressionRetriever
from langchain_community.retrievers import BM25Retriever
# from langchain_cohere import CohereRerank   # 如果有 Cohere key
from langchain_classic.retrievers.document_compressors import LLMChainExtractor
from langchain_core.documents import Document

# 1) BM25 + 向量混合
raw = vs.get(include=["documents", "metadatas"])
all_docs = [Document(page_content=t, metadata=m or {}) for t, m in zip(raw["documents"], raw["metadatas"])]
bm25 = BM25Retriever.from_documents(all_docs); bm25.k = 8
vector = vs.as_retriever(search_kwargs={"k": 8})
ensemble = EnsembleRetriever(retrievers=[bm25, vector], weights=[0.4, 0.6])

# 2) 精排（这里用 LLM 抽取，生产推荐用 reranker 模型）
compressor = LLMChainExtractor.from_llm(ChatOpenAI(model="gpt-6-luna"))
retriever_v2 = ContextualCompressionRetriever(
    base_compressor=compressor,
    base_retriever=ensemble,
)
```

---

## 5. v3：带引用 / Citation

要求模型回答时标上 `[1][2]` 这样的引用编号：

```python
prompt = ChatPromptTemplate.from_messages([
    ("system",
     "根据以下资料回答问题，每个事实后用 [编号] 标注来源，仅引用真实编号。"
     "如资料未提及，请说「未在资料中提到」。\n\n资料：\n{ctx}"),
    ("human", "{q}"),
])

from langchain_core.runnables import RunnableParallel

def format_with_id(docs):
    return "\n\n".join(f"[{i+1}] (来自 {d.metadata.get('source', '?')})\n{d.page_content}" for i, d in enumerate(docs))

rag_with_cite = (
    RunnableParallel(docs=retriever_v2, q=RunnablePassthrough())   # 只检索一次
    | RunnablePassthrough.assign(ctx=lambda x: format_with_id(x["docs"]))
    | RunnableParallel(
        answer=prompt | model | StrOutputParser(),
        docs=lambda x: x["docs"],             # 透传原始 docs 给前端展示
    )
)

out = rag_with_cite.invoke("流式 API 有哪些")
print(out["answer"])
for i, d in enumerate(out["docs"]):
    print(f"[{i+1}] {d.metadata.get('source')}")
```

检索只做一次，编号和透传给前端的 `docs` 才能一一对应。如果 `ctx` 和 `docs` 各调一次 `retriever_v2`，两次结果可能不同（这里的压缩器本身就要调 LLM），引用编号就会对错文档。

输出像：

```
LangChain 提供 stream/astream [1]、astream_events [2]、astream_log [3] 三种流式 API。
[1] docs/01-langchain/06-streaming.md
[2] docs/01-langchain/06-streaming.md
[3] docs/01-langchain/06-streaming.md
```

---

## 6. v4：对话式 RAG（带历史）

上面的写法单轮问答没问题，多轮就会出错：

> 用户：LCEL 是什么？
> 助手：（讲完）
> 用户：它和老版本有什么区别？

第二个问题「它和老版本有什么区别」拿去单独检索，**根本找不到和 LCEL 相关的文档**，因为里面没出现 LCEL 这个词。

解决办法是先用 LLM 结合历史把 query 改写成完整的问题。

```python
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.prompts import MessagesPlaceholder

# 改写器
rewrite_prompt = ChatPromptTemplate.from_messages([
    ("system", "把用户最新提问改写成可独立检索的完整问题，无需新增信息。"),
    MessagesPlaceholder("history"),
    ("human", "{q}"),
])
rewriter = rewrite_prompt | ChatOpenAI(model="gpt-6-luna") | StrOutputParser()

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

再用 `RunnableWithMessageHistory` 自动注入历史。它在 1.x 里仍可用；如果改成 agent 形态，就换成 checkpointer，见第 8 节：

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

这样能拿到「正在检索」「正在回答」这类细粒度状态，前端可以据此显示进度，用户不用对着空白干等。

---

## 8. Agentic RAG：把检索变成工具（1.x 推荐的第二种写法）

前面的链永远是先检索再回答。如果有的问题根本不需要检索（比如寒暄），有的要检索好几次（复合问题），可以把检索器包成工具交给 `create_agent`，让模型自己决定：

```python
from langchain_core.tools import create_retriever_tool
from langchain.agents import create_agent

kb = create_retriever_tool(
    retriever_v2,
    name="search_kb",
    description="检索 LangChain 知识库。回答技术问题前先调用，query 要独立完整。",
)
agent = create_agent(
    model,
    tools=[kb],
    system_prompt="先检索再回答，答案要基于检索资料；资料里没有就说不知道，不要编。",
    # 多轮记忆：直接挂 checkpointer，不需要手写 query 改写（模型自己会把"它"补全成完整 query）
)
out = agent.invoke({"messages": [{"role": "user", "content": "LCEL 是什么？"}]})
print(out["messages"][-1].text)
```

第 6 节手写的 query 改写和历史感知 retriever，在 agent 形态下由模型自己完成：它调用工具时传的 `query` 就是改写好的独立问题。代价是多一轮模型调用，行为也不如固定链好预测。所以检索流程固定的场景，我还是会用前面的链。

---

## 9. 把整个 RAG 改用 LangGraph 实现

如果 RAG 需要多步检索、自我检查、调用其他工具，用 LangGraph 写更合适。先看最简版本：

```python
from langgraph.graph import StateGraph, START, END
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
g.add_edge(START, "retrieve")
g.add_edge("retrieve", "generate")
g.add_edge("generate", END)

app = g.compile()
app.invoke({"question": "LCEL 是什么"})
```

Self-RAG、Corrective RAG 这类带自检和重试的模式，就是在这张图上加条件边和回路，写法参考 LangGraph 02 篇（条件边）和 04 篇（手写循环），本书不单独展开。

---

## 10. 评估 RAG 质量

可以用 LangSmith Eval（详见 LangSmith 03 篇），主要看这几个指标：

- Retrieval Recall / Precision：检索质量
- Faithfulness：答案是否忠实于资料，有没有幻觉
- Relevance：答案是否切题
- Context Relevance：检索到的内容是否相关

开源的 RAGAS 也很常用：

```python
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_recall
```

---

## 11. 工程清单

上线前可以对着这张清单逐项检查：

- [ ] 文档源是否完整 + 增量更新机制
- [ ] Chunk 大小是否经验证（A/B 不同尺寸）
- [ ] BM25 + Vector 混合
- [ ] 加 Reranker（Cohere / BGE / 通义 gte-rerank 系列）
- [ ] 中文：embedding 用 bge-zh / 通义 text-embedding-v4 / OpenAI 3-large
- [ ] 多轮：query 改写
- [ ] 引用：强制 `[编号]` 输出 + 后端拼 source
- [ ] 兜底：检索 0 条 → 不让 LLM 编
- [ ] 缓存：相同 query 命中缓存
- [ ] 监控：LangSmith 上 trace + 评估指标
- [ ] 越界：用户问与知识库无关的问题如何处理（路由 LLM 兜底）

---

## 12. 本章 demo

[`demos/langchain/13_rag.py`](../../demos/langchain/13_rag.py)：单文件版完整 RAG（含构建索引、混合检索、对话式）。

下一篇：[LangChain 14：Callbacks 回调系统](14-callbacks.md)
