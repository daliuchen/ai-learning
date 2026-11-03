# 批量 Embed + Cost 优化

> 百万级文档第一次入库，可能要跑好几个小时、花几十美元。用好批量 API、并发、缓存，再选对模型，这笔开销通常能省掉一半。

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

OpenAI 的限制：单次 input list 最多 2048 项，单条最多 8191 tokens，一次请求所有输入合计不超过 300k tokens。

batch_size 建议取 96-512，具体看 token 总量和你能接受的延迟。

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

这类任务的上限通常是账号的 TPM（每分钟 token 数），不是并发数。按第 10 节的口径，100 万条约 500M tokens：如果账号的 embedding 限额是 1M TPM，最快也要 500 分钟（8 个多小时），并发开再多也只会被 429 挡回来；要在 1 小时内跑完，需要 10M TPM 左右的限额。自己账号的 RPM / TPM 以 OpenAI 控制台的 Limits 页为准，并发数按它倒推，再加上重试和指数退避。

---

## 3. OpenAI Batch API（50% 折扣）

量大又不着急的 embed 任务，可以走 Batch API：

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
    if batch.status in ("failed", "expired", "cancelled"):
        raise RuntimeError(f"batch {batch.id} {batch.status}")
    time.sleep(60)


# 4. 下载结果
result_file = client.files.content(batch.output_file_id)
results = [json.loads(l) for l in result_file.text.strip().split("\n")]
```

好处是价格打五折（OpenAI 定价页上 Batch API 统一按标准价的 50% 计费），而且走单独的批处理额度，不占在线接口的 TPM。代价是结果最长要等 24 小时，流程也更复杂，只有数据量大时才划算。最合适的场景是初始化全库。注意单个 batch 有规模上限（embedding 批次合计最多 5 万条输入，文件最大 200MB，以 Batch API 文档为准），100 万条要拆成 20 个以上的 batch 分别提交。

---

## 4. 缓存：避免重复 embed

文档没变，就不要再 embed 一次：

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

实际做法：用 chunk 文本当 cache key，TTL 从 30 天起步，Redis 或文件缓存都可以。

---

## 5. 自部署模型

如果手上已经有 GPU，自部署比调 OpenAI 便宜得多：

```
text-embedding-3-small (OpenAI):
  $0.02 / 1M tokens

BGE-large-zh-v1.5 (自部署 A100):
  电费 + 折旧 ~$1/小时
  吞吐 ~3000 sentences/秒（avg 200 tokens）≈ 60 万 tokens/秒 ≈ 21.6 亿 tokens/小时
  → $1 / 2160M tokens ≈ $0.0005 / 1M tokens
  便宜约 40x（示意估算，吞吐和成本以你的硬件实测为准）
```

模型选择和用法见 [开源模型：Qwen3-Embedding / BGE / Jina / Nomic](../02-models/03-open-source.md) 和 [sentence-transformers 工具链](../02-models/04-sentence-transformers.md)。

---

## 6. 选对维度

```
text-embedding-3-large @ 3072 维 → $0.13 / 1M tokens
text-embedding-3-large @ 1024 维 (Matryoshka) → $0.13 / 1M tokens
```

两行价格一样。

OpenAI 按输入 token 计费，`dimensions` 截维不影响 API 费用；但 3072 维的存储是 1024 维的 3 倍，暴力检索的计算量也大致按维度线性增长。所以截维省下的是下游的存储和查询成本，不是 embed 本身的费用。

---

## 7. 文档预处理

embed 之前先把垃圾内容过滤掉：

```python
def is_worth_embedding(text):
    if len(text) < 30:        # 太短
        return False
    if not has_meaningful_content(text):  # 全是 stopword
        return False
    return True


texts = []
for t in raw_texts:
    if not is_worth_embedding(t):
        continue
    if len(t) > 8000:         # 太长：先切块再 embed，不要直接丢掉
        texts.extend(split_into_chunks(t, max_chars=2000))
    else:
        texts.append(t)
```

按 3-small 的价格，每条 500 tokens 的话，少 embed 1000 条只省大约 1 美分，单看 API 费用不多；过滤的主要收益是索引里少了噪声 chunk，存储和检索质量都受益。

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

Cohere `embed` 单次最多 96 个 doc（以当前 API 参考为准；运行下面的代码需要 Cohere API key）。千问 `text-embedding-v4` 的兼容接口每次最多 10 条：

```python
import cohere
co = cohere.ClientV2()


def embed_cohere_batch(texts, model="embed-v5.0-pro"):
    embeddings = []
    for i in range(0, len(texts), 96):
        batch = texts[i:i+96]
        resp = co.embed(texts=batch, model=model, input_type="search_document", embedding_types=["float"])
        embeddings.extend(resp.embeddings.float_)
    return embeddings
```

VoyageAI 的做法类似，只是限制数字不同，具体看它的文档。

---

## 10. 完整 cost 估算

100 万条文档，平均每条 500 字，约 500M tokens（中文 1 字约 0.7–1 个 token，视 tokenizer 而定，这里按 1:1 粗估）：

| 方案 | Cost |
|------|------|
| OpenAI 3-small (API) | $10 |
| OpenAI 3-small (Batch API，五折) | $5 |
| OpenAI 3-large (API) | $65 |
| 千问 text-embedding-v4（官方标价 0.5 元/百万 token，Batch API 0.25 元/百万；2026-11） | 约 ¥250 / ¥125 |
| 自部署 BGE-large (1 张 A100) | 按第 5 节的示意吞吐约 15 分钟跑完，GPU 费用不到 $1；加上部署、调试和机器空转，实际按小时计（示意） |

只看计算费用，自部署便宜一个数量级以上；但 100 万条的一次性入库用 API 也只要 $5–65，自部署的优势要在反复重建索引、持续大量入库时才体现出来，前提是愿意投入搭建和运维。

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
| 生产上用 batch size 1 跑 | 至少 100，256-512 最好 |
| 顺序跑，没有并发 | asyncio.gather + Semaphore |
| 没加缓存，重复 embed | 一定要加 |
| 不监控费用 | 每天设预算告警 |
| API 限流后不重试 | exponential backoff |

---

## 13. 下一步

- [缓存策略：Embedding Cache / Query Cache / Result Cache](./03-caching.md)
- [部署形态选型](./04-deployment.md)
- [监控：召回率 / Latency / Cost](./05-monitoring.md)
