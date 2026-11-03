# 缓存策略：Embedding Cache / Query Cache / Result Cache

> RAG 链路里有好几处可以缓存：已经 embed 过的文本不必再 embed，相同的 query 不必再检索，相同 query 加相同 context 也不必再调一次 LLM。几层缓存配合起来，重复请求多的场景能省下相当一部分成本和延迟，具体省多少取决于请求的重复程度。这篇逐层讲 key 怎么设计、多久失效、怎么监控命中率。

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

各层的命中率和失效条件都不一样，要分开设计。

---

## 2. Embedding Cache（最划算）

文档侧：

```python
import hashlib
import json
import redis


r = redis.Redis()


def embed_with_cache(text: str, model: str = "text-embedding-3-small"):
    key = f"emb:{model}:{hashlib.md5(text.encode()).hexdigest()}"
    cached = r.get(key)
    if cached:
        return json.loads(cached)
    
    vec = embed_actual(text, model)
    r.setex(key, 86400 * 30, json.dumps(vec))    # 30 天
    return vec
```

key 里要包含三样东西：模型名（不同模型的向量不能混用）、input_type（Cohere 这类区分输入类型的模型需要）、文本 hash。

命中的情况主要有两种：文档没变（content_hash 一致）却被触发了重新 embed；增量索引时跳过没改动的 chunk。

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

TTL 设短一点（1 小时）就行。query embedding 比文档便宜，这层缓存主要是为了省延迟。

命中率随业务差别很大：客服类问题集中在 FAQ，重复度高，命中率也高；C 端聊天有一批热门问题，命中率居中；长尾内容搜索的 query 很少重复，命中率很低。具体数字要上线后按第 9 节的方法自己统计。

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

TTL 取中等（10 分钟），具体由文档库的更新频率决定。

有两点要处理：文档库变了要主动 invalidate（按 doc_id pattern 删缓存）；不同用户的 filter 不同，key 里要带上 user 或 tenant。

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

LLM 调用最贵，所以这一层省的钱最多。

答案缓存跨用户共享时要格外小心：内容里不能带个人信息；文档库变了，缓存的答案也要更新。

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

把每层 key 包含哪些变量写清楚，出问题时排查起来方便很多。

---

## 7. 失效策略

### 7.1 TTL（最简单）

```python
r.setex(key, ttl, value)
```

各层 TTL 不同：

- Embedding：30 天
- Query embedding：1 小时
- Retrieval：10 分钟
- Answer：10 分钟

### 7.2 主动 invalidate

文档变了，主动清掉相关缓存：

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

实际项目里更常用版本号：

```python
# 全库版本号
corpus_version = "v2.3"

# cache key 含 version
key = f"retr:{corpus_version}:{md5(query)}"

# 版本升级时不用主动清，自然 TTL 过期，新版本起新 key
```

### 7.3 LRU（内存有限）

Redis 配置 `maxmemory-policy=allkeys-lru`，热数据留着，冷数据自动淘汰。

---

## 8. 多层 cache 组合

```python
async def smart_retrieve(query, top_k=5, user=None):
    # Layer 1: 查询级答案 cache
    tenant = user.tenant if user else "public"
    answer_key = f"ans:{md5(query)}:{tenant}"
    if answer := await r.get(answer_key):
        return {"answer": answer, "cache_hit": "answer"}
    
    # Layer 2: 检索结果 cache
    retr_key = f"retr:{md5(query)}:{tenant}"   # 不同租户的 filter 不同，检索结果不能共用
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

在 Grafana dashboard 上看每层的命中率。命中率偏低，一般是 TTL 太短、key 设计得太细，或者流量本身太分散。

---

## 10. 完整 demo

```python
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

输出形如：

```
First call: 0.32s, hit=False
Second call: 0.0001s, hit=True
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| key 里没带模型版本 | 升级模型后新旧向量混在一起 |
| TTL 太长，数据不同步 | 缩短 TTL，或主动 invalidate |
| 跨用户缓存泄漏信息 | key 里加 tenant_id |
| Cache miss storm（多个请求同时重建同一个 key） | 加锁或 single-flight |

---

## 12. 下一步

- [部署形态选型](./04-deployment.md)
- [监控：召回率 / Latency / Cost](./05-monitoring.md)
- [完整 RAG Pipeline 实战](../08-applications/01-full-rag.md)
