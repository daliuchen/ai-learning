# Self-Query / Metadata Filter

> 用户提问时常常夹带了筛选条件，比如"找去年 billing 类别的文档"。Self-Query 让 LLM 把这类自然语言拆成两部分：一个用于向量检索的 query，加上一组 metadata filter（`category=billing AND created_at >= 2025-01-01`）。这篇讲怎么实现、怎么防止 LLM 写错 filter。

---

## 1. 问题：用户说人话，向量库要结构化 filter

```
用户：找去年关于退款的英文文档

直接 embed("找去年关于退款的英文文档") 检索：
  ❌ 语义混在一起
  ❌ 时间 / 语言条件无法精确表达

更好：
  Query 部分: "退款"
  Filter 部分: {lang: en, 2025-01-01 <= created_at < 2026-01-01}
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
       "created_at": {"$gte": 1735689600, "$lt": 1767225600}
     }
   }
   （1735689600 / 1767225600 是 2025-01-01 / 2026-01-01 的 UTC 时间戳，
    运行时由代码按当前日期算出来，见下面的实现）
3. 系统按 filter + search_query 查向量库
```

---

## 3. 实现

```python
import json
import time
from datetime import datetime, timezone
from openai import OpenAI


client = OpenAI()

# 相对时间的边界由代码算好再放进 prompt，不让 LLM 自己推算日期
now = int(time.time())
this_year = datetime.now(timezone.utc).year
this_year_start = int(datetime(this_year, 1, 1, tzinfo=timezone.utc).timestamp())
last_year_start = int(datetime(this_year - 1, 1, 1, tzinfo=timezone.utc).timestamp())
last_30_days = now - 30 * 86400


METADATA_SCHEMA = """文档 metadata 字段：
- category: enum(billing, auth, support, sales)
- lang: enum(zh, en, ja)
- visibility: enum(public, internal, admin)
- created_at: int (unix timestamp)
- tags: list of strings
"""


SELF_QUERY_PROMPT = f"""把用户问题转成检索 query + metadata filter。

{METADATA_SCHEMA}

当前时间（unix）：{now}

输出 JSON：
{{
  "search_query": "用来做向量搜的语义部分",
  "filter": {{ "字段": 值 或 {{"$gte": ...}} 等 }}
}}

例：
用户："找去年关于退款的英文文档"
输出：{{
  "search_query": "退款",
  "filter": {{"lang": "en", "created_at": {{"$gte": {last_year_start}, "$lt": {this_year_start}}}}}
}}

用户："最新的 billing FAQ"
输出：{{
  "search_query": "billing FAQ",
  "filter": {{"category": "billing", "created_at": {{"$gte": {last_30_days}}}}}
}}

只输出 JSON，不要解释。
"""


def parse_query(user_query: str) -> dict:
    resp = client.chat.completions.create(
        model="gpt-6-luna",
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

## 4. 转成向量库的 filter 格式

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
            if "$gt" in value: r.gt = value["$gt"]
            if "$lte" in value: r.lte = value["$lte"]
            if "$lt" in value: r.lt = value["$lt"]
            must.append(FieldCondition(key=key, range=r))
        else:
            must.append(FieldCondition(key=key, match=MatchValue(value=value)))
    return Filter(must=must)
```

---

## 5. 完整 demo

```python
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
- category: billing/auth/support/sales
- lang: zh/en/ja
- visibility: public/internal/admin
- created_at: unix int

输出 JSON：{"search_query": "...", "filter": {...}}
- 日期用 unix int
- "最近 N 天" 转 {"$gte": now - N*86400}

例：
"最近一周英文 billing 文档" → {"search_query": "billing", "filter": {"lang": "en", "category": "billing", "created_at": {"$gte": now_minus_7_days}}}
"""


def parse(query):
    resp = client.chat.completions.create(
        model="gpt-6-luna",
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
            if "$gt" in v: r.gt = v["$gt"]
            if "$lte" in v: r.lte = v["$lte"]
            if "$lt" in v: r.lt = v["$lt"]
            must.append(FieldCondition(key=k, range=r))
        else:
            must.append(FieldCondition(key=k, match=MatchValue(value=v)))
    
    q_emb = client.embeddings.create(model="text-embedding-3-small", input=[sq]).data[0].embedding
    
    return qd.query_points(
        collection_name="docs",
        query=q_emb,
        query_filter=Filter(must=must) if must else None,
        limit=3,
    ).points


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
# langchain 1.x 已搬到 langchain-classic
from langchain_classic.chains.query_constructor.base import AttributeInfo
from langchain_classic.retrievers.self_query.base import SelfQueryRetriever
from langchain_openai import ChatOpenAI


metadata_info = [
    AttributeInfo(name="category", description="类别", type="string"),
    AttributeInfo(name="lang", description="语言", type="string"),
    AttributeInfo(name="created_at", description="创建时间 unix", type="integer"),
]


retriever = SelfQueryRetriever.from_llm(
    llm=ChatOpenAI(model="gpt-6-luna"),
    vectorstore=vectorstore,
    document_contents="公司知识库文档",
    metadata_field_info=metadata_info,
)


docs = retriever.invoke("找去年的中文 billing 文档")
```

---

## 7. 进阶：复合 filter

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

在 prompt 里放几个例子，LLM 就能学会写这种组合条件。

---

## 8. 容错

LLM 有时会写出错误的 filter，比如字段名不存在、取值不在枚举里：

```python
ALLOWED_FIELDS = {"category", "lang", "visibility", "created_at"}
ALLOWED_VALUES = {
    "category": {"billing", "auth", "support", "sales"},
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
      {"field": "created", "after": "2026-10-27"},  # 按请求时的当前日期算
    ],
  },
}
```

前端展示：

```
> 找最近一周关于退款的中文文档
理解为：搜"退款"，限定 中文 + 最近 7 天
```

用户看到之后，如果 LLM 理解错了可以自己改。

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
| "昨天"这类时间表达被忽略 | prompt 里给 examples |
| 用户的说法和 schema 字段名对不上 | 给字段写 description，让 LLM 照着映射 |
| 用户来自多种语言 | prompt 里放多语言示例 |

---

## 12. 05-retrieval 章节小结

把这一章的手段串起来，完整的检索 pipeline 是这样：

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

- 检索怎么评测：[检索评测指标：Recall@k / MRR / nDCG](../06-evaluation/01-metrics.md)
- [完整 RAG Pipeline 实战](../08-applications/01-full-rag.md)
- 上线后的监控和持续改进：[监控：召回率 / Latency / Cost](../07-production/05-monitoring.md)
