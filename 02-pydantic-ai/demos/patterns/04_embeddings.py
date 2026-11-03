"""
04_embeddings.py
================
用 Pydantic AI 内置的 Embedder + 内存向量索引，搭一个 mini RAG Agent。
   * Embedder('openai:text-embedding-v4')：千问 embedding（OpenAI 兼容端点，靠 OPENAI_BASE_URL）
   * 自己写 20 行的内存向量索引（余弦相似度）——Pydantic AI 不规定向量库
   * 把检索包成 @agent.tool 给 Agent 调用
   * 加一层 in-process embedding 缓存

没有 OPENAI_API_KEY 时：
   * embedding 走内置的 TestEmbeddingModel（确定性伪向量，只用来演示流程，检索结果无语义）
   * Agent 走 test 模型

依赖：
    pip install "pydantic-ai-slim[openai]"

运行：
    python demos/patterns/04_embeddings.py
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from pydantic_ai import Agent, Embedder, RunContext
from pydantic_ai.embeddings import TestEmbeddingModel

load_dotenv()

USE_REAL = bool(os.getenv("OPENAI_API_KEY"))
# 有千问 key 用千问（OpenAI 兼容端点），否则用内置 test 模型
MODEL_NAME = "openai-chat:qwen-flash" if USE_REAL else "test"
EMBED_MODEL = os.getenv("QWEN_EMBED_MODEL", "text-embedding-v4")

embedder = Embedder(f"openai:{EMBED_MODEL}" if USE_REAL else TestEmbeddingModel(dimensions=32))


# ---------------------------------------------------------------------
# 缓存层：相同 text → 复用向量（真实场景换 Redis / 磁盘）
# ---------------------------------------------------------------------
_cache: dict[str, list[float]] = {}


def _ckey(text: str) -> str:
    return hashlib.sha256(f"{EMBED_MODEL}::{text}".encode()).hexdigest()


async def embed_documents_cached(texts: list[str]) -> list[list[float]]:
    miss = [t for t in texts if _ckey(t) not in _cache]
    if miss:
        result = await embedder.embed_documents(miss)  # 文档侧
        for t, v in zip(miss, result.embeddings):
            _cache[_ckey(t)] = list(v)
    return [_cache[_ckey(t)] for t in texts]


# ---------------------------------------------------------------------
# 最小内存向量索引（余弦相似度）
# ---------------------------------------------------------------------
def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


@dataclass
class MiniIndex:
    texts: list[str] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)

    def add(self, texts: list[str], vectors: list[list[float]]) -> None:
        self.texts += texts
        self.vectors += vectors

    def search(self, qv: list[float], k: int = 3) -> list[tuple[float, str]]:
        scored = [(_cos(qv, v), t) for t, v in zip(self.texts, self.vectors)]
        return sorted(scored, reverse=True)[:k]


# ---------------------------------------------------------------------
# Agent + 工具
# ---------------------------------------------------------------------
@dataclass
class RagDeps:
    index: MiniIndex


agent = Agent(
    MODEL_NAME,
    deps_type=RagDeps,
    instructions=(
        "回答问题前，请先用 retrieve 工具检索知识库；"
        "只基于检索片段作答；找不到资料就说不知道。"
    ),
)


@agent.tool
async def retrieve(ctx: RunContext[RagDeps], query: str, k: int = 3) -> str:
    """从知识库检索相关片段。

    Args:
        query: 用户问题或检索关键词。
        k: 返回结果数。
    """
    qv = (await embedder.embed_query(query)).embeddings[0]  # 查询侧用 embed_query
    hits = ctx.deps.index.search(list(qv), k)
    if not hits:
        return "（知识库无结果）"
    return "\n---\n".join(f"[{i + 1}] (score={s:.2f}) {t}" for i, (s, t) in enumerate(hits))


# ---------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------
DOCS = [
    "Pydantic AI 是由 Pydantic 团队推出的 LLM 应用框架，主打类型安全。",
    "Pydantic AI 的 TestModel 和 FunctionModel 让你不调真实 LLM 也能跑单测。",
    "Agent.run_stream 与 Agent.iter 可以输出事件流，方便对接 Web UI。",
    "Pydantic AI 不规定向量库，做 RAG 可以对接 pgvector / FAISS / 托管向量服务。",
    "Pydantic AI 的 Embedder 提供统一的 embed_query / embed_documents 接口。",
]


async def main() -> None:
    if not USE_REAL:
        print("[!] 未检测到 OPENAI_API_KEY：embedding 用 TestEmbeddingModel（伪向量），Agent 用 test 模型\n")

    # 1) 建索引
    index = MiniIndex()
    index.add(DOCS, await embed_documents_cached(DOCS))
    print(f"已写入 {len(DOCS)} 条文档。")

    # 2) 直接看检索效果（不经过 Agent）
    print("\n===== 纯检索效果 =====")
    qv = (await embedder.embed_query("怎么免费做单测？")).embeddings[0]
    for i, (s, d) in enumerate(index.search(list(qv), 2)):
        print(f"  hit {i + 1}: ({s:.2f}) {d}")

    # 3) 跑 Agent
    print("\n===== Agent 问答 =====")
    deps = RagDeps(index=index)
    for q in ["Pydantic AI 怎么不烧钱跑单测？", "RAG 应该选哪个向量库？"]:
        print(f"\n> {q}")
        r = await agent.run(q, deps=deps)
        print("回答:", r.output)


if __name__ == "__main__":
    asyncio.run(main())
