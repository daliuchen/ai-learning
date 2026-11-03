"""把 docs/ 下所有 .md 切块并向量化（Embedding 走 OpenAI 兼容接口，默认千问 text-embedding-v4）。
向量存内存里的 numpy 矩阵 + 磁盘 .npz 缓存；文档量大了再换成专业向量库（Chroma / pgvector / Qdrant）。
没配 API Key 时自动降级为纯关键词检索，Server 照样能跑。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Chunk:
    doc_id: str
    path: str
    index: int
    text: str


@dataclass
class KBIndex:
    chunks: list[Chunk] = field(default_factory=list)
    vectors: np.ndarray | None = None  # (n_chunks, dim)，已 L2 归一化
    semantic: bool = False


def doc_id_of(rel_path: str) -> str:
    """用相对路径 hash 作为稳定 ID"""
    return hashlib.md5(rel_path.encode()).hexdigest()[:12]


def split_markdown(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """简单按字数切块（生产建议按 markdown 标题切）"""
    if len(text) <= chunk_size:
        return [text]
    out, start = [], 0
    while start < len(text):
        out.append(text[start : start + chunk_size])
        start += chunk_size - overlap
    return out


def _embed(texts: list[str]) -> np.ndarray:
    from openai import OpenAI

    client = OpenAI()  # OPENAI_API_KEY / OPENAI_BASE_URL（千问兼容模式）
    model = os.environ.get("QWEN_EMBED_MODEL", "text-embedding-v4")
    vecs: list[list[float]] = []
    for i in range(0, len(texts), 10):  # 百炼 embedding 单次最多 10 条
        resp = client.embeddings.create(model=model, input=texts[i : i + 10])
        vecs.extend(d.embedding for d in resp.data)
    arr = np.asarray(vecs, dtype=np.float32)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def build_index(docs_dir: Path, cache_dir: Path | None = None) -> KBIndex:
    idx = KBIndex()
    for f in sorted(docs_dir.rglob("*.md")):
        rel = str(f.relative_to(docs_dir))
        for i, piece in enumerate(split_markdown(f.read_text(encoding="utf-8"))):
            idx.chunks.append(Chunk(doc_id_of(rel), rel, i, piece))
    if not idx.chunks or not os.environ.get("OPENAI_API_KEY"):
        return idx  # 无 Key：降级为关键词检索

    # 内容没变就复用磁盘缓存，别每次启动都花钱
    sig = hashlib.md5(json.dumps([c.text for c in idx.chunks]).encode()).hexdigest()
    cache = (cache_dir / f"{sig}.npz") if cache_dir else None
    if cache and cache.exists():
        idx.vectors = np.load(cache)["v"]
    else:
        idx.vectors = _embed([c.text for c in idx.chunks])
        if cache:
            cache_dir.mkdir(parents=True, exist_ok=True)
            np.savez(cache, v=idx.vectors)
    idx.semantic = True
    return idx


def semantic_search(idx: KBIndex, query: str, top_k: int) -> list[tuple[Chunk, float]]:
    q = _embed([query])[0]
    scores = idx.vectors @ q  # 余弦相似度（向量已归一化）；越大越相似
    order = np.argsort(-scores)[:top_k]
    return [(idx.chunks[i], float(scores[i])) for i in order]
