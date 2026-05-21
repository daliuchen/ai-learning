# 缓存策略：Embedding Cache / Query Cache / Result Cache

> **一句话**：3 层缓存——**embedding cache**（已 embed 的文本不重 embed）+ **query cache**（同 query 不重搜）+ **result cache**（同 query 同 context 不重 LLM）能省 50%+ 成本和延迟。

---

## 1. 三层缓存

```
[User Query]
   ↓
[Query Embedding Cache]   ← 热门 query 命中
   ↓ miss
embed query
   ↓
[Retrieval Cache]         ← 同 query 同 corpus 不重搜
   ↓ miss
向量库 + rerank
   ↓
[Result Cache]            ← 同 (query, top docs) 不重 LLM
   ↓ miss
LLM 生成
   ↓
[Answer]
```

不同层 hit rate / 失效条件不同。

---

## 2. Embedding Cache（最划算）

文档侧：

```python
import hashlib
import redis


r = redis.Redis()


def embed_with_cache(text: str, model: str = "text-embedding-3-small"):
    key = f"emb:{model}:{hashlib.md5(text.encode()).hexdigest()}"
    cached = r.get(key)
    if cached:
        import json
        return json.loads(cached)
    
    vec = embed_actual(text, model)
    r.setex(key, 86400 * 30, json.dumps(vec))    # 30 天
    return vec
```

**Key 设计**：

- 包含 model 名（不同模型不能共享）
- 包含 input_type（Cohere 多类型时）
- 文本 hash

**命中场景**：

- 文档没变（content_hash 一致）→ 重 embed 同文本
- 增量索引时跳过未变 chunk

---

## 3. Query Embedding Cache

```python
def embed_query_cached(query, ttl=3600):
    """热门 query 的 embedding 缓存"""
    key = f"qemb:{model}:{md5(query)}"
    cached = r.get(key)
    if cached:
        return json.loads(cached)
    
    vec = embed(query)
    r.setex(key, ttl, json.dumps(vec))
    return vec
```

**TTL 短点**（1 小时）——query embedding 比文档便宜，重要的是省 latency。

实战 hit rate：

- C 端聊天：~30%（热门问题）
- 客服：~50%（FAQ 集中）
- 长尾内容搜索：~10%

---

## 4. Retrieval Result Cache

```python
def retrieve_cached(query, top_k=5, ttl=600):
    """缓存检索结果"""
    key = f"retr:{md5(query)}:{top_k}"
    cached = r.get(key)
    if cached:
        return json.loads(cached)
    
    results = retrieve_actual(query, top_k)
    r.setex(key, ttl, json.dumps(results))
    return results
```

**TTL 中等**（10 分钟）——文档库变更频率决定。

注意：

- 文档库改了要 invalidate（按 doc_id pattern 删 cache）
- 不同用户的 filter 不同 → key 要含 user / tenant

---

## 5. Answer Cache（LLM 输出）

```python
def answer_cached(query, contexts, ttl=600):
    """同 query + 同 context = 同答案"""
    ctx_hash = md5("".join(c["id"] for c in contexts))
    key = f"ans:{md5(query)}:{ctx_hash}"
    cached = r.get(key)
    if cached:
        return cached.decode()
    
    answer = llm_generate(query, contexts)
    r.setex(key, ttl, answer)
    return answer
```

LLM 是最贵的——这层省钱最多。

但**注意**：

- 答案 cache 跨 user 共享要小心（不能含个人信息）
- 文档库变了 → 答案要更新

---

## 6. Cache key 设计

```python
def make_cache_key(parts: dict) -> str:
    """统一格式 cache key"""
    sorted_items = sorted(parts.items())
    parts_str = "|".join(f"{k}={v}" for k, v in sorted_items)
    return hashlib.md5(parts_str.encode()).hexdigest()


# 示例
key = make_cache_key({
    "type": "retrieve",
    "query": query,
    "top_k": 5,
    "filter_lang": "zh",
    "user_tier": "pro",
    "corpus_version": "v2.3",
})
```

明确每层 key 包含什么变量，方便 debug。

---

## 7. 失效策略

### 7.1 TTL（最简单）

```python
r.setex(key, ttl, value)
```

各层不同 TTL：

- Embedding：30 天
- Query embedding：1 小时
- Retrieval：10 分钟
- Answer：10 分钟

### 7.2 主动 invalidate

文档变了主动清相关 cache：

```python
def invalidate_doc(doc_id):
    # 找所有相关 cache key
    pattern = f"retr:*"  # 简化
    for key in r.scan_iter(pattern):
        # 反查文档 ID 在不在结果里
        data = json.loads(r.get(key))
        if any(d["id"] == doc_id for d in data):
            r.delete(key)
```

实战常用 versioning：

```python
# 全库版本号
corpus_version = "v2.3"

# cache key 含 version
key = f"retr:{corpus_version}:{md5(query)}"

# 版本升级时不用主动清，自然 TTL 过期，新版本起新 key
```

### 7.3 LRU（内存有限）

Redis 配置 `maxmemory-policy=allkeys-lru`，热门保留、冷的自动淘汰。

---

## 8. 多层 cache 组合

```python
async def smart_retrieve(query, top_k=5, user=None):
    # Layer 1: 查询级答案 cache
    answer_key = f"ans:{md5(query)}:{user.tenant if user else 'public'}"
    if answer := await r.get(answer_key):
        return {"answer": answer, "cache_hit": "answer"}
    
    # Layer 2: 检索结果 cache
    retr_key = f"retr:{md5(query)}"
    if cached_retr := await r.get(retr_key):
        contexts = json.loads(cached_retr)
        # 答案需要重新生成（用户 / 时间不同？）
    else:
        # Layer 3: query embedding cache
        qemb_key = f"qemb:{md5(query)}"
        if cached_qemb := await r.get(qemb_key):
            q_vec = json.loads(cached_qemb)
        else:
            q_vec = await embed_actual(query)
            await r.setex(qemb_key, 3600, json.dumps(q_vec))
        
        # 检索
        contexts = await vector_db.search(q_vec, limit=top_k)
        await r.setex(retr_key, 600, json.dumps(contexts))
    
    # LLM 生成
    answer = await llm_generate(query, contexts)
    await r.setex(answer_key, 600, answer)
    return {"answer": answer, "cache_hit": "miss"}
```

---

## 9. 监控 hit rate

```python
from prometheus_client import Counter


cache_hits = Counter("cache_hits", "Cache hits", ["layer"])
cache_misses = Counter("cache_misses", "Cache misses", ["layer"])


def cached_call(layer, key, ttl, compute_fn):
    if cached := r.get(key):
        cache_hits.labels(layer=layer).inc()
        return json.loads(cached)
    
    cache_misses.labels(layer=layer).inc()
    value = compute_fn()
    r.setex(key, ttl, json.dumps(value))
    return value
```

Grafana dashboard 看每层 hit rate。低 hit rate 说明：

- TTL 太短
- key 设计太精细
- 流量太分散

---

## 10. 完整 demo

```python
# demos/production/03_caching.py
import asyncio
import hashlib
import json
import time
from openai import AsyncOpenAI


client = AsyncOpenAI()


# 用 dict 模拟 Redis
class FakeCache:
    def __init__(self):
        self.store = {}
        self.expiry = {}
    
    def get(self, key):
        if key not in self.store:
            return None
        if time.time() > self.expiry.get(key, float("inf")):
            del self.store[key]
            del self.expiry[key]
            return None
        return self.store[key]
    
    def set(self, key, value, ttl):
        self.store[key] = value
        self.expiry[key] = time.time() + ttl


cache = FakeCache()


def md5(s):
    return hashlib.md5(s.encode()).hexdigest()


async def embed_cached(text, ttl=3600):
    key = f"emb:{md5(text)}"
    if cached := cache.get(key):
        return json.loads(cached), True
    
    resp = await client.embeddings.create(model="text-embedding-3-small", input=[text])
    vec = resp.data[0].embedding
    cache.set(key, json.dumps(vec), ttl)
    return vec, False


async def main():
    # 第一次
    t = time.time()
    vec, hit = await embed_cached("如何取消订阅")
    print(f"First call: {time.time()-t:.2f}s, hit={hit}")
    
    # 第二次（命中）
    t = time.time()
    vec, hit = await embed_cached("如何取消订阅")
    print(f"Second call: {time.time()-t:.4f}s, hit={hit}")


asyncio.run(main())
```

输出：

```
First call: 0.32s, hit=False
Second call: 0.0001s, hit=True
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| Cache key 没含 model 版本 | 升级模型后污染 |
| TTL 太长 → 不同步 | 平衡 / 主动 invalidate |
| Cross-user cache 信息泄漏 | 加 tenant_id 到 key |
| Cache miss storm（同时多请求 build 同 key） | 加 lock / single-flight |

---

## 12. 下一步

- 📖 部署形态 → [04-deployment.md](./04-deployment.md)
- 📖 监控 → [05-monitoring.md](./05-monitoring.md)
- 📖 完整 RAG → [08-applications/01-full-rag.md](../08-applications/01-full-rag.md)
