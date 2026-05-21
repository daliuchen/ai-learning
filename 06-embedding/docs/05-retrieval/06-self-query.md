# Self-Query / Metadata Filter

> **一句话**：Self-Query 让 LLM 把自然语言 query "翻译"成 **检索 query + metadata filter**——用户说"找去年 billing 类别的文档"，系统自动加 `category=billing AND created_at > 2024-01-01`。

---

## 1. 问题：用户说人话，向量库要结构化 filter

```
用户：找去年关于退款的英文文档

直接 embed("找去年关于退款的英文文档") 检索：
  ❌ 语义混在一起
  ❌ 时间 / 语言条件无法精确表达

更好：
  Query 部分: "退款"
  Filter 部分: {lang: en, created_at >= 2024-01-01}
```

---

## 2. Self-Query 基本思路

```
1. LLM 看 query + 你定义的 metadata schema
2. LLM 输出结构化 JSON:
   {
     "search_query": "退款",
     "filter": {
       "lang": "en",
       "created_at": {">=": 1704067200}
     }
   }
3. 系统按 filter + search_query 查向量库
```

---

## 3. 实现

```python
import json
from openai import OpenAI


client = OpenAI()


METADATA_SCHEMA = """文档 metadata 字段：
- category: enum(billing, support, sales, technical)
- lang: enum(zh, en, ja)
- visibility: enum(public, internal, admin)
- created_at: int (unix timestamp)
- tags: list of strings
"""


SELF_QUERY_PROMPT = f"""把用户问题转成检索 query + metadata filter。

{METADATA_SCHEMA}

输出 JSON：
{{
  "search_query": "用来做向量搜的语义部分",
  "filter": {{ "字段": 值 或 {{"$gte": ...}} 等 }}
}}

例：
用户："找去年关于退款的英文文档"
输出：{{
  "search_query": "退款",
  "filter": {{"lang": "en", "created_at": {{"$gte": 1704067200}}}}
}}

用户："最新的 billing FAQ"
输出：{{
  "search_query": "billing FAQ",
  "filter": {{"category": "billing", "created_at": {{"$gte": 1715000000}}}}
}}

只输出 JSON，不要解释。
"""


def parse_query(user_query: str) -> dict:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SELF_QUERY_PROMPT},
            {"role": "user", "content": user_query},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


parsed = parse_query("找最近一周关于退款的中文文档")
print(parsed)
# {"search_query": "退款", "filter": {"lang": "zh", "created_at": {"$gte": ...}}}
```

---

## 4. 转向量库 filter 格式

```python
# Pinecone
def to_pinecone_filter(filter_dict):
    return filter_dict  # Pinecone 用 MongoDB 风格，直接传


# Qdrant
def to_qdrant_filter(filter_dict):
    from qdrant_client.models import Filter, FieldCondition, MatchValue, Range
    must = []
    for key, value in filter_dict.items():
        if isinstance(value, dict):
            r = Range()
            if "$gte" in value: r.gte = value["$gte"]
            if "$lte" in value: r.lte = value["$lte"]
            must.append(FieldCondition(key=key, range=r))
        else:
            must.append(FieldCondition(key=key, match=MatchValue(value=value)))
    return Filter(must=must)
```

---

## 5. 完整 demo

```python
# demos/retrieval/06_self_query.py
import json
import time
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, Range,
    PayloadSchemaType,
)


client = OpenAI()
qd = QdrantClient(":memory:")


qd.create_collection(
    "docs",
    vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
)


for field in ["category", "lang", "visibility"]:
    qd.create_payload_index("docs", field_name=field, field_schema=PayloadSchemaType.KEYWORD)
qd.create_payload_index("docs", field_name="created_at", field_schema=PayloadSchemaType.INTEGER)


# 数据
now = int(time.time())
docs = [
    ("如何关闭自动续费", "billing", "zh", "public", now - 7 * 86400),
    ("How to cancel subscription", "billing", "en", "public", now - 30 * 86400),
    ("登录帮助", "auth", "zh", "public", now - 3 * 86400),
    ("Refund policy details", "billing", "en", "public", now - 60 * 86400),
    ("Internal pricing note", "billing", "en", "internal", now - 5 * 86400),
]


texts = [d[0] for d in docs]
embs = [d.embedding for d in client.embeddings.create(model="text-embedding-3-small", input=texts).data]


qd.upsert(
    collection_name="docs",
    points=[
        PointStruct(
            id=i,
            vector=emb,
            payload={
                "text": text, "category": cat, "lang": lang,
                "visibility": vis, "created_at": ts,
            },
        )
        for i, ((text, cat, lang, vis, ts), emb) in enumerate(zip(docs, embs))
    ],
)


# Self-query
SELF_QUERY_PROMPT = """把用户问题转成 search_query + filter。

metadata 字段：
- category: billing/auth/sales
- lang: zh/en
- visibility: public/internal
- created_at: unix int

输出 JSON：{"search_query": "...", "filter": {...}}
- 日期用 unix int
- "最近 N 天" 转 {"$gte": now - N*86400}

例：
"最近一周英文 billing 文档" → {"search_query": "billing", "filter": {"lang": "en", "category": "billing", "created_at": {"$gte": now_minus_7_days}}}
"""


def parse(query):
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SELF_QUERY_PROMPT.replace("now_minus_7_days", str(now - 7 * 86400))},
            {"role": "user", "content": query},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def search_with_filter(parsed):
    sq = parsed["search_query"]
    filt = parsed.get("filter", {})
    
    # 转 Qdrant filter
    must = []
    for k, v in filt.items():
        if isinstance(v, dict):
            r = Range()
            if "$gte" in v: r.gte = v["$gte"]
            if "$lte" in v: r.lte = v["$lte"]
            must.append(FieldCondition(key=k, range=r))
        else:
            must.append(FieldCondition(key=k, match=MatchValue(value=v)))
    
    q_emb = client.embeddings.create(model="text-embedding-3-small", input=[sq]).data[0].embedding
    
    return qd.search(
        collection_name="docs",
        query_vector=q_emb,
        query_filter=Filter(must=must) if must else None,
        limit=3,
    )


for q in [
    "最近一周关于取消订阅的中文文档",
    "英文的 billing 内容",
]:
    print(f"\n=== {q} ===")
    parsed = parse(q)
    print(f"Parsed: {parsed}")
    hits = search_with_filter(parsed)
    for h in hits:
        print(f"  score={h.score:.4f}  {h.payload['text']} [{h.payload['lang']}, {h.payload['category']}]")
```

---

## 6. LangChain 集成

```python
from langchain.chains.query_constructor.base import AttributeInfo
from langchain.retrievers.self_query.base import SelfQueryRetriever
from langchain_openai import ChatOpenAI


metadata_info = [
    AttributeInfo(name="category", description="类别", type="string"),
    AttributeInfo(name="lang", description="语言", type="string"),
    AttributeInfo(name="created_at", description="创建时间 unix", type="integer"),
]


retriever = SelfQueryRetriever.from_llm(
    llm=ChatOpenAI(model="gpt-4o-mini"),
    vectorstore=vectorstore,
    document_contents="公司知识库文档",
    metadata_field_info=metadata_info,
)


docs = retriever.invoke("找去年的中文 billing 文档")
```

---

## 7. 高级：复合 filter

```python
# OR 条件
{
  "$or": [
    {"category": "billing"},
    {"category": "support"}
  ]
}


# 嵌套
{
  "$and": [
    {"lang": "en"},
    {"$or": [
      {"category": "billing"},
      {"tags": {"$in": ["payment"]}}
    ]}
  ]
}
```

prompt 里加例子让 LLM 学会写。

---

## 8. 容错

LLM 可能输出错误 filter（字段名错 / 值不对枚举）：

```python
ALLOWED_FIELDS = {"category", "lang", "visibility", "created_at"}
ALLOWED_VALUES = {
    "category": {"billing", "support", "sales"},
    "lang": {"zh", "en", "ja"},
}


def sanitize_filter(filt):
    clean = {}
    for k, v in filt.items():
        if k not in ALLOWED_FIELDS:
            continue
        if k in ALLOWED_VALUES and not isinstance(v, dict):
            if v not in ALLOWED_VALUES[k]:
                continue
        clean[k] = v
    return clean


parsed["filter"] = sanitize_filter(parsed.get("filter", {}))
```

---

## 9. UX：让用户看到解析后的 filter

```python
{
  "answer": "...",
  "interpretation": {
    "search_query": "退款",
    "filters": [
      {"field": "language", "value": "中文"},
      {"field": "created", "after": "2024-05-13"},
    ],
  },
}
```

前端展示：

```
> 找最近一周关于退款的中文文档
理解为：搜"退款"，限定 中文 + 最近 7 天
```

用户能纠正你 LLM 的误解。

---

## 10. 何时不用

```
schema 没 metadata：
  → 直接搜
  
用户从来不指定过滤条件：
  → 不需要

延迟敏感（< 100ms）：
  → LLM 额外一次 call 慢，考虑用规则代替简单模式
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| LLM 编造 metadata 字段 | sanitize + ALLOWED_FIELDS |
| 时间表达"昨天"被忽略 | prompt 给 examples |
| 用户描述跟 schema 名不一样 | LLM 学 description |
| 多语言用户 | prompt 多语言示例 |

---

## 12. 05-retrieval 章节小结

完整 RAG 检索 pipeline：

```
[Query]
   ↓ Self-query / Multi-query / HyDE 改写
   ↓
[向量召回] + [BM25 召回]
   ↓ RRF 融合
[Top-100 candidates]
   ↓ Rerank（cross-encoder）
[Top-5]
   ↓
[LLM 生成]
```

---

## 13. 下一步

- 📖 评测 retrieval → [06-evaluation/01-metrics.md](../06-evaluation/01-metrics.md)
- 📖 完整 RAG 实战 → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
- 📖 监控 + 持续改进 → [07-production/05-monitoring.md](../07-production/05-monitoring.md)
