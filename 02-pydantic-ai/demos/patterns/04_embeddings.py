"""
04_embeddings.py
================
用 OpenAI embeddings + chromadb 在 Pydantic AI 里搭一个 mini RAG Agent。
   * 构建本地内存 chromadb collection
   * 用 OpenAI text-embedding-3-small 算向量
   * 把检索包成 @agent.tool 给 Agent 调用
   * 加一层 in-process embedding 缓存

没有 OPENAI_API_KEY 时：
   * embedding 走"哈希向量"（确定性、不联网，仅用于演示流程，不保证检索质量）
   * Agent 走 TestModel

依赖：
    pip install chromadb openai

运行：
    python demos/patterns/04_embeddings.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import struct
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel

USE_REAL_OPENAI = bool(os.getenv("OPENAI_API_KEY"))
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536  # 与上面 model 匹配；换模型要同步改

# ---------------------------------------------------------------------
# Embedding 函数（真实 / 离线 双实现）
# ---------------------------------------------------------------------
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


async def _embed_openai(texts: list[str]) -> list[list[float]]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    resp = await client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def _embed_offline(texts: list[str]) -> list[list[float]]:
    """无 key 时的占位 embedding：把 SHA256 拉成 1536 维伪向量。

    仅用于演示流水线能跑通，不要在真实检索上用。
    """
    out: list[list[float]] = []
    for t in texts:
        h = hashlib.sha256(t.encode("utf-8")).digest()
        # 把 32 字节摘要重复填满到 EMBED_DIM*4 字节，再每 4 字节解为 float32
        raw = (h * (EMBED_DIM // 8 + 1))[: EMBED_DIM * 4]
        vec = list(struct.unpack(f"{EMBED_DIM}f", raw))
        # 归一化便于余弦距离
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        out.append([v / norm for v in vec])
    return out


# ---------------------------------------------------------------------
# 缓存层：相同 text + model → 复用向量
# ---------------------------------------------------------------------
_cache: dict[str, list[float]] = {}


def _ckey(text: str) -> str:
    return hashlib.sha256(f"{EMBED_MODEL}::{text}".encode()).hexdigest()


async def embed_cached(texts: list[str]) -> list[list[float]]:
    miss_idx: list[int] = []
    miss_texts: list[str] = []
    out: list[list[float] | None] = []
    for i, t in enumerate(texts):
        k = _ckey(t)
        if k in _cache:
            out.append(_cache[k])
        else:
            out.append(None)
            miss_idx.append(i)
            miss_texts.append(t)

    if miss_texts:
        new = await _embed_openai(miss_texts) if USE_REAL_OPENAI else _embed_offline(miss_texts)
        for i, v in zip(miss_idx, new):
            _cache[_ckey(texts[i])] = v
            out[i] = v
    return [v for v in out if v is not None]


# ---------------------------------------------------------------------
# Agent + 工具
# ---------------------------------------------------------------------
@dataclass
class RagDeps:
    collection: object  # chromadb collection
    embed_fn: EmbedFn


agent = Agent(
    "openai:gpt-4o-mini",
    deps_type=RagDeps,
    system_prompt=(
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
    [vec] = await ctx.deps.embed_fn([query])
    hit = ctx.deps.collection.query(query_embeddings=[vec], n_results=k)
    docs = hit.get("documents", [[]])[0]
    if not docs:
        return "（知识库无结果）"
    return "\n---\n".join(f"[{i + 1}] {d}" for i, d in enumerate(docs))


# ---------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------
DOCS = [
    "Pydantic AI 是由 Pydantic 团队推出的 LLM 应用框架，主打类型安全。",
    "Pydantic AI 的 TestModel 让你不调真实 LLM 也能跑单测。",
    "Agent.run_stream 与 Agent.iter 可以输出 SSE 风格的事件流，方便对接 Web UI。",
    "Pydantic AI 不内置向量库，做 RAG 推荐对接 chromadb / qdrant / pgvector。",
    "A2A 协议让不同框架的 Agent 之间可以互相调用。",
]


async def main() -> None:
    if not USE_REAL_OPENAI:
        print("[!] 未检测到 OPENAI_API_KEY：")
        print("    * embedding 使用本地哈希向量（仅演示流程）")
        print("    * Agent 使用 TestModel\n")

    # 1) 准备向量库（内存版 chromadb）
    try:
        import chromadb
    except ImportError:
        print("请先 pip install chromadb")
        return

    client = chromadb.Client()  # in-memory
    coll = client.get_or_create_collection(
        name="mini_kb",
        metadata={"hnsw:space": "cosine"},
    )

    # 2) 写入文档（带 content hash 防止重复写）
    ids = [f"d{i}" for i in range(len(DOCS))]
    embeddings = await embed_cached(DOCS)
    coll.add(
        ids=ids,
        documents=DOCS,
        embeddings=embeddings,
        metadatas=[{"hash": _ckey(d)} for d in DOCS],
    )
    print(f"已写入 {len(DOCS)} 条文档。")

    # 3) 直接看一下检索效果（不经过 Agent）
    print("\n===== 纯检索效果 =====")
    [qv] = await embed_cached(["怎么免费做单测？"])
    hit = coll.query(query_embeddings=[qv], n_results=2)
    for i, d in enumerate(hit["documents"][0]):
        print(f"  hit {i + 1}: {d}")

    # 4) 跑 Agent
    print("\n===== Agent 问答 =====")
    deps = RagDeps(collection=coll, embed_fn=embed_cached)

    questions = [
        "Pydantic AI 怎么不烧钱跑单测？",
        "RAG 应该选哪个向量库？",
    ]

    for q in questions:
        print(f"\n> {q}")
        if USE_REAL_OPENAI:
            r = await agent.run(q, deps=deps)
        else:
            with agent.override(model=TestModel()):
                r = await agent.run(q, deps=deps)
        print("回答:", r.output)


if __name__ == "__main__":
    asyncio.run(main())
