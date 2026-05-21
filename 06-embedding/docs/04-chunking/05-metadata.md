# Chunk Metadata 设计

> **一句话**：每个 chunk 不只是 text，还要带 `doc_id / section / page / source / created_at / tags` 这些 metadata——**好的 metadata 设计直接决定生产 RAG 的检索精度和 UX**。

---

## 1. 为啥 metadata 这么重要

不带 metadata 的 chunk 在生产里几乎不能用：

```python
# ❌ 只有 text
chunk = "用户取消订阅的步骤是..."

# 检索后给 LLM：
"基于以下文档回答：用户取消订阅的步骤是..."

# 问题：
# - 不知道来自哪份文档（无法引用）
# - 不知道是否过期
# - 不知道目标用户是谁（管理员？普通用户？）
# - 不能 filter（"只看 zh / public"）
```

---

## 2. 推荐的 metadata schema

```python
{
    # === 来源标识 ===
    "doc_id": "kb_article_42",            # 业务 ID
    "chunk_idx": 3,                       # 第几个 chunk
    "total_chunks": 8,
    
    # === 内容定位 ===
    "title": "订阅管理 FAQ",               # 文档标题
    "section": "如何取消订阅",            # 当前 section
    "section_path": "用户手册 > 订阅 > 取消",
    "page": 5,                            # PDF 来源
    "url": "https://docs.example.com/cancel#section-3",
    
    # === 业务属性 ===
    "category": "billing",
    "tags": ["refund", "subscription"],
    "lang": "zh",
    "audience": "user",                   # user / admin / dev
    
    # === 版本 / 时间 ===
    "version": "v3.2",
    "created_at": 1715817600,
    "updated_at": 1716000000,
    "expires_at": None,
    
    # === 权限 ===
    "visibility": "public",               # public / internal / admin
    "tenant_id": "org_42",
    "owner": "team_support",
    
    # === Chunking 信息（debug 用）===
    "chunk_size": 245,
    "chunking_strategy": "recursive_md",
}
```

---

## 3. metadata 设计原则

### 3.1 只放"检索 / 过滤 / 显示"需要的

```
✅ doc_id（关联回原文）
✅ category（filter 用）
✅ created_at（按时间过滤）
✅ url（前端链接）

❌ embedding 模型版本（独立表存）
❌ 全文（chunk text 就是了）
❌ binary content（用 URL 引用）
```

### 3.2 字段类型一致

```python
# ❌ 不一致
{"created_at": "2026-05-20"}      # 字符串
{"created_at": "2026/05/20"}      # 不同格式
{"created_at": 1715817600}        # int

# ✅ 统一 unix int
{"created_at": 1715817600}
```

### 3.3 枚举用固定值

```python
# ❌ 自由文本
{"category": "Billing"}
{"category": "billing"}
{"category": "BILLING"}

# ✅ 枚举
{"category": "billing"}  # 系统统一 lowercase

# 更好：定义 enum
class Category:
    BILLING = "billing"
    SUPPORT = "support"
    SALES = "sales"
```

---

## 4. metadata 给 LLM 看

不只是 filter——给 LLM 看 metadata 提升答案质量：

```python
def format_chunk_for_llm(hit):
    text = hit.payload["text"]
    title = hit.payload.get("title", "")
    section = hit.payload.get("section_path", "")
    updated = hit.payload.get("updated_at", 0)
    
    age_days = (now() - updated) / 86400 if updated else None
    age_str = f"（{int(age_days)} 天前更新）" if age_days else ""
    
    return f"""[来源: {title}{age_str}]
[Section: {section}]
{text}"""


# 喂给 LLM
chunks_text = "\n\n---\n\n".join(format_chunk_for_llm(h) for h in hits)


prompt = f"""基于以下文档回答用户问题。如果文档过期或不相关，请说明。

{chunks_text}

问题：{user_question}
"""
```

LLM 看到"3 个月前更新"会自动谨慎。

---

## 5. 引用 / 溯源

前端展示要显示"答案来自哪里":

```python
def answer_with_citations(query):
    hits = vector_db.search(query, limit=5)
    
    # 给每个 hit 一个 citation 编号
    citations = []
    chunks_with_refs = []
    for i, h in enumerate(hits):
        ref_id = f"[{i+1}]"
        citations.append({
            "ref": ref_id,
            "title": h.payload["title"],
            "url": h.payload["url"],
            "section": h.payload["section_path"],
        })
        chunks_with_refs.append(f"{ref_id} {h.payload['text']}")
    
    prompt = f"""基于以下文档回答用户问题。引用每条 claim 时用 [1] [2] 等编号。

{chr(10).join(chunks_with_refs)}

问题：{query}
"""
    
    answer = llm.generate(prompt)
    return {"answer": answer, "citations": citations}
```

前端：

```html
<p>用户可以登录后进入设置取消订阅 [1]，订阅在周期末停止 [1]。退款政策见 [2]。</p>

<div class="citations">
  <a href="#1">[1] 订阅管理 FAQ - 如何取消订阅</a>
  <a href="#2">[2] 退款政策 - 全额退款条件</a>
</div>
```

---

## 6. 在 chunking 时填 metadata

```python
def chunk_doc_with_metadata(doc):
    """切一份完整 doc，每个 chunk 自带 metadata"""
    chunks = splitter.split_text(doc.text)
    items = []
    for i, chunk_text in enumerate(chunks):
        items.append({
            "text": chunk_text,
            "metadata": {
                # 文档级
                "doc_id": doc.id,
                "title": doc.title,
                "url": doc.url,
                "category": doc.category,
                "lang": doc.lang,
                "version": doc.version,
                "created_at": doc.created_at,
                "updated_at": doc.updated_at,
                "visibility": doc.visibility,
                "tenant_id": doc.tenant_id,
                
                # chunk 级
                "chunk_idx": i,
                "total_chunks": len(chunks),
                "chunk_size": len(chunk_text),
            },
        })
    return items
```

---

## 7. 跟向量库 payload index 配合

详见 [03-vector-db/07-hybrid-storage.md](../03-vector-db/07-hybrid-storage.md)。

```python
# Qdrant：给常用 filter 字段建 index
for field, schema in [
    ("category", PayloadSchemaType.KEYWORD),
    ("lang", PayloadSchemaType.KEYWORD),
    ("visibility", PayloadSchemaType.KEYWORD),
    ("tenant_id", PayloadSchemaType.KEYWORD),
    ("created_at", PayloadSchemaType.INTEGER),
]:
    client.create_payload_index("docs", field_name=field, field_schema=schema)
```

---

## 8. 增量更新 vs 替换

文档变了，metadata 怎么处理？

```python
def update_doc(doc):
    # 1. 删除旧的所有 chunk
    vector_db.delete(filter={"doc_id": doc.id})
    
    # 2. 重新 chunk + embed + 插入（带新 metadata）
    items = chunk_doc_with_metadata(doc)
    for item in items:
        item["embedding"] = embed(item["text"])
    vector_db.upsert(items)
```

或软删除 + 加新版本（详见 [07-production/01-incremental.md](../07-production/01-incremental.md)）。

---

## 9. 多版本 / 历史保留

```python
# 不删，加 version
{
    "doc_id": "kb_42",
    "version": "v3",          # 当前
    "is_current": True,        # filter 用
    "previous_version": "v2",
}

# 老版本：is_current=False
# 查询默认 filter: is_current=True
# 想看历史：is_current=False
```

---

## 10. demo：完整 metadata 流程

```python
# demos/chunking/05_metadata.py
import time
import hashlib
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter


md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("#", "h1"), ("##", "h2")])
char_splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=30)


def chunk_md_with_meta(doc):
    final = []
    md_chunks = md_splitter.split_text(doc["content"])
    
    for mc in md_chunks:
        for i, sub in enumerate(char_splitter.split_text(mc.page_content)):
            chunk_id = hashlib.md5(f"{doc['id']}:{i}:{sub[:20]}".encode()).hexdigest()
            final.append({
                "id": chunk_id,
                "text": sub,
                "metadata": {
                    "doc_id": doc["id"],
                    "title": doc["title"],
                    "url": doc.get("url"),
                    "category": doc["category"],
                    "lang": doc["lang"],
                    "version": doc["version"],
                    "created_at": doc["created_at"],
                    "section_h1": mc.metadata.get("h1"),
                    "section_h2": mc.metadata.get("h2"),
                    "chunk_idx": i,
                    "chunk_size": len(sub),
                },
            })
    return final


# 示例
doc = {
    "id": "kb_42",
    "title": "订阅管理 FAQ",
    "url": "https://docs.example.com/sub-faq",
    "category": "billing",
    "lang": "zh",
    "version": "v3",
    "created_at": int(time.time()),
    "content": """# 订阅管理

## 取消订阅
登录账户 → 设置 → 取消订阅
确认后周期末停止

## 退款政策
7 天内全额退款
""",
}


chunks = chunk_md_with_meta(doc)
for c in chunks:
    print(f"ID: {c['id'][:8]}...")
    print(f"  Section: {c['metadata']['section_h1']} > {c['metadata']['section_h2']}")
    print(f"  Text: {c['text'][:50]}...")
    print()
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| metadata 太大（塞全文） | 只放 ID + 关键字段 |
| 时间用 string 不能 range | 改 int timestamp |
| 没 doc_id → 增量更新难 | 必有 doc_id |
| 字段名不一致 | 文档化 + lint |
| 没 version → 灰度难 | 加 version 字段 |

---

## 12. 04-chunking 章节小结

走完这 5 篇你应该：

- 知道 chunking 决定 RAG 上限
- 会用 recursive / markdown / structure-aware 切
- 会用 small-to-big 提质量
- 设计完整的 chunk metadata

---

## 13. 下一步

- 📖 检索策略：纯向量 vs 混合 → [05-retrieval/01-vector-vs-keyword.md](../05-retrieval/01-vector-vs-keyword.md)
- 📖 BM25 + dense 融合 → [05-retrieval/02-bm25-fusion.md](../05-retrieval/02-bm25-fusion.md)
- 📖 rerank → [05-retrieval/05-rerank-pipeline.md](../05-retrieval/05-rerank-pipeline.md)
