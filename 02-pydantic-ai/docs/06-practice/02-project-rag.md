# Pydantic AI 实战 02：RAG 知识库问答 Agent

> 这一篇用 Pydantic AI、embedding 和向量库搭一个回答时会给出文档出处的问答 Agent，再用 Pydantic Evals 评测它。流程从头走到尾：文档加载 → 切块 → 向量化 → 入库 → 检索工具 → Agent 装配 → 评测 → 部署。

以 `pydantic-ai 2.64.0` 为准。

---

## 1. 项目目标

输入：一批 Markdown / PDF 文档（如内部 wiki、产品手册、API 文档）。

输出：一个对话式 Agent，要求做到：

1. 准确回答文档里的问题
2. 附带引用片段（哪一段文档、哪一行、哪一页）
3. 文档里没说的问题，明确告诉用户，不要编
4. 通过自动评测验证准确率

技术栈：

```
Pydantic AI         ← Agent 框架
pydantic_ai.Embedder  ← Embedding（千问 text-embedding-v4 / OpenAI / 本地 sentence-transformers 可选）
ChromaDB            ← 向量库（也支持 FAISS）
pypdf + markdown    ← 文档解析
Pydantic Evals      ← 评测
FastAPI             ← Web 部署
Logfire             ← 可观测
```

---

## 2. 架构图

```
┌──────────────────────────────────────────────────────────┐
│                    离线建索引（一次性）                    │
└──────────────────────────────────────────────────────────┘
   docs/ (md, pdf)
       │
       ▼
  ┌─────────────┐    ┌──────────┐    ┌────────────┐
  │  Loader     │ →  │ Splitter │ →  │ Embedder   │
  │ (pypdf/md)  │    │ (固定块) │    │ (sbert/OAI)│
  └─────────────┘    └──────────┘    └────────────┘
                                          │
                                          ▼
                                     ┌──────────┐
                                     │ ChromaDB │
                                     └──────────┘

┌──────────────────────────────────────────────────────────┐
│                     在线问答（每次调用）                  │
└──────────────────────────────────────────────────────────┘
   用户问题
       │
       ▼
   ┌──────────────┐
   │ Pydantic AI  │
   │   Agent      │ ← instructions: "必须先调 retrieve 再回答"
   └──────────────┘
       │
       │  @agent.tool retrieve(query) → 取 top-k
       ▼
   ┌──────────────┐
   │  ChromaDB    │ ← 向量检索
   └──────────────┘
       │
       ▼  返回 chunks + 来源
   Agent 综合 → output_type=Answer(text, sources)
       │
       ▼
   {answer: "...", sources: [{path, page}, ...]}
```

---

## 3. 准备：文档加载与切块

### 3.1 加载

```python
# rag/loader.py
from pathlib import Path
from dataclasses import dataclass

@dataclass
class Document:
    text: str
    source: str       # 例如 "docs/foo.md" 或 "manual.pdf#page=3"

def load_markdown(path: Path) -> list[Document]:
    text = path.read_text(encoding="utf-8")
    return [Document(text=text, source=str(path))]

def load_pdf(path: Path) -> list[Document]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    docs: list[Document] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            docs.append(Document(text=text, source=f"{path}#page={i+1}"))
    return docs

def load_directory(root: Path) -> list[Document]:
    docs: list[Document] = []
    for p in root.rglob("*"):
        if p.suffix.lower() == ".md":
            docs.extend(load_markdown(p))
        elif p.suffix.lower() == ".pdf":
            docs.extend(load_pdf(p))
    return docs
```

每个 PDF 一页一条 `Document`，把页码写进 `source`，后面引用时就能精确到页。

### 3.2 切块（自写一个简单的 splitter）

```python
# rag/splitter.py
from rag.loader import Document

def split_text(text: str, chunk_size: int = 600, overlap: int = 80) -> list[str]:
    """按字符数切，保留 overlap。简单但够用。"""
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks

def split_documents(docs: list[Document], chunk_size: int = 600, overlap: int = 80) -> list[Document]:
    out: list[Document] = []
    for d in docs:
        for chunk in split_text(d.text, chunk_size, overlap):
            out.append(Document(text=chunk, source=d.source))
    return out
```

> 实际项目里可以用 `langchain-text-splitters` 的 `RecursiveCharacterTextSplitter`，它按段落和句子边界切，效果比按字符数好。Pydantic AI 没有自带 splitter，借用 LangChain 的很常见。

---

## 4. Embedding 与入库

### 4.1 Embedding：直接用 `pydantic_ai.Embedder`

```python
# rag/embedder.py
import os
from pydantic_ai import Embedder
from pydantic_ai.embeddings import TestEmbeddingModel

EMBED_MODEL = os.getenv("QWEN_EMBED_MODEL", "text-embedding-v4")

def make_embedder() -> Embedder:
    if os.getenv("OPENAI_API_KEY"):
        return Embedder(f"openai:{EMBED_MODEL}")           # 千问（OPENAI_BASE_URL 指向 DashScope 兼容端点）；官方 OpenAI 用 text-embedding-3-small
    return Embedder(TestEmbeddingModel(dimensions=32))     # 离线：确定性伪向量，只演示流程，检索无语义
    # 本地免费：Embedder("sentence-transformers:BAAI/bge-small-zh-v1.5")  （需要 pip install "pydantic-ai-slim[sentence-transformers]"，会装 torch）
```

`Embedder` 的接口是 `embed_documents(list[str])` / `embed_query(str)`（各有 `_sync` 版本），返回对象的 `.embeddings` 是向量列表。文档侧和查询侧要分开调用。工具函数跑在事件循环里，所以检索要用异步版 `await embedder.embed_query(...)`。在工具里调 `embed_query_sync` 会报 `RuntimeError: This event loop is already running`。

### 4.2 ChromaDB 入库

这里用 ChromaDB 写，是因为它的 collection、upsert、query 几个概念最直观，生产上也能直接用。本章 demo 为了免装依赖，换成了一个约 30 行的内存余弦索引加 JSON 落盘，对外接口（`add` / `search`）和下面这个类一样，所以两者可以直接互换，换向量库只需要改 `VectorStore` 这一个类。

```python
# rag/store.py
import hashlib
import chromadb
from chromadb.config import Settings
from rag.loader import Document
from rag.embedder import Embedder

class VectorStore:
    def __init__(self, embedder: Embedder, persist_dir: str = "./chroma_db",
                 collection_name: str = "kb"):
        self.embedder = embedder
        client = chromadb.PersistentClient(path=persist_dir,
                                           settings=Settings(anonymized_telemetry=False))
        # 注意：embedding_function=None，我们自己算 embedding 喂进去
        self.col = client.get_or_create_collection(name=collection_name,
                                                   embedding_function=None)

    def add(self, docs: list[Document]) -> None:
        if not docs:
            return
        texts = [d.text for d in docs]
        embs = [list(v) for v in self.embedder.embed_documents_sync(texts).embeddings]   # 建索引在循环外，可用 sync 版
        # 用 source + 内容算稳定 id：Python 内置 hash() 对 str 每个进程都不一样，重建索引会产生重复条目
        ids = [hashlib.sha1(f"{d.source}\n{d.text}".encode()).hexdigest()[:16] for d in docs]
        metas = [{"source": d.source} for d in docs]
        self.col.upsert(ids=ids, documents=texts, embeddings=embs, metadatas=metas)

    async def search(self, query: str, k: int = 4) -> list[Document]:     # 被 async 工具调用，必须是 async
        q_emb = list((await self.embedder.embed_query(query)).embeddings[0])
        res = self.col.query(query_embeddings=[q_emb], n_results=k)
        out: list[Document] = []
        for txt, meta in zip(res["documents"][0], res["metadatas"][0]):
            out.append(Document(text=txt, source=meta["source"]))
        return out
```

---

## 5. retrieval 工具与 Agent 装配

### 5.1 业务输出模型

```python
# rag/agent.py
from pydantic import BaseModel, Field

class SourceCite(BaseModel):
    """一条引用。"""
    source: str = Field(description="文档路径或 'path#page=N'")
    snippet: str = Field(description="原文片段，<= 200 字")

class Answer(BaseModel):
    """RAG Agent 的最终回答。"""
    text: str = Field(description="对用户问题的回答，必须基于检索内容。")
    sources: list[SourceCite] = Field(description="引用的文档来源，至少 1 条；若文档里没说，sources=[] 且 text 明确告知用户")
    confidence: float = Field(description="自评信心 0-1", ge=0, le=1)
```

`Answer` 强制 Agent 给出引用，这是整个项目里**最要紧的一步**。没有这个类型约束，模型经常忘了给引用。

### 5.2 Agent + retrieve 工具

```python
# rag/agent.py（续）
from dataclasses import dataclass
from pydantic_ai import Agent, RunContext
from rag.store import VectorStore

@dataclass
class RagDeps:
    store: VectorStore
    top_k: int = 4

SYSTEM = """你是一个严谨的文档问答助手。

回答规则：
1. 必须先调用 retrieve 工具检索相关文档，**禁止凭印象回答**。
2. 回答时引用检索结果中的内容，把对应来源放进 sources 字段。
3. 如果检索结果里没有答案，text 明确告知"文档里未提及"，sources=[]，confidence<0.3。
4. 一次回答最多 3 条来源，每条 snippet 不超过 200 字。
"""

rag_agent = Agent[RagDeps, Answer](
    "openai-chat:qwen-flash",
    deps_type=RagDeps,
    output_type=Answer,
    instructions=SYSTEM,
)

@rag_agent.tool
async def retrieve(ctx: RunContext[RagDeps], query: str) -> list[dict]:
    """从知识库检索相关文档片段。

    参数：
        query: 用自然语言描述要搜的内容。
    返回：
        chunks: 一个数组，每条含 source 和 text。
    """
    docs = await ctx.deps.store.search(query, k=ctx.deps.top_k)     # search 是 async（内部 await embed_query）
    return [{"source": d.source, "text": d.text} for d in docs]
```

几个细节：

- `Agent[RagDeps, Answer]` 显式标泛型，IDE 跳转 / mypy 都能识别 `ctx.deps.store`
- `retrieve` 返回 `list[dict]` 而不是字符串，让模型看到结构化数据
- instructions 里写明先 retrieve 再回答，否则模型会跳过检索

### 5.3 一次问答

```python
# rag/run.py
from rag.embedder import make_embedder
from rag.store import VectorStore
from rag.agent import rag_agent, RagDeps

def ask(question: str) -> None:
    store = VectorStore(make_embedder())
    deps = RagDeps(store=store, top_k=4)
    result = rag_agent.run_sync(question, deps=deps)
    ans = result.output
    print(f"答：{ans.text}")
    print(f"信心：{ans.confidence:.2f}")
    for i, c in enumerate(ans.sources, 1):
        print(f"  [{i}] {c.source}")
        print(f"      ↳ {c.snippet[:120]}…")

ask("Pydantic AI 是什么？")
```

---

## 6. 离线建索引

```python
# rag/build_index.py
from pathlib import Path
from rag.loader import load_directory
from rag.splitter import split_documents
from rag.embedder import make_embedder
from rag.store import VectorStore

def build_index(docs_dir: str = "./docs", persist: str = "./chroma_db") -> None:
    print(f"📚 加载 {docs_dir}…")
    raw = load_directory(Path(docs_dir))
    print(f"   共 {len(raw)} 个文档")

    print("✂️  切块…")
    chunks = split_documents(raw, chunk_size=600, overlap=80)
    print(f"   共 {len(chunks)} 个块")

    print("🔢 Embedding + 入库…")
    store = VectorStore(make_embedder(), persist_dir=persist)
    store.add(chunks)
    print("✅ 完成")

if __name__ == "__main__":
    build_index()
```

跑 `python -m rag.build_index` 一次性建好。

---

## 7. 评测：Pydantic Evals

光跑通不够，还要量化准确率。用 `pydantic-evals`（2.64；`LLMJudge` 的 `model=` 可以填 `"openai-chat:qwen-flash"`）：

```python
# rag/eval.py
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import LLMJudge, Evaluator, EvaluatorContext
from rag.agent import rag_agent, RagDeps, Answer
from rag.store import VectorStore
from rag.embedder import make_embedder

# 1. 构造评测用例
cases = [
    Case(
        name="basic_intro",
        inputs="Pydantic AI 是什么？",
        expected_output="一个 Python Agent 框架",
        metadata={"category": "概念题"},
    ),
    Case(
        name="not_in_docs",
        inputs="奥运会 2032 年在哪举办？",
        expected_output="文档里未提及",
        metadata={"category": "拒答题"},
    ),
]

# 2. 自定义评测器：必须给 sources
class HasSourcesEvaluator(Evaluator[str, Answer]):
    def evaluate(self, ctx: EvaluatorContext[str, Answer]) -> dict:
        ans = ctx.output
        return {
            "has_sources": len(ans.sources) > 0,
            "n_sources": len(ans.sources),
        }

# 3. 跑评测
async def run_eval():
    store = VectorStore(make_embedder())
    deps = RagDeps(store=store)

    async def task(question: str) -> Answer:
        result = await rag_agent.run(question, deps=deps)
        return result.output

    dataset = Dataset(cases=cases, evaluators=[
        HasSourcesEvaluator(),
        LLMJudge(rubric="回答是否准确反映了文档内容？", model="openai-chat:qwen-flash"),
    ])
    report = await dataset.evaluate(task)
    report.print(include_input=True, include_output=True)
```

运行后能看到每条 case 的得分，可以拿来做回归。

---

## 8. 进阶 1：Rerank 提升精度

向量召回 top-20，再用更强的模型重排到 top-3（运行这段需要下载本地的 CrossEncoder 模型）：

```python
# rag/rerank.py
from sentence_transformers import CrossEncoder
from rag.loader import Document

class Reranker:
    def __init__(self, model: str = "BAAI/bge-reranker-base"):
        self.ce = CrossEncoder(model)

    def rerank(self, query: str, docs: list[Document], top_k: int = 3) -> list[Document]:
        pairs = [(query, d.text) for d in docs]
        scores = self.ce.predict(pairs)
        ranked = sorted(zip(docs, scores), key=lambda x: -x[1])
        return [d for d, _ in ranked[:top_k]]
```

把它插到 `retrieve` 工具里：

```python
@rag_agent.tool
async def retrieve(ctx: RunContext[RagDeps], query: str) -> list[dict]:
    docs = await ctx.deps.store.search(query, k=20)        # 召回 20
    docs = ctx.deps.reranker.rerank(query, docs, top_k=3)  # 重排 3
    return [{"source": d.source, "text": d.text} for d in docs]
```

---

## 9. 进阶 2：混合检索（BM25 + 向量）

纯向量在专有名词、代码符号上会漏。加 BM25 关键词检索做并集：

```python
# rag/hybrid.py
from rank_bm25 import BM25Okapi
from rag.loader import Document

class HybridStore:
    def __init__(self, vector_store, all_docs: list[Document]):
        self.vector_store = vector_store
        self.all_docs = all_docs
        tokens = [d.text.split() for d in all_docs]
        self.bm25 = BM25Okapi(tokens)

    async def search(self, query: str, k: int = 4) -> list[Document]:
        vec = await self.vector_store.search(query, k=k)      # VectorStore.search 是 async
        bm25_scores = self.bm25.get_scores(query.split())
        top_idx = sorted(range(len(bm25_scores)),
                         key=lambda i: -bm25_scores[i])[:k]
        bm = [self.all_docs[i] for i in top_idx]
        # 去重 + 取并集
        seen = set()
        merged = []
        for d in vec + bm:
            key = (d.source, d.text[:50])
            if key not in seen:
                seen.add(key)
                merged.append(d)
        return merged[:k * 2]
```

---

## 10. 进阶 3：精确引用定位

只给 `source` 还不够，最好能高亮原文出现的位置。在 chunk 元数据里多存一份偏移量：

```python
# 切块时
def split_with_offset(text: str, source: str, chunk_size: int = 600):
    chunks = []
    for start in range(0, len(text), chunk_size - 80):
        chunks.append({
            "text": text[start:start + chunk_size],
            "source": source,
            "char_start": start,
            "char_end": min(start + chunk_size, len(text)),
        })
    return chunks
```

Agent 输出的 `SourceCite` 可以扩展：

```python
class SourceCite(BaseModel):
    source: str
    snippet: str
    char_start: int | None = None
    char_end: int | None = None
```

前端拿到后直接跳转到原文那段。

---

## 11. 生产部署：FastAPI

```python
# rag/web.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from pydantic import BaseModel
from rag.agent import rag_agent, RagDeps, Answer
from rag.store import VectorStore
from rag.embedder import make_embedder

class AskRequest(BaseModel):
    question: str

# 全局共享 store，省得每个请求都 reload embedding model
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = VectorStore(make_embedder())
    yield

app = FastAPI(lifespan=lifespan)

@app.post("/ask", response_model=Answer)
async def ask(req: AskRequest):
    deps = RagDeps(store=app.state.store)
    result = await rag_agent.run(req.question, deps=deps)
    return result.output
```

跑：

```bash
uvicorn rag.web:app --host 0.0.0.0 --port 8000
curl -X POST http://localhost:8000/ask \
     -H "Content-Type: application/json" \
     -d '{"question": "Pydantic AI 怎么定义工具？"}'
```

### 11.1 流式版

```python
from fastapi.responses import StreamingResponse

@app.post("/ask/stream")
async def ask_stream(req: AskRequest):
    deps = RagDeps(store=app.state.store)

    async def gen():
        async with rag_agent.run_stream(req.question, deps=deps) as stream:
            # rag_agent 的 output_type 是结构化的 Answer：流式用 stream_output() 拿部分填充的对象，而不是 stream_text
            async for partial in stream.stream_output():
                yield f"data: {partial.model_dump_json()}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
```

---

## 12. 接 Logfire 可观测

```python
import logfire
logfire.configure()
logfire.instrument_pydantic_ai()
```

每条问答跑完，可以在 Logfire 里看到：

- retrieve 命中了哪些 chunks
- 模型重试了几次
- 总 token 数 / cost
- 失败原因

给每次运行带上 `metadata={"user_id": ...}`（开着 instrumentation 时会写进 span 属性），在 Logfire 里按它过滤，把回答差的样本入 dataset 做下一轮 eval；线上也可以用 `OnlineEvaluation` capability 抽样评估（见[Pydantic Evals 评测框架](../04-modules/02-evals.md)）。

---

## 13. 与 LangChain RAG 对比

LangChain 的经典 RAG 写法：

```python
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

vs = Chroma(persist_directory="./db", embedding_function=OpenAIEmbeddings())
retriever = vs.as_retriever(search_kwargs={"k": 4})

template = "基于文档回答：\n{context}\n\n问题：{question}"
prompt = ChatPromptTemplate.from_template(template)
chain = (
    {"context": retriever, "question": RunnablePassthrough()}
    | prompt
    | ChatOpenAI(model="gpt-6-luna")
)
```

| 维度 | LangChain | Pydantic AI |
|------|-----------|-------------|
| 检索器抽象 | 有独立的 `Retriever` 抽象 | 无，自己包成 tool |
| 上下文拼接 | LCEL `{"context": retriever}` 自动 | 工具返回结构化 dict |
| 引用 sources | 要手动从 retriever 取再传 | output_type 强制带 sources |
| 流式 | LCEL `.stream()` | `run_stream()` |
| 类型安全 | `chain.invoke()` 返回 `Any` | `result.output: Answer` |
| 适合场景 | 标准 RAG / 多种 retriever 比试 | 要严格结构化引用 + 工程化 |

复杂的检索逻辑（多 retriever、ensemble、rerank）完全可以继续用 LangChain Retriever，包成一个 Pydantic AI tool 就行，两者不冲突。

---

## 14. 常见坑

| 现象 | 原因 | 解决 |
|------|------|------|
| 模型不调 retrieve 直接回答 | instructions 不够强硬 | 写明"必须先调 retrieve"，并在 `output_type` 里加必填的 `sources` 字段 |
| sources 为空却答得很具体 | 模型在编 | 设 `confidence` 字段 + output_validator 检查 sources 非空 |
| 检索召回不准 | chunk_size 不合适 / embedding 模型差 | 试 chunk_size ∈ {300,600,1000}；中文用千问 `text-embedding-v4`（API）或 `Qwen3-Embedding`（自部署） |
| 中文检索差 | 用了英文 embedding 模型 | 换支持中文的模型：`text-embedding-v4`、`Qwen3-Embedding` 或 `BAAI/bge-m3` |
| 同一份文档建两次索引后重复 | id 不稳定（比如用了 Python 内置 `hash()`，它对字符串每个进程结果都不同） | 用文件路径 + 块内容的 sha1 当 id（见 §4.2） |
| PDF 切块后乱码 | pypdf 提取限制 | 试 `pdfplumber` 或 `unstructured` |
| 引用 snippet 全是开头几行 | top-k 过小 / 文档重复 | 提高 k；加 dedup |
| Logfire 看不到 retrieve 调用 | 没 `instrument_pydantic_ai()` | 加上 |
| FastAPI 每请求都加载模型 | 没用 lifespan | 用 `lifespan` 共享 store |
| 流式但 sources 缺失 | 结构化流尚未完成时取了部分 | 等 `stream_output()` 完成再取 |

---

## 15. 工程清单

- [ ] embedding model 中英文对照 A/B
- [ ] BM25 + 向量 + Reranker 三层
- [ ] 增量更新机制（监听 Git push 触发重建）
- [ ] FastAPI + uvicorn + gunicorn
- [ ] Logfire metric 看板：召回命中率 / token 成本 / 失败率
- [ ] Pydantic Evals 周期性回归
- [ ] 拒答题（"文档里没说"）单独评测
- [ ] 多用户 ACL（每个 user 只能查自己 collection）
- [ ] 限速 + 重试 + 回退模型（主模型 → 备用模型，`ModelFallback`）
- [ ] 知识库分版本（v1 / v2 collection 并行）

---

## 16. 项目目录结构

```
rag-agent/
├── docs/                       # 你的原始文档（md / pdf）
├── chroma_db/                  # 向量库持久化目录（gitignore）
├── rag/
│   ├── __init__.py
│   ├── loader.py
│   ├── splitter.py
│   ├── embedder.py
│   ├── store.py
│   ├── agent.py
│   ├── rerank.py
│   ├── hybrid.py
│   ├── build_index.py
│   ├── eval.py
│   └── web.py
├── tests/
│   └── test_eval.py
└── requirements.txt
```

---

## 17. 本章 demo

完整可运行的单文件版本（不用建包，直接跑）：[`demos/practice/02_project_rag.py`](../../demos/practice/02_project_rag.py)

```bash
# 1) 装依赖
pip install "pydantic-ai-slim[openai]" pypdf python-dotenv   # 向量库用本篇自带的内存 + JSON 版；上规模再换 chromadb / pgvector

# 2) 准备文档（放几个 .md 到 ./sample_docs/）
mkdir -p sample_docs
printf '# Pydantic AI\n\nPydantic AI 是 Pydantic 团队推出的类型安全 Agent 框架。\n' > sample_docs/intro.md

# 3) 跑
python demos/practice/02_project_rag.py
```

demo 会：

1. 自动为 `./sample_docs/` 建索引
2. 启动一个交互问答 loop
3. 每次回答打印答案 + 引用 + 信心分

---

下一篇：[Pydantic AI 实战 03：多 Agent 研究助手（Researcher + Writer + Reviewer）](03-project-research.md)，用 Pydantic Graph 编排三个 Agent。
