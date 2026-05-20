"""
实战 2：RAG 知识库问答 Agent（单文件版本，方便跑）。

运行：
    # 准备示例文档
    mkdir -p sample_docs
    cat > sample_docs/intro.md <<'EOF'
    # Pydantic AI
    Pydantic AI 是 Pydantic 团队推出的类型安全 Agent 框架。
    它支持 OpenAI / Anthropic / Gemini 等 15+ 模型 Provider。

    ## 核心特性
    - 类型安全：output_type=Pydantic 模型
    - 工具调用：@agent.tool 装饰器自动 schema 化
    - 依赖注入：deps_type + RunContext
    EOF

    pip install pydantic-ai chromadb sentence-transformers pypdf python-dotenv
    export OPENAI_API_KEY=...
    python demos/practice/02_project_rag.py

需要：
    - 一些 .md / .pdf 放在 ./sample_docs/ 目录
    - OPENAI_API_KEY（Agent 用 gpt-4o-mini）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

load_dotenv()


# ============================================================
# 1) 文档加载
# ============================================================

@dataclass
class Document:
    text: str
    source: str

def load_markdown(path: Path) -> list[Document]:
    return [Document(text=path.read_text(encoding="utf-8"), source=str(path))]

def load_pdf(path: Path) -> list[Document]:
    try:
        from pypdf import PdfReader
    except ImportError:
        print(f"⚠️  跳过 PDF {path}：请 pip install pypdf")
        return []
    reader = PdfReader(str(path))
    out = []
    for i, page in enumerate(reader.pages):
        txt = page.extract_text() or ""
        if txt.strip():
            out.append(Document(text=txt, source=f"{path}#page={i+1}"))
    return out

def load_directory(root: Path) -> list[Document]:
    docs: list[Document] = []
    for p in root.rglob("*"):
        suf = p.suffix.lower()
        if suf == ".md":
            docs.extend(load_markdown(p))
        elif suf == ".pdf":
            docs.extend(load_pdf(p))
    return docs


# ============================================================
# 2) 切块
# ============================================================

def split_text(text: str, chunk_size: int = 600, overlap: int = 80) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        start += chunk_size - overlap
    return chunks

def split_documents(docs: list[Document], chunk_size: int = 600, overlap: int = 80) -> list[Document]:
    out: list[Document] = []
    for d in docs:
        for chunk in split_text(d.text, chunk_size, overlap):
            out.append(Document(text=chunk, source=d.source))
    return out


# ============================================================
# 3) Embedder（默认本地 sentence-transformers）
# ============================================================

class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class LocalEmbedder:
    """本地多语言 embedding，首次会下载模型，约 100MB。"""
    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5"):
        from sentence_transformers import SentenceTransformer
        print(f"📥 加载 embedding 模型 {model_name}…")
        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


# ============================================================
# 4) 向量库（ChromaDB）
# ============================================================

class VectorStore:
    def __init__(self, embedder: Embedder, persist_dir: str = "./chroma_db",
                 collection_name: str = "kb"):
        import chromadb
        from chromadb.config import Settings
        self.embedder = embedder
        client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self.col = client.get_or_create_collection(
            name=collection_name, embedding_function=None,
        )

    def add(self, docs: list[Document]) -> None:
        if not docs:
            return
        texts = [d.text for d in docs]
        embs = self.embedder.embed(texts)
        ids = [f"doc-{i}-{abs(hash(d.source + d.text[:30])) & 0xFFFFFFFF}"
               for i, d in enumerate(docs)]
        metas = [{"source": d.source} for d in docs]
        self.col.upsert(ids=ids, documents=texts, embeddings=embs, metadatas=metas)

    def search(self, query: str, k: int = 4) -> list[Document]:
        q_emb = self.embedder.embed([query])[0]
        res = self.col.query(query_embeddings=[q_emb], n_results=k)
        out = []
        if not res["documents"] or not res["documents"][0]:
            return out
        for txt, meta in zip(res["documents"][0], res["metadatas"][0]):
            out.append(Document(text=txt, source=meta["source"]))
        return out

    def count(self) -> int:
        return self.col.count()


# ============================================================
# 5) Agent 装配
# ============================================================

class SourceCite(BaseModel):
    source: str = Field(description="文档路径或 'path#page=N'")
    snippet: str = Field(description="原文片段，<= 200 字")

class Answer(BaseModel):
    text: str = Field(description="对用户问题的回答，必须基于检索内容。")
    sources: list[SourceCite] = Field(
        description="引用的文档来源；若文档里没说，sources=[] 且 text 明确告知用户",
        default_factory=list,
    )
    confidence: float = Field(description="自评信心 0-1", ge=0, le=1)


@dataclass
class RagDeps:
    store: VectorStore
    top_k: int = 4


SYSTEM = """你是一个严谨的文档问答助手。

回答规则：
1. 必须先调用 retrieve 工具检索相关文档，**禁止凭印象回答**。
2. 回答时只引用检索结果中的内容，把对应来源放进 sources 字段。
3. 如果检索结果里没有答案，text 明确告知"文档里未提及"，sources=[]，confidence<0.3。
4. 一次回答最多 3 条来源，每条 snippet 不超过 200 字。
"""

rag_agent = Agent[RagDeps, Answer](
    "openai:gpt-4o-mini",
    deps_type=RagDeps,
    output_type=Answer,
    system_prompt=SYSTEM,
)

@rag_agent.tool
async def retrieve(ctx: RunContext[RagDeps], query: str) -> list[dict]:
    """从知识库检索相关文档片段。

    参数：
        query: 用自然语言描述要搜的内容。
    """
    docs = ctx.deps.store.search(query, k=ctx.deps.top_k)
    return [{"source": d.source, "text": d.text} for d in docs]


# ============================================================
# 6) 离线建索 + 在线问答
# ============================================================

def build_index(docs_dir: str = "./sample_docs") -> VectorStore:
    print(f"📚 加载 {docs_dir}…")
    p = Path(docs_dir)
    if not p.exists():
        p.mkdir(parents=True)
        # 自动生成一个示例文档
        (p / "intro.md").write_text(
            "# Pydantic AI\n"
            "Pydantic AI 是 Pydantic 团队推出的类型安全 Agent 框架。\n"
            "它支持 OpenAI / Anthropic / Gemini 等 15+ 模型 Provider。\n\n"
            "## 核心特性\n"
            "- 类型安全：output_type=Pydantic 模型\n"
            "- 工具调用：@agent.tool 装饰器自动 schema 化\n"
            "- 依赖注入：deps_type + RunContext\n"
            "- 可观测：Logfire 一行接入\n"
            "- 评测：pydantic-evals 独立包\n",
            encoding="utf-8",
        )
        (p / "rag.md").write_text(
            "# RAG 实战\n"
            "RAG 全称 Retrieval-Augmented Generation，先检索再生成。\n"
            "在 Pydantic AI 里，把检索逻辑包成 @agent.tool 即可。\n\n"
            "## 推荐组件\n"
            "- Embedder: sentence-transformers (本地) 或 OpenAI text-embedding-3-small\n"
            "- 向量库: ChromaDB / FAISS / Qdrant\n"
            "- Reranker: BAAI/bge-reranker-base 提升精度\n",
            encoding="utf-8",
        )
        print(f"   (空目录，自动生成了 2 个示例 .md)")

    raw = load_directory(p)
    print(f"   共 {len(raw)} 个文档")
    chunks = split_documents(raw, chunk_size=600, overlap=80)
    print(f"✂️  切成 {len(chunks)} 个块")

    store = VectorStore(LocalEmbedder())
    if store.count() == 0:
        print("🔢 第一次入库…")
        store.add(chunks)
    else:
        print(f"♻️  向量库已存在 {store.count()} 条，跳过入库（删 ./chroma_db 可重建）")
    return store


def ask(store: VectorStore, question: str) -> None:
    deps = RagDeps(store=store, top_k=4)
    try:
        result = rag_agent.run_sync(question, deps=deps)
        ans = result.output
        print(f"\n💡 答：{ans.text}")
        print(f"📊 信心：{ans.confidence:.2f}")
        if ans.sources:
            print(f"📚 引用：")
            for i, c in enumerate(ans.sources, 1):
                print(f"  [{i}] {c.source}")
                print(f"      ↳ {c.snippet[:120]}…")
        else:
            print("📚 引用：（无）")
    except Exception as e:
        print(f"[出错] {type(e).__name__}: {e}")


# ============================================================
# 7) 主入口
# ============================================================

def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        print("❌ 请先在 .env 设置 OPENAI_API_KEY")
        return

    store = build_index("./sample_docs")
    print(f"\n✅ 索引就绪（{store.count()} 条），开始问答（输入 q 退出）\n")

    # 跑几条预设问题
    presets = [
        "Pydantic AI 是什么？",
        "RAG 推荐用什么向量库？",
        "奥运会 2032 在哪举办？",   # 文档里没说，看 Agent 怎么拒答
    ]
    for q in presets:
        print(f"❓ {q}")
        ask(store, q)
        print("-" * 60)

    # 进交互
    while True:
        try:
            q = input("\n❓ ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"q", "quit", "exit"}:
            break
        ask(store, q)


if __name__ == "__main__":
    main()
