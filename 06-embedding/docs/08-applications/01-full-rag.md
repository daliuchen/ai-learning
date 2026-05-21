# 完整 RAG Pipeline 实战

> **一句话**：把前面 7 章学的拼起来——**chunk → embed → 索引 → 召回 → rerank → LLM** 一个完整生产级 RAG，能跑、能评、能上线。

---

## 1. 项目目标

搭一个公司内部知识库问答：

- 索引 1 万-100 万条文档
- 用户用自然语言问，秒级返回带引用的答案
- 准确（faithfulness > 0.9）
- 实时增量更新

---

## 2. 架构图

```
[Document Source]
  - Confluence / Notion / GitHub Wiki / PDF
       ↓
[Ingestion Pipeline]
  1. 解析文档 → Markdown
  2. Chunking（结构感知 + small-to-big）
  3. Embed（BGE-large-zh）
  4. 写 Qdrant
       ↓
[Query Pipeline]
  1. Self-query 解析 filter
  2. Embed query
  3. Vector + BM25 + RRF 混合召回
  4. Rerank (bge-reranker-large)
  5. LLM 生成（gpt-4o-mini）+ 引用
       ↓
[Response]
  - answer
  - citations
```

---

## 3. 全部代码

```python
# demos/applications/01_full_rag.py
import asyncio
import hashlib
import json
import time
import numpy as np
from collections import defaultdict
from openai import AsyncOpenAI
from sentence_transformers import SentenceTransformer, CrossEncoder
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, Range,
    PayloadSchemaType,
)
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from rank_bm25 import BM25Okapi
import jieba


# === 初始化 ===
client = AsyncOpenAI()
embedder = SentenceTransformer("BAAI/bge-base-zh-v1.5")
reranker = CrossEncoder("BAAI/bge-reranker-large", max_length=512)
qd = QdrantClient(":memory:")


COLLECTION = "kb_docs"


# === 1. 索引阶段 ===

def chunk_doc(doc):
    """结构感知 + small-to-big 切分"""
    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2")],
    )
    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=300, chunk_overlap=30,
        separators=["\n\n", "\n", "。", "，", " ", ""],
    )
    
    parent_chunks = []
    child_items = []
    
    md_chunks = md_splitter.split_text(doc["content"])
    
    for mc in md_chunks:
        parent_id = hashlib.md5(f"{doc['id']}:{mc.page_content[:30]}".encode()).hexdigest()
        parent_chunks.append({
            "id": parent_id,
            "text": mc.page_content,
            "section": mc.metadata,
        })
        
        for i, sub in enumerate(char_splitter.split_text(mc.page_content)):
            child_items.append({
                "id": hashlib.md5(f"{parent_id}:{i}".encode()).hexdigest(),
                "parent_id": parent_id,
                "text": sub,
                "doc_id": doc["id"],
                "doc_title": doc["title"],
                "doc_url": doc.get("url", ""),
                "category": doc.get("category", "general"),
                "lang": doc.get("lang", "zh"),
                "section_h1": mc.metadata.get("h1", ""),
                "section_h2": mc.metadata.get("h2", ""),
                "created_at": doc.get("created_at", int(time.time())),
            })
    
    return parent_chunks, child_items


def encode_doc(text):
    return embedder.encode(text, normalize_embeddings=True)


def encode_query(text):
    return embedder.encode(
        f"为这个句子生成表示以用于检索相关文章：{text}",
        normalize_embeddings=True,
    )


parent_store = {}
all_corpus_texts = []
all_corpus_ids = []


async def index_docs(docs):
    # 1. 切分
    all_children = []
    for doc in docs:
        parents, children = chunk_doc(doc)
        for p in parents:
            parent_store[p["id"]] = p
        all_children.extend(children)
    
    # 2. embed 子 chunk
    texts = [c["text"] for c in all_children]
    embeddings = embedder.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    
    # 3. 写 Qdrant
    qd.create_collection(
        COLLECTION,
        vectors_config=VectorParams(size=768, distance=Distance.COSINE),
    )
    for field in ["category", "lang", "doc_id"]:
        qd.create_payload_index(COLLECTION, field_name=field, field_schema=PayloadSchemaType.KEYWORD)
    
    points = []
    for c, emb in zip(all_children, embeddings):
        points.append(PointStruct(
            id=c["id"],
            vector=emb.tolist(),
            payload=c,
        ))
    qd.upsert(COLLECTION, points=points)
    
    # 4. BM25 索引
    global all_corpus_texts, all_corpus_ids, bm25
    all_corpus_texts = [c["text"] for c in all_children]
    all_corpus_ids = [c["id"] for c in all_children]
    bm25_tokenized = [list(jieba.cut(t)) for t in all_corpus_texts]
    bm25 = BM25Okapi(bm25_tokenized)
    
    print(f"索引完成: {len(all_children)} child chunks, {len(parent_store)} parents")


# === 2. 检索阶段 ===

def rrf(rankings, k=60, top_k=20):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, did in enumerate(ranking, 1):
            scores[did] += 1 / (k + rank)
    return sorted(scores, key=scores.get, reverse=True)[:top_k]


async def hybrid_retrieve(query, top_recall=20, top_final=5, filter_lang="zh"):
    # 1. Vector
    q_vec = encode_query(query)
    vec_hits = qd.search(
        COLLECTION,
        query_vector=q_vec.tolist(),
        query_filter=Filter(must=[
            FieldCondition(key="lang", match=MatchValue(value=filter_lang)),
        ]),
        limit=top_recall,
    )
    vec_ids = [h.id for h in vec_hits]
    
    # 2. BM25
    bm25_scores = bm25.get_scores(list(jieba.cut(query)))
    bm25_top_idx = np.argsort(-bm25_scores)[:top_recall]
    bm25_ids = [all_corpus_ids[i] for i in bm25_top_idx]
    
    # 3. RRF
    merged_ids = rrf([vec_ids, bm25_ids], top_k=top_recall)
    
    # 4. 拿 child + 找 parent + 去重
    seen_parents = set()
    children_for_rerank = []
    for cid in merged_ids:
        hits = qd.retrieve(COLLECTION, ids=[cid])
        if not hits:
            continue
        child = hits[0].payload
        pid = child["parent_id"]
        if pid in seen_parents:
            continue
        seen_parents.add(pid)
        children_for_rerank.append({**child, "child_id": cid})
        if len(children_for_rerank) >= top_recall:
            break
    
    # 5. Rerank
    pairs = [[query, c["text"]] for c in children_for_rerank]
    if pairs:
        rerank_scores = reranker.predict(pairs)
        order = np.argsort(-rerank_scores)[:top_final]
        final = [(children_for_rerank[i], float(rerank_scores[i])) for i in order]
    else:
        final = []
    
    return final


# === 3. 生成阶段 ===

async def rag_answer(query):
    retrieved = await hybrid_retrieve(query, top_final=3)
    
    # 取 parent text 给 LLM（小召大答）
    contexts = []
    citations = []
    for i, (child, score) in enumerate(retrieved, 1):
        parent = parent_store[child["parent_id"]]
        contexts.append(f"[{i}] {parent['text']}")
        citations.append({
            "ref": f"[{i}]",
            "doc_title": child["doc_title"],
            "section": f"{child['section_h1']} > {child['section_h2']}",
            "url": child["doc_url"],
        })
    
    context_text = "\n\n".join(contexts)
    
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": """你是公司知识库助手。基于以下文档回答用户问题。

规则：
- 只用文档信息，不编造
- 每个 claim 标 [1] [2] 等编号引用
- 文档不能回答时直说"我没找到相关信息"
"""},
            {"role": "user", "content": f"文档：\n{context_text}\n\n问题：{query}"},
        ],
    )
    
    return {
        "answer": resp.choices[0].message.content,
        "citations": citations,
    }


# === 4. 跑起来 ===

async def main():
    docs = [
        {
            "id": "kb_cancel",
            "title": "订阅管理 FAQ",
            "url": "https://docs.example.com/sub-faq",
            "category": "billing",
            "lang": "zh",
            "content": """# 订阅管理

## 如何取消订阅

要取消订阅，请按以下步骤：

1. 登录您的账户
2. 进入"设置"页面
3. 点击"账户"标签
4. 在"订阅"部分点击"取消订阅"按钮

确认后，订阅将在当前周期结束后停止。在此期间您仍可使用所有功能。

## 退款政策

退款仅适用于年付订阅。首次订阅后 7 天内可申请全额退款。超过 7 天将按比例退款剩余月份。月付订阅不支持退款，请联系客服 support@example.com。
""",
        },
        {
            "id": "kb_login",
            "title": "登录帮助",
            "url": "https://docs.example.com/login",
            "category": "auth",
            "lang": "zh",
            "content": """# 登录帮助

## 如何登录

1. 访问 example.com
2. 点击右上角"登录"
3. 输入邮箱和密码

## 忘记密码

点击登录页面"忘记密码"，输入注册邮箱，系统会发送重置链接到邮箱。
""",
        },
    ]
    
    await index_docs(docs)
    
    for q in ["怎么取消订阅", "我想退款", "登录失败"]:
        print(f"\n{'='*60}")
        print(f"Q: {q}")
        result = await rag_answer(q)
        print(f"\nA: {result['answer']}")
        print(f"\nCitations:")
        for c in result["citations"]:
            print(f"  {c['ref']} {c['doc_title']} - {c['section']}")


asyncio.run(main())
```

---

## 4. 跑出来效果

```
Q: 怎么取消订阅

A: 您可以按以下步骤取消订阅 [1]：
1. 登录您的账户
2. 进入"设置"页面
3. 点击"账户"标签
4. 在"订阅"部分点击"取消订阅"按钮

确认后，订阅将在当前周期结束后停止，期间您仍可使用所有功能 [1]。

Citations:
  [1] 订阅管理 FAQ - 订阅管理 > 如何取消订阅
```

---

## 5. 加 evalset 验证

```python
EVALSET = [
    {"query": "如何取消订阅", "expected_doc_id": "kb_cancel", "tags": ["billing", "T1"]},
    {"query": "怎么停止扣费", "expected_doc_id": "kb_cancel", "tags": ["billing", "T2"]},
    {"query": "退款规则", "expected_doc_id": "kb_cancel", "tags": ["billing", "T2"]},
    {"query": "登录有问题", "expected_doc_id": "kb_login", "tags": ["auth", "T2"]},
]


async def eval():
    results = []
    for case in EVALSET:
        retrieved = await hybrid_retrieve(case["query"], top_final=5)
        retrieved_docs = [c["doc_id"] for c, _ in retrieved]
        passed = case["expected_doc_id"] in retrieved_docs
        results.append({"case": case, "passed": passed})
    
    passed = sum(1 for r in results if r["passed"])
    print(f"\n评测: {passed}/{len(results)} 通过 = {passed/len(results):.0%}")


asyncio.run(eval())
```

---

## 6. 部署到 FastAPI

```python
from fastapi import FastAPI


app = FastAPI()


@app.post("/rag/query")
async def query(req: dict):
    result = await rag_answer(req["query"])
    return result


@app.post("/rag/index")
async def index(docs: list[dict]):
    await index_docs(docs)
    return {"indexed": len(docs)}
```

---

## 7. 生产化清单

- [ ] 索引：增量更新（详见 [07-production/01-incremental.md](../07-production/01-incremental.md)）
- [ ] 缓存：3 层缓存（详见 [07-production/03-caching.md](../07-production/03-caching.md)）
- [ ] 评测：跑 evalset（详见 [06-evaluation](../06-evaluation)）
- [ ] 监控：质量 / 延迟 / 成本（详见 [07-production/05-monitoring.md](../07-production/05-monitoring.md)）
- [ ] 部署：TEI / Qdrant 集群（详见 [07-production/04-deployment.md](../07-production/04-deployment.md)）

---

## 8. 下一步

- 📖 语义搜索（电商类）→ [02-semantic-search.md](./02-semantic-search.md)
- 📖 多模态：图搜图 / 文搜图 → [03-multimodal.md](./03-multimodal.md)
- 📖 推荐系统 with embedding → [04-recommendation.md](./04-recommendation.md)
- 📖 去重 / 聚类 → [05-deduplication.md](./05-deduplication.md)
