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

    pip install "pydantic-ai-slim[openai]" pypdf python-dotenv
    export OPENAI_API_KEY=...
    python demos/practice/02_project_rag.py

需要：
    - 一些 .md / .pdf 放在 ./sample_docs/ 目录
    - OPENAI_API_KEY + OPENAI_BASE_URL（千问；Agent 用 qwen-flash、embedding 用 text-embedding-v4）
      没有 key 时 Agent 走 test 模型、embedding 走 TestEmbeddingModel，只能演示流程
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

# 有千问 key 用千问（OpenAI 兼容端点），否则用内置 test 模型
MODEL_NAME = "openai-chat:qwen-flash" if os.getenv("OPENAI_API_KEY") else "test"

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
# 3) Embedder + 4) 向量库（内存 + JSON 落盘）
# ============================================================
# Pydantic AI 自带 Embedder（统一的 embed_query / embed_documents 接口），
# 但不规定向量库——这里用 30 行的内存索引 + JSON 文件持久化，换 pgvector / FAISS 只改这个类。

import json
import math

from pydantic_ai import Embedder
from pydantic_ai.embeddings import TestEmbeddingModel

USE_REAL = bool(os.getenv("OPENAI_API_KEY"))
EMBED_MODEL = os.getenv("QWEN_EMBED_MODEL", "text-embedding-v4")


def make_embedder() -> Embedder:
    if USE_REAL:
        return Embedder(f"openai:{EMBED_MODEL}")  # 千问 embedding（OpenAI 兼容端点）
    print("[!] 无 OPENAI_API_KEY：用 TestEmbeddingModel，检索没有语义，仅演示流程")
    return Embedder(TestEmbeddingModel(dimensions=32))


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((math.sqrt(sum(x * x for x in a)) or 1.0) * (math.sqrt(sum(y * y for y in b)) or 1.0))


class VectorStore:
    def __init__(self, embedder: Embedder, persist_path: str = "./kb_index.json"):
        self.embedder = embedder
        self.path = Path(persist_path)
        self.items: list[dict] = []
        if self.path.exists():
            self.items = json.loads(self.path.read_text(encoding="utf-8"))

    def add(self, docs: list[Document]) -> None:
        if not docs:
            return
        result = self.embedder.embed_documents_sync([d.text for d in docs])  # 文档侧
        for d, v in zip(docs, result.embeddings):
            self.items.append({"text": d.text, "source": d.source, "vec": list(v)})
        self.path.write_text(json.dumps(self.items), encoding="utf-8")

    async def search(self, query: str, k: int = 4) -> list[Document]:
        # 工具函数跑在事件循环里，所以这里必须用 async 的 embed_query（*_sync 版本不能在运行中的循环里调用）
        qv = list((await self.embedder.embed_query(query)).embeddings[0])  # 查询侧
        ranked = sorted(self.items, key=lambda it: _cos(qv, it["vec"]), reverse=True)[:k]
        return [Document(text=it["text"], source=it["source"]) for it in ranked]

    def count(self) -> int:
        return len(self.items)


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
    MODEL_NAME,
    deps_type=RagDeps,
    output_type=Answer,
    instructions=SYSTEM,
)

@rag_agent.tool
async def retrieve(ctx: RunContext[RagDeps], query: str) -> list[dict]:
    """从知识库检索相关文档片段。

    参数：
        query: 用自然语言描述要搜的内容。
    """
    docs = await ctx.deps.store.search(query, k=ctx.deps.top_k)
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
            "- Embedder: pydantic_ai.Embedder（OpenAI 兼容 / Cohere / Voyage / 本地 sentence-transformers）\n"
            "- 向量库: ChromaDB / FAISS / Qdrant\n"
            "- Reranker: BAAI/bge-reranker-base 提升精度\n",
            encoding="utf-8",
        )
        print(f"   (空目录，自动生成了 2 个示例 .md)")

    raw = load_directory(p)
    print(f"   共 {len(raw)} 个文档")
    chunks = split_documents(raw, chunk_size=600, overlap=80)
    print(f"✂️  切成 {len(chunks)} 个块")

    store = VectorStore(make_embedder())
    if store.count() == 0:
        print("🔢 第一次入库…")
        store.add(chunks)
    else:
        print(f"♻️  向量库已存在 {store.count()} 条，跳过入库（删 ./kb_index.json 可重建）")
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

    # 进交互（非交互环境 / 没 key 时直接结束）
    while sys.stdin.isatty():
        try:
            q = input("\n❓ ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in {"q", "quit", "exit"}:
            break
        ask(store, q)


if __name__ == "__main__":
    main()
