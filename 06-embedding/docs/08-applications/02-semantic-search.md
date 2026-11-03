# 语义搜索：电商与内容场景

> 语义搜索和 RAG 用的是同一套检索底座，区别在出口：语义搜索把文档列表交给用户自己挑，不让 LLM 去总结答案。电商的商品搜索、内容平台的文章搜索都属于这一类，排序和业务字段在这里比在 RAG 里重要得多。

---

## 1. 跟 RAG 的区别

| | RAG | 语义搜索 |
|---|---|---|
| 输出 | LLM 总结答案 | 文档/商品列表 |
| 用户视角 | "问答" | "搜索" |
| top_k | 3-5 | 10-50 |
| 排序重要性 | 中（前几条 OK 就行）| 高（细粒度排序）|
| 业务字段 | text 为主 | text + price/rating/category 等 |
| LLM 参与 | 必须 | 可选（rerank / 改写）|

---

## 2. 电商商品搜索

### 2.1 商品数据

```python
products = [
    {
        "id": "p_001",
        "title": "Apple MacBook Pro 14 M3",
        "description": "Pro 级笔记本，搭载 M3 Pro / Max 芯片，14.2 寸 Liquid Retina XDR 屏",
        "category": "笔记本电脑",
        "brand": "Apple",
        "price": 14999.0,
        "rating": 4.8,
        "stock": 25,
        "tags": ["m3", "pro", "工作", "设计"],
    },
    # ...
]
```

### 2.2 给商品 embedding

```python
def product_to_text(p):
    """商品转文本用于 embed"""
    parts = [
        p["title"],
        p["brand"],
        p["category"],
        p["description"],
        " ".join(p.get("tags", [])),
    ]
    return " | ".join(filter(None, parts))


texts = [product_to_text(p) for p in products]
embeddings = embed_batch(texts)
```

### 2.3 入库 + filter index

```python
qd.create_collection("products", vectors_config=VectorParams(size=1024, distance=Distance.COSINE))

for f in ["category", "brand"]:
    qd.create_payload_index("products", field_name=f, field_schema=PayloadSchemaType.KEYWORD)

for f in ["price", "rating"]:
    qd.create_payload_index("products", field_name=f, field_schema=PayloadSchemaType.FLOAT)
qd.create_payload_index("products", field_name="stock", field_schema=PayloadSchemaType.INTEGER)


points = [
    PointStruct(
        id=i,               # Qdrant 的点 ID 只能是整数或 UUID，"p_001" 这种业务 ID 放进 payload
        vector=emb,
        payload=p,
    )
    for i, (p, emb) in enumerate(zip(products, embeddings))
]

qd.upsert("products", points=points)
```

---

## 3. 多维度搜索 query

比如用户输入"找 1 万以内、4 星以上的苹果笔记本"，要先把语义部分和过滤条件拆开：

```python
def parse_query(text):
    """LLM 解析，分离语义 query + filter"""
    resp = llm(...)  # 见 05-retrieval/06-self-query
    return {
        "semantic_query": "苹果笔记本",
        "filter": {
            "brand": "Apple",
            "category": "笔记本电脑",
            "price": {"$lte": 10000},
            "rating": {"$gte": 4},
        },
    }


def search(text, top_k=20):
    parsed = parse_query(text)
    
    q_vec = embed(parsed["semantic_query"])
    
    # 转 Qdrant filter
    must = []
    for k, v in parsed["filter"].items():
        if isinstance(v, dict):
            r = Range()
            if "$lte" in v: r.lte = v["$lte"]
            if "$gte" in v: r.gte = v["$gte"]
            must.append(FieldCondition(key=k, range=r))
        else:
            must.append(FieldCondition(key=k, match=MatchValue(value=v)))
    
    hits = qd.query_points(
        "products",
        query=q_vec.tolist(),
        query_filter=Filter(must=must) if must else None,
        limit=top_k,
    ).points
    
    return [h.payload for h in hits]
```

---

## 4. 业务加权排序

只按 cosine 排序不够，用户还希望评分高的、销量好的排在前面：

```python
def business_score(hit):
    """业务加权得分"""
    sim = hit.score                            # 语义相似度
    rating_boost = (hit.payload["rating"] / 5) * 0.2   # 评分加权
    popularity = min(hit.payload.get("sales", 0) / 1000, 1) * 0.1  # 销量
    stock_alive = 1 if hit.payload["stock"] > 0 else 0.3   # 有货优先
    
    return sim * 0.7 + rating_boost + popularity * stock_alive


hits = qd.query_points("products", query=q_vec, limit=50).points
ranked = sorted(hits, key=lambda h: -business_score(h))[:20]
```

也可以把业务分预先揉进向量里，但我不推荐，这样语义和业务规则耦合得太紧，调一次权重就得重建索引。

---

## 5. 多向量：标题 vs 描述

商品的标题和描述表达的东西不一样，分开 embed 更准：

```python
qd.create_collection(
    "products",
    vectors_config={
        "title": VectorParams(size=1024, distance=Distance.COSINE),
        "description": VectorParams(size=1024, distance=Distance.COSINE),
    },
)


for i, p in enumerate(products):
    qd.upsert("products", points=[PointStruct(
        id=i,
        vector={
            "title": embed(p["title"]).tolist(),
            "description": embed(p["description"]).tolist(),
        },
        payload=p,
    )])


# 查的时候按 title 优先
hits = qd.query_points(
    "products",
    query=q_vec.tolist(), using="title",
    limit=20,
).points
```

也可以两个向量都查一遍，再用 RRF 合并。

---

## 6. 完整 demo

```python
import numpy as np
from openai import OpenAI


client = OpenAI()


PRODUCTS = [
    {"id": "p1", "title": "iPhone 15 Pro Max", "category": "手机", "price": 9999, "rating": 4.8},
    {"id": "p2", "title": "MacBook Pro 14", "category": "笔记本", "price": 14999, "rating": 4.7},
    {"id": "p3", "title": "iPad Air", "category": "平板", "price": 4799, "rating": 4.6},
    {"id": "p4", "title": "AirPods Pro 2", "category": "耳机", "price": 1899, "rating": 4.7},
    {"id": "p5", "title": "Mac mini M2", "category": "台式", "price": 4999, "rating": 4.5},
    {"id": "p6", "title": "Magic Mouse", "category": "配件", "price": 749, "rating": 4.2},
    {"id": "p7", "title": "ThinkPad X1 Carbon", "category": "笔记本", "price": 12999, "rating": 4.6},
    {"id": "p8", "title": "Surface Laptop 5", "category": "笔记本", "price": 8999, "rating": 4.4},
]


def product_to_text(p):
    return f"{p['title']} | {p['category']} | 价格 {p['price']} | 评分 {p['rating']}"


texts = [product_to_text(p) for p in PRODUCTS]
embs = np.array([d.embedding for d in client.embeddings.create(model="text-embedding-3-small", input=texts).data])


def search(query, max_price=None, top_k=5):
    q_vec = np.array(client.embeddings.create(model="text-embedding-3-small", input=[query]).data[0].embedding)
    
    # 业务加权
    scores = []
    for p, emb in zip(PRODUCTS, embs):
        if max_price and p["price"] > max_price:
            continue
        sim = float(emb @ q_vec)
        boost = (p["rating"] / 5) * 0.15
        scores.append((p, sim + boost))
    
    return sorted(scores, key=lambda x: -x[1])[:top_k]


for q, mp in [
    ("性价比高的笔记本", 10000),
    ("苹果手机", None),
    ("便宜的耳机", None),
]:
    print(f"\n=== {q} (max_price={mp}) ===")
    for p, score in search(q, max_price=mp):
        print(f"  {score:.4f}  {p['title']:<30}  ¥{p['price']} ★{p['rating']}")
```

---

## 7. 跟 Elasticsearch 配合

电商系统大多已经有 Elasticsearch 了，两者可以分工：

```
ES：精确匹配 / facet aggregation / 商品库存查询
Vector DB：语义搜索 / 同义改写
```

查询时两路并发，再融合：

```python
async def hybrid_product_search(query, filter_dict):
    # 并发跑
    es_task = es_search(query, filter_dict)
    vec_task = vector_search(query, filter_dict)
    
    es_hits, vec_hits = await asyncio.gather(es_task, vec_task)
    
    # RRF 融合
    return rrf([
        [h["_id"] for h in es_hits["hits"]["hits"]],
        [h.id for h in vec_hits],
    ])
```

---

## 8. 内容平台搜索

内容平台和商品搜索类似，只是文本更长：

```python
articles = [
    {
        "id": "a_001",
        "title": "Python 异步编程指南",
        "summary": "asyncio / await / event loop 全面解析",
        "tags": ["python", "async"],
        "author": "...",
        "published_at": "2026-01-15",
        "read_time_min": 12,
    },
    # ...
]


# 内容更长，可以分别 embed 标题和摘要
```

---

## 9. typo / 拼音兼容

用户在电商里搜"iponne"，应该能召回 iPhone。几种做法：

```python
# 方案 1: 模糊搜索（ES fuzzy）
# 方案 2: Query 改写
def correct_typo(query):
    resp = llm(f"修正拼写：{query}。只输出修正后的 query。")
    return resp.strip()


# 方案 3: 字符级 n-gram 召回（比如 ES 的 ngram 分词），对拼错几个字母更宽容
# 方案 4: 拼音搜索（中文场景）
import pypinyin
pinyin_text = pypinyin.lazy_pinyin(text)
```

---

## 10. UX：搜索高亮 + 类目导航

```python
def search_with_facets(query):
    hits = vector_search(query, top_k=50)
    
    # facet 聚合
    facets = {}
    for h in hits:
        cat = h.payload["category"]
        facets[cat] = facets.get(cat, 0) + 1
    
    return {
        "results": hits[:20],
        "facets": {"category": facets},
        "total": len(hits),
    }
```

前端可以用这些 facet 让用户进一步筛选。

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| 商品 description 太长 | 切开，或者只 embed 关键部分 |
| 业务字段和语义分数搅在一起 | sim 和各项 boost 分开算、分别记录，最后按配置里的权重合成，方便单独调和排查 |
| 拼错的词召回不到 | 用 LLM 纠错，或者走 fuzzy 检索 |
| 类目划错（"iPhone" 出现在"耳机"分类下）| 用业务 metadata 加 filter，别全靠语义 |

---

## 12. 下一步

- 图搜图、文搜图：[多模态：图搜图 / 文搜图 / 图搜文](./03-multimodal.md)
- 推荐系统：[用 Embedding 做推荐系统](./04-recommendation.md)
- 去重和聚类：[用 Embedding 做去重、聚类与异常检测](./05-deduplication.md)
