# 增量索引：文档新增 / 修改 / 删除

> 生产环境里的文档一直在变：新文档要入库，改过的要更新 chunk，删掉的要从索引里清干净。基础做法是两条：chunk 通过 doc_id 关联到原文档，写入一律用 upsert。这篇在此基础上讲变更检测、章节级 diff、零停机重建和幂等重试。

---

## 1. 基本数据模型

```python
{
    "chunk_id": "chunk_abc123",  # 主键
    "doc_id": "kb_42",           # 关联原文档
    "chunk_idx": 3,              # 第几个 chunk
    "text": "...",
    "embedding": [...],
    "metadata": {
        "doc_version": 5,         # 文档版本
        "updated_at": 1778889600,  # unix 秒（2026-05-16）
    }
}
```

一个文档对应 N 个 chunk，删除和更新都按 `doc_id` 操作。

---

## 2. 新增文档

```python
def add_doc(doc):
    chunks = chunk_doc(doc)
    embeddings = embed_batch([c["text"] for c in chunks])
    
    points = [
        {
            "id": f"{doc['id']}_{i}",   # chunk id
            "vector": emb,
            "payload": {
                "doc_id": doc["id"],
                "chunk_idx": i,
                "text": chunk["text"],
                "metadata": doc["metadata"],
            },
        }
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
    ]
    
    vector_db.upsert(points)
```

---

## 3. 修改文档（最棘手）

文档内容一改，就要重新切 chunk、重新 embed、替换索引里的旧数据。

```python
def update_doc(doc):
    # 1. 删旧 chunk
    vector_db.delete(filter={"doc_id": doc["id"]})
    
    # 2. 重新走 add_doc 流程
    add_doc(doc)
```

这里有两点要留意。一是中间状态：多数向量库没有跨操作的事务，先删后插的话，插入失败就会出现"旧的删了、新的没进来"。稳妥的顺序是先把新版本的 chunk 写进去（带上新的 `doc_version`），再删掉旧版本的；用 pgvector 的话直接放进一个 PG 事务（见第 10 节）。二是大文档处理慢，要做成异步任务。

---

## 4. 怎么判断要不要重新 embed

```python
def needs_reindex(doc, last_indexed):
    return (
        doc["content_hash"] != last_indexed.get("content_hash")
        or doc["version"] > last_indexed.get("version", 0)
    )
```

给每条记录加一个 `content_hash`（md5(text)）字段，内容变了才重新 embed。

```python
import hashlib


def content_hash(text):
    return hashlib.md5(text.encode()).hexdigest()


# 文档 metadata
{
    "doc_id": "kb_42",
    "content": "...",
    "content_hash": content_hash(content),
    "version": 5,
}
```

---

## 5. 章节级 diff 优化（进阶）

文档只改了一段时，没必要全文重新 embed，只处理改动过的 chunk 就行：

```python
def smart_update_doc(doc, old_chunks_meta):
    """只更新变了的 chunk"""
    new_chunks = chunk_doc(doc)
    new_hashes = [content_hash(c["text"]) for c in new_chunks]
    old_hashes = [c["content_hash"] for c in old_chunks_meta]
    
    # 找差异
    to_keep = []      # chunk_idx 不变
    to_add = []       # 新增
    to_remove = []    # 删除
    
    # 简单情况：chunk 数量一样，按位置对比
    if len(new_hashes) == len(old_hashes):
        for i, (nh, oh) in enumerate(zip(new_hashes, old_hashes)):
            if nh != oh:
                to_add.append(i)   # 要重 embed
    else:
        # 复杂情况：用 diff 算法
        # 简化版：全删全加
        to_remove = list(range(len(old_chunks_meta)))
        to_add = list(range(len(new_chunks)))
    
    # 执行
    if to_remove:
        vector_db.delete(filter={"doc_id": doc["id"], "chunk_idx": {"$in": to_remove}})
    
    if to_add:
        new_emb = embed_batch([new_chunks[i]["text"] for i in to_add])
        # ... upsert
```

我的经验是看文档大小：小文档（少于 10 个 chunk）直接全删全加，省事；大文档（比如 100 页的 PDF）才值得做章节级 diff。

---

## 6. 删除文档

```python
def delete_doc(doc_id):
    vector_db.delete(filter={"doc_id": doc_id})
```

也可以软删除：

```python
def soft_delete_doc(doc_id):
    vector_db.update_metadata(
        filter={"doc_id": doc_id},
        update={"deleted": True, "deleted_at": int(time.time())},
    )


# 检索时排除：各库写法不同。Qdrant 用 must_not + 字段存在性判断；
# Pinecone 不支持按 null 过滤，可以给所有记录写一个 deleted: false，软删时改成 true
filter = {"deleted": {"$eq": False}}
```

软删除的好处是误删能恢复，历史也可以审计。过老的软删数据定期做一次 hard delete。

---

## 7. 批量处理 / 全库重建

```python
def reindex_all_docs():
    """全库重建（换 embedder 时用）"""
    docs = db.query("SELECT * FROM docs")
    
    # 用新 collection（防止下线时间）
    new_collection = "docs_v2"
    vector_db.create_collection(new_collection, ...)
    
    # 批量 embed
    batch_size = 256
    for batch_start in range(0, len(docs), batch_size):
        batch = docs[batch_start:batch_start + batch_size]
        chunks_per_doc = [chunk_doc(d) for d in batch]
        all_chunks = [c for cs in chunks_per_doc for c in cs]
        embeddings = embed_batch([c["text"] for c in all_chunks])
        
        points = build_points(all_chunks, embeddings)
        vector_db.upsert(collection=new_collection, points=points)
    
    # 切换
    vector_db.alias("docs", to=new_collection)
    vector_db.delete_collection("docs_v1")
```

零停机迁移：

```
1. 起新 collection
2. 后台跑完
3. 切 alias
4. 删老 collection
```

---

## 8. 数据源监听

CDC 模式（Change Data Capture）：

```python
# 监听数据库变更
import asyncio
from contextlib import asynccontextmanager


async def listen_doc_changes():
    async with db.listen("doc_changes") as ch:
        async for event in ch:
            doc_id = event["doc_id"]
            op = event["op"]
            
            if op == "insert":
                doc = await db.get(doc_id)
                add_doc(doc)
            elif op == "update":
                doc = await db.get(doc_id)
                update_doc(doc)
            elif op == "delete":
                delete_doc(doc_id)


asyncio.run(listen_doc_changes())
```

或者定期轮询：

```python
async def poll_changes(last_synced, interval=60):
    while True:
        changes = await db.query(
            "SELECT * FROM docs WHERE updated_at > %s",
            last_synced,
        )
        for doc in changes:
            update_doc(doc)
        last_synced = max(d["updated_at"] for d in changes) if changes else last_synced
        await asyncio.sleep(interval)
```

---

## 9. 重试 / 幂等

embed 或 upsert 失败后，要能安全地重跑：

```python
async def add_doc_safe(doc, max_retries=3):
    for attempt in range(max_retries):
        try:
            # upsert 幂等（同 ID 直接覆盖）
            await add_doc(doc)
            return
        except Exception as e:
            log.error(f"add_doc failed attempt {attempt}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                # 落到 dead-letter queue
                await dlq.send({"doc_id": doc["id"], "error": str(e)})
                raise
```

chunk_id 要用确定性算法生成（hash(doc_id + chunk_idx)），这样重跑时同一个 id 会自动覆盖，不会产生重复数据。

---

## 10. 在 pgvector 里做增量

```sql
-- 新增
INSERT INTO chunks (chunk_id, doc_id, text, embedding) VALUES (...);

-- 更新（推荐 UPSERT）
INSERT INTO chunks (chunk_id, doc_id, text, embedding)
VALUES (...)
ON CONFLICT (chunk_id) DO UPDATE
SET text = EXCLUDED.text, embedding = EXCLUDED.embedding;

-- 删除某 doc 所有 chunk
DELETE FROM chunks WHERE doc_id = $1;

-- 软删
UPDATE chunks SET deleted_at = NOW() WHERE doc_id = $1;
SELECT * FROM chunks WHERE deleted_at IS NULL ORDER BY embedding <=> $1 LIMIT 5;
```

pgvector 的优势在于能直接用 PG 事务，比向量库先删再插更稳。

---

## 11. 完整 demo

```python
import asyncio
import hashlib
import uuid
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Filter, FieldCondition, MatchValue


class IncrementalIndexer:
    def __init__(self, client: QdrantClient, collection: str):
        self.client = client
        self.collection = collection
    
    def _chunk_id(self, doc_id, idx):
        # 确定性 ID：同一个 (doc_id, idx) 永远得到同一个 UUID，重跑时自动覆盖
        return str(uuid.UUID(hashlib.md5(f"{doc_id}_{idx}".encode()).hexdigest()))
    
    async def add_doc(self, doc):
        chunks = self._chunk(doc)
        embeddings = await self._embed_batch([c["text"] for c in chunks])
        
        points = [
            PointStruct(
                id=self._chunk_id(doc["id"], i),
                vector=emb,
                payload={
                    "doc_id": doc["id"],
                    "chunk_idx": i,
                    "text": c["text"],
                    "content_hash": hashlib.md5(c["text"].encode()).hexdigest(),
                },
            )
            for i, (c, emb) in enumerate(zip(chunks, embeddings))
        ]
        
        self.client.upsert(self.collection, points=points)
    
    async def update_doc(self, doc):
        # 1. 删旧
        self.client.delete(
            self.collection,
            points_selector=Filter(must=[
                FieldCondition(key="doc_id", match=MatchValue(value=doc["id"])),
            ]),
        )
        # 2. 重新加
        await self.add_doc(doc)
    
    async def delete_doc(self, doc_id):
        self.client.delete(
            self.collection,
            points_selector=Filter(must=[
                FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
            ]),
        )
    
    def _chunk(self, doc):
        # 简化版
        return [{"text": doc["content"][i:i+500]} for i in range(0, len(doc["content"]), 500)]
    
    async def _embed_batch(self, texts):
        # 调 OpenAI
        from openai import AsyncOpenAI
        client = AsyncOpenAI()
        resp = await client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [d.embedding for d in resp.data]


# 用
indexer = IncrementalIndexer(QdrantClient(":memory:"), "docs")
asyncio.run(indexer.add_doc({"id": "kb_42", "content": "..."}))
asyncio.run(indexer.update_doc({"id": "kb_42", "content": "更新后的内容"}))
asyncio.run(indexer.delete_doc("kb_42"))
```

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| chunk_id 不稳定，重跑生成了不同 ID | 用 hash(doc_id + idx) 生成确定性 ID |
| 改了文档没删旧 chunk | 先 delete 再 insert，或者用 UPSERT |
| 大量并发更新互相冲突 | 按 doc_id 加锁，或串行处理 |
| 文档删了，metadata 缓存还在 | 缓存也要一起清 |

---

## 13. 下一步

- [批量 Embed + Cost 优化](./02-batch-cost.md)
- [缓存策略：Embedding Cache / Query Cache / Result Cache](./03-caching.md)
- [部署形态选型](./04-deployment.md)
