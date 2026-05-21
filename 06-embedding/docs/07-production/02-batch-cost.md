# 批量 Embed + Cost 优化

> **一句话**：百万级文档第一次入库可能要几小时 / 几十美元——**批量 API + 并发 + 缓存 + 选对模型** 能省一半。

---

## 1. 批量 vs 单条

```
单条 embed：
  1000 doc × 200ms / 调用 = 200 秒
  
批量（每次 100）：
  1000 doc / 100 × 800ms = 8 秒
  快 25 倍
```

```python
# ❌ 慢
for doc in docs:
    resp = client.embeddings.create(model="...", input=doc.text)
    embeddings.append(resp.data[0].embedding)


# ✅ 快
all_texts = [d.text for d in docs]
embeddings = []
batch_size = 256

for i in range(0, len(all_texts), batch_size):
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=all_texts[i:i+batch_size],
    )
    embeddings.extend([d.embedding for d in resp.data])
```

OpenAI 限制：

- 单次 input list 最多 2048 项
- 单条最多 8191 tokens

推荐 batch_size = **96-512**（看 token 总量 / latency 容忍）。

---

## 2. 并发跑

```python
import asyncio
from openai import AsyncOpenAI


client = AsyncOpenAI()


async def embed_batch(texts):
    resp = await client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in resp.data]


async def embed_all(all_texts, batch_size=256, concurrency=10):
    sem = asyncio.Semaphore(concurrency)
    
    async def task(batch):
        async with sem:
            return await embed_batch(batch)
    
    batches = [all_texts[i:i+batch_size] for i in range(0, len(all_texts), batch_size)]
    results = await asyncio.gather(*[task(b) for b in batches])
    
    return [emb for r in results for emb in r]


vecs = asyncio.run(embed_all(my_million_texts))
```

10 路并发 + batch 256 → 100 万条 ~10 分钟。

注意 rate limit：

- OpenAI Tier 1：500 RPM / 1M TPM
- 适当 `concurrency` 不超限

---

## 3. OpenAI Batch API（50% 折扣）

不紧急的大量 embed → 用 Batch API：

```python
# 1. 准备 .jsonl
import json

requests = []
for i, text in enumerate(texts):
    requests.append({
        "custom_id": f"doc_{i}",
        "method": "POST",
        "url": "/v1/embeddings",
        "body": {
            "model": "text-embedding-3-small",
            "input": text,
        },
    })


with open("embed_batch.jsonl", "w") as f:
    for r in requests:
        f.write(json.dumps(r) + "\n")


# 2. 上传 + 提交
file = client.files.create(file=open("embed_batch.jsonl", "rb"), purpose="batch")
batch = client.batches.create(
    input_file_id=file.id,
    endpoint="/v1/embeddings",
    completion_window="24h",
)


print(f"Batch {batch.id} submitted")


# 3. 等完成（最多 24h，通常更快）
import time
while True:
    batch = client.batches.retrieve(batch.id)
    if batch.status == "completed":
        break
    time.sleep(60)


# 4. 下载结果
result_file = client.files.content(batch.output_file_id)
results = [json.loads(l) for l in result_file.text.strip().split("\n")]
```

**优**：

- 50% 价格折扣
- 限流更松

**劣**：

- 延迟最长 24h
- 大数据集才划算（手续费 / 复杂度）

适合：初始化全库（一次性 100 万条）。

---

## 4. 缓存：避免重复 embed

文档没变就别重 embed：

```python
import hashlib


def embed_with_cache(text):
    key = hashlib.md5(text.encode()).hexdigest()
    cached = cache.get(f"embed:{key}")
    if cached:
        return cached
    
    vec = embed(text)
    cache.set(f"embed:{key}", vec, ttl=86400 * 30)
    return vec
```

**实战**：

- 文档库的 chunk 文本作为 cache key
- TTL 30 天起
- Redis / 文件 cache 都行

---

## 5. 自部署模型

如果你已经有 GPU，自部署比 OpenAI 便宜得多：

```
text-embedding-3-small (OpenAI):
  $0.02 / 1M tokens

BGE-large-zh-v1.5 (自部署 A100):
  电费 + 折旧 ~$1/小时
  吞吐 ~3000 sentences/秒（avg 200 tokens）
  → ~$0.0001 / 1M tokens
  便宜 200x
```

详见 [02-models/03-open-source.md](../02-models/03-open-source.md) + [04-sentence-transformers.md](../02-models/04-sentence-transformers.md)。

---

## 6. 选对维度

```
text-embedding-3-large @ 3072 维 → $0.13 / 1M tokens
text-embedding-3-large @ 1024 维 (Matryoshka) → $0.13 / 1M tokens

Wait，价格一样？
```

是的，OpenAI Matryoshka 维度不影响 API 费用。但：

- 存储 × 3
- 检索慢 × 3

所以截维省的是**下游**成本（存储 + 查询），不是 embed cost。

---

## 7. 文档预处理

embed 前过滤垃圾：

```python
def is_worth_embedding(text):
    if len(text) < 30:        # 太短
        return False
    if len(text) > 8000:      # 太长，要切
        return False
    if not has_meaningful_content(text):  # 全是 stopword
        return False
    return True


texts = [t for t in raw_texts if is_worth_embedding(t)]
```

每节省 1000 条 = 几美分。百万级累积。

---

## 8. 增量 embed 监控

```python
class EmbedCostTracker:
    def __init__(self):
        self.total_tokens = 0
        self.total_calls = 0
    
    def record(self, n_tokens):
        self.total_tokens += n_tokens
        self.total_calls += 1
    
    def estimate_cost(self, price_per_1m=0.02):
        return self.total_tokens / 1_000_000 * price_per_1m
    
    def report(self):
        print(f"Calls: {self.total_calls}")
        print(f"Tokens: {self.total_tokens:,}")
        print(f"Cost: ${self.estimate_cost():.2f}")


tracker = EmbedCostTracker()

# embed 时
tracker.record(resp.usage.total_tokens)

# 收尾
tracker.report()
```

---

## 9. Cohere / Voyage Batch

Cohere `embed` 单次最多 96 个 doc：

```python
import cohere
co = cohere.Client()


def embed_cohere_batch(texts, model="embed-multilingual-v3.0"):
    embeddings = []
    for i in range(0, len(texts), 96):
        batch = texts[i:i+96]
        resp = co.embed(texts=batch, model=model, input_type="search_document")
        embeddings.extend(resp.embeddings)
    return embeddings
```

VoyageAI 类似但限制不同（看 doc）。

---

## 10. 完整 cost 估算

100 万条 docs × 平均 500 字 ≈ 500M tokens（中文 1 字 ≈ 1 token）：

| 方案 | Cost |
|------|------|
| OpenAI 3-small (API) | $10 |
| OpenAI 3-small (Batch API 50% off) | $5 |
| OpenAI 3-large (API) | $65 |
| Cohere multilingual | $50 |
| 自部署 BGE-large (1 A100 4小时) | ~$8 |

**自部署对量大场景压倒性优势**——但需要前期搭建。

---

## 11. 节流策略

```python
# 防止瞬时高并发烧光预算
class BudgetGuard:
    def __init__(self, daily_max_usd=10):
        self.daily_max = daily_max_usd
        self.spent_today = 0
    
    def check(self, est_cost):
        if self.spent_today + est_cost > self.daily_max:
            raise BudgetExceeded(f"Daily budget {self.daily_max} would be exceeded")
        self.spent_today += est_cost


guard = BudgetGuard(daily_max_usd=50)

for batch in batches:
    est = estimate_cost(batch)
    guard.check(est)
    embed(batch)
```

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| 用 batch size 1 跑生产 | 至少 100，最优 256-512 |
| 顺序跑不并发 | asyncio.gather + Semaphore |
| 没 cache 重复 embed | 必做 |
| 不监控 cost | 每天 budget alert |
| API 限流没 retry | exponential backoff |

---

## 13. 下一步

- 📖 缓存策略 → [03-caching.md](./03-caching.md)
- 📖 部署形态 → [04-deployment.md](./04-deployment.md)
- 📖 监控 → [05-monitoring.md](./05-monitoring.md)
