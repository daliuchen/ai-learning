# OpenAI text-embedding-3 系列

> **一句话**：OpenAI 当前 GA 的 embedding 是 `text-embedding-3-small` / `text-embedding-3-large`，比 v2（ada-002）便宜且强，**支持 Matryoshka 截维**，是商业 API 的默认选择。

---

## 1. 三个模型对比

| 模型 | Dim 默认 | MTEB avg | 单价 (per 1M tokens) | 备注 |
|------|----------|----------|---------------------|------|
| text-embedding-ada-002 | 1536 | ~60.99 | $0.10 | 老版本，**不要再用** |
| text-embedding-3-small | 1536 | ~62.26 | $0.02 | 默认选这个 |
| text-embedding-3-large | 3072 | ~64.59 | $0.13 | 质量敏感场景 |

3-small 比 ada-002 便宜 5 倍且更好——**不要再用 ada-002**。

---

## 2. 最简用法

```python
from openai import OpenAI

client = OpenAI()


def embed(text: str, model: str = "text-embedding-3-small") -> list[float]:
    resp = client.embeddings.create(model=model, input=text)
    return resp.data[0].embedding


vec = embed("如何取消订阅")
print(len(vec))  # 1536
```

---

## 3. 批量

```python
texts = ["第一段", "第二段", "第三段", ...]

resp = client.embeddings.create(
    model="text-embedding-3-small",
    input=texts,  # ← 直接传 list
)

vecs = [d.embedding for d in resp.data]
```

**限制**：

- 单次 input 最多 **2048** 项
- 每项最多 **8191** tokens
- 推荐 batch size 96-512（看延迟 vs 吞吐）

---

## 4. Matryoshka：截维

OpenAI 3-small / 3-large 训练时用了 Matryoshka——**前 N 维可以单独使用**：

```python
# 截到 512 维
resp = client.embeddings.create(
    model="text-embedding-3-large",
    input="如何取消订阅",
    dimensions=512,   # ← 任意 256-3072
)
print(len(resp.data[0].embedding))  # 512
```

省存储、加速检索。质量损失参考：

| 模型 | 全维 MTEB | 256 维 | 512 维 | 1024 维 |
|------|-----------|--------|--------|---------|
| 3-large (3072) | 64.6 | ~59.0 | ~62.0 | ~63.5 |
| 3-small (1536) | 62.3 | ~58.6 | ~61.0 | - |

**实战推荐**：

- 量大 → 3-large 截到 1024 维（性价比最佳）
- 量小 → 3-small 1536 维（最简单）

---

## 5. 异步并发

```python
import asyncio
from openai import AsyncOpenAI


client = AsyncOpenAI()


async def embed_batch(texts):
    resp = await client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    return [d.embedding for d in resp.data]


async def main(all_texts, batch=256):
    chunks = [all_texts[i:i+batch] for i in range(0, len(all_texts), batch)]
    results = await asyncio.gather(*[embed_batch(c) for c in chunks])
    return [v for r in results for v in r]


vecs = asyncio.run(main(my_texts))
```

并发跑提速明显。注意：

- **Rate limit**：免费 / Tier 1 都有限制（看 https://platform.openai.com/settings/limits）
- 加重试 / 指数退避防限流

---

## 6. 跟 ada-002 兼容

如果你已经索引了一堆 ada-002 embedding：

| 选项 | 怎么做 |
|------|--------|
| 全量迁移到 3-small | 重建所有 embedding（最干净） |
| 渐进迁移 | 新数据用 3-small，老数据保留 ada-002 → 两套并存 |
| 维持现状 | 不动，但成本是新模型 5 倍 |

⚠️ **不能在同一个 collection 里混存**——dim 一样（都 1536）但**向量分布完全不同**，相似度不可比。

---

## 7. cost 估算

```python
def estimate_cost(num_chars: int, model="text-embedding-3-small") -> float:
    """rough：1 token ≈ 4 chars（英文）or 1.5 chars（中文）"""
    tokens = num_chars / 3  # 折中
    prices = {
        "text-embedding-3-small": 0.02,
        "text-embedding-3-large": 0.13,
        "text-embedding-ada-002": 0.10,
    }
    return tokens / 1_000_000 * prices[model]


# 100 万条文档，每条平均 500 字
total_chars = 1_000_000 * 500
print(f"3-small: ${estimate_cost(total_chars, 'text-embedding-3-small'):.2f}")
# 3-small: ~$3.33

print(f"3-large: ${estimate_cost(total_chars, 'text-embedding-3-large'):.2f}")
# 3-large: ~$21.67
```

百万级文档预算几美元——很便宜。

---

## 8. 性能 / latency

OpenAI API：

- 单次 embedding 单条：~100-300ms（包含网络）
- batch 256 条：~500-800ms
- 大 batch 摊薄更好

如果 latency 敏感（< 100ms）：

- 本地部署小模型（all-MiniLM / bge-small）
- 或缓存 query 端 embedding（详见 [07-production/03-caching.md](../07-production/03-caching.md)）

---

## 9. 跟 v1（ada-002）的关键差异

| 维度 | ada-002 | 3-series |
|------|---------|----------|
| Matryoshka | ❌ | ✅ |
| 多语言 | 较弱 | 改进 |
| MTEB | 61.0 | 62.3-64.6 |
| 价格 | $0.10/M | $0.02/M（3-small）|
| dim 可调 | 固定 1536 | 256-3072 任意 |

迁移：

```python
# 旧：
client.embeddings.create(model="text-embedding-ada-002", input=texts)

# 新：
client.embeddings.create(model="text-embedding-3-small", input=texts)
```

API 完全兼容，改个 model 名就行。

---

## 10. 同步 vs 异步 demo

```python
# demos/models/01_openai.py
import asyncio
import time
from openai import OpenAI, AsyncOpenAI


sync_client = OpenAI()
async_client = AsyncOpenAI()


texts = [f"测试文本 #{i}" for i in range(1000)]


# 同步串行
def sync_embed():
    t = time.time()
    vecs = []
    for chunk_start in range(0, len(texts), 256):
        chunk = texts[chunk_start:chunk_start + 256]
        resp = sync_client.embeddings.create(model="text-embedding-3-small", input=chunk)
        vecs.extend([d.embedding for d in resp.data])
    print(f"Sync: {time.time() - t:.2f}s")
    return vecs


# 异步并发
async def async_embed():
    t = time.time()
    tasks = []
    for chunk_start in range(0, len(texts), 256):
        chunk = texts[chunk_start:chunk_start + 256]
        tasks.append(async_client.embeddings.create(model="text-embedding-3-small", input=chunk))
    resps = await asyncio.gather(*tasks)
    vecs = [d.embedding for r in resps for d in r.data]
    print(f"Async: {time.time() - t:.2f}s")
    return vecs


sync_embed()
asyncio.run(async_embed())
```

异步通常 3-5x 快（受 rate limit 约束）。

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| ada-002 索引升到 3-series | 重建，不要混 |
| 单 input > 8191 tokens | 先切（详见 [04-chunking](../04-chunking)） |
| 限流 | 加重试 + 看 tier 限制 |
| 截维想"动态切" | 必须 dim 一致，要不就重建 |

---

## 12. 何时不选 OpenAI

- 数据合规要求不出公司 → 自部署 BGE
- 极致 latency（< 50ms）→ 自部署小模型
- 跨语言重 → Cohere multilingual-v3

详见 [06-mteb-selection.md](./06-mteb-selection.md)。

---

## 13. 下一步

- 📖 Cohere / Voyage 对比 → [02-cohere-voyage.md](./02-cohere-voyage.md)
- 📖 开源 SOTA → [03-open-source.md](./03-open-source.md)
- 📖 rerank 补强 → [05-rerank.md](./05-rerank.md)
