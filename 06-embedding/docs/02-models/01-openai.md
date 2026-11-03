# OpenAI text-embedding-3 系列

> OpenAI 目前正式提供的 embedding 模型是 `text-embedding-3-small` 和 `text-embedding-3-large`。它们比上一代 ada-002 便宜，效果也更好，还支持 Matryoshka 截维，所以很多团队把它当作商业 API 的默认选项。这篇讲它们的用法、成本，以及什么情况下不该选它。

截至 2026 年 11 月，OpenAI 官方 embeddings 文档仍只列 3 个模型（3-small / 3-large / ada-002），没有出新一代；下表数字和价格来自官方 [embeddings 指南](https://developers.openai.com/api/docs/guides/embeddings)与[定价页](https://developers.openai.com/api/docs/pricing)。这个系列 2024 年发布后一直没换代，同期 Cohere、Voyage、Qwen、Jina 都已更新到下一代（见[Cohere 与 VoyageAI：OpenAI 之外的商业 API](02-cohere-voyage.md)和[开源模型：Qwen3-Embedding / BGE / Jina / Nomic](03-open-source.md)）。它今天还是很多人的默认选择，更多是因为生态通用，并非榜单领先。

---

## 1. 三个模型对比

| 模型 | Dim 默认 | MTEB avg（官方页） | 单价 (per 1M tokens，官方定价页) | 备注 |
|------|----------|----------|---------------------|------|
| text-embedding-ada-002 | 1536 | 61.0 | $0.10 | 老版本，别再用 |
| text-embedding-3-small | 1536 | 62.3 | $0.02 | 默认选这个 |
| text-embedding-3-large | 3072 | 64.6 | $0.13 | 质量敏感场景 |

3-small 比 ada-002 便宜 5 倍，效果还更好，**新项目不要再用 ada-002**。

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

几个限制：

- 单次 input 最多 2048 项
- 每项最多 8191 tokens
- 一次请求所有输入合计不超过 300k tokens
- batch size 建议 96-512，按你更在意延迟还是吞吐来定

---

## 4. Matryoshka：截维

3-small 和 3-large 训练时用了 Matryoshka 方法，前 N 维可以单独拿出来用：

```python
# 截到 512 维
resp = client.embeddings.create(
    model="text-embedding-3-large",
    input="如何取消订阅",
    dimensions=512,   # ← 不超过原生维度的任意值（3-large 3072，3-small 1536）
)
print(len(resp.data[0].embedding))  # 512
```

截维能省存储，也能加快检索。下表是 OpenAI 发布这组模型时公布的 MTEB（v1）平均分，"-" 表示官方没给，其他维度的损失请用自己的 evalset 测：

| 模型 | 全维 MTEB | 256 维 | 512 维 | 1024 维 |
|------|-----------|--------|--------|---------|
| 3-large (3072) | 64.6 | 62.0 | - | 64.1 |
| 3-small (1536) | 62.3 | - | 61.6 | - |

3-large 截到 256 维（62.0）仍然比 ada-002 全维（61.0）高。我的建议：数据量大、对质量敏感，用 3-large 截到 1024 维，分数比 3-small 全维高、存储还小三分之一，代价是 API 单价贵 6.5 倍；数据量小，直接用 3-small 的 1536 维，省事。

---

## 5. OpenAI 兼容接口：用千问 text-embedding-v4 跑同一份代码

`openai` SDK 改一下 `base_url`，就能调用任何兼容 `/embeddings` 的服务。本手册的示例统一用阿里云百炼的 `text-embedding-v4`，国内能直连，中文效果好，也能截维：

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["DASHSCOPE_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

resp = client.embeddings.create(
    model="text-embedding-v4",
    input=["如何关闭自动续费", "重置密码教程"],   # 兼容接口一次最多 10 条
    dimensions=512,                              # 可选 2048/1536/1024(默认)/768/512/256/128/64
)
print(len(resp.data[0].embedding))  # 512
```

百炼官方文档（2026 年 11 月）对 `text-embedding-v4` 的说明：单条最长 8192 token，每次请求最多 10 条（比 OpenAI 的 2048 条小得多，批量时要自己分批），支持 100+ 语言；北京地域标价 0.0005 元 / 千 token（价格会变，以[官方页](https://help.aliyun.com/zh/model-studio/embedding)为准）。官方给出的分数：1024 维 MTEB 68.36 / CMTEB 70.14，2048 维 71.58 / 71.99。另有更新的 `qwen3.7-text-embedding`（最高 2560 维）和多模态 `qwen3-vl-embedding`，选型时可以一起评估。

注意：走兼容接口只能用 `dimensions` 截维，`text_type`、`instruct` 和稀疏向量输出只有 DashScope 原生 SDK 才支持。

---

## 6. 异步并发

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

并发能明显提速，但要留意 rate limit：免费档和 Tier 1 都有额度限制，具体看 OpenAI 控制台的 limits 页面。代码里最好加上重试和指数退避，避免被限流打断。

---

## 7. 跟 ada-002 兼容

如果你已经用 ada-002 建好了一批索引：

| 选项 | 怎么做 |
|------|--------|
| 全量迁移到 3-small | 重建所有 embedding（最干净） |
| 渐进迁移 | 新数据用 3-small，老数据保留 ada-002，两套并存 |
| 维持现状 | 不动，但成本是新模型 5 倍 |

两种模型的向量不能混存在同一个 collection 里。维度虽然都是 1536，但向量分布完全不同，相似度没法比。

---

## 8. cost 估算

```python
def estimate_cost(num_chars: int, model="text-embedding-3-small", lang="zh") -> float:
    """粗估：英文约 4 个字符 1 token；中文约 1 个字 1 token（cl100k 下常见汉字多为 1 token，生僻字更多）"""
    tokens = num_chars if lang == "zh" else num_chars / 4
    prices = {
        "text-embedding-3-small": 0.02,
        "text-embedding-3-large": 0.13,
        "text-embedding-ada-002": 0.10,
    }
    return tokens / 1_000_000 * prices[model]


# 100 万条中文文档，每条平均 500 字 ≈ 500M tokens
total_chars = 1_000_000 * 500
print(f"3-small: ${estimate_cost(total_chars, 'text-embedding-3-small'):.2f}")
# 3-small: $10.00

print(f"3-large: ${estimate_cost(total_chars, 'text-embedding-3-large'):.2f}")
# 3-large: $65.00
```

百万级中文文档全量 embed 一次，3-small 大约 $10，3-large 大约 $65（和[批量 Embed + Cost 优化](../07-production/02-batch-cost.md)第 10 节的算法一致）。

---

## 9. 性能 / latency

调用 OpenAI API 的大致耗时（量级参考，随网络、地域和服务端负载变化）：

- 单条 embedding：~100-300ms（含网络）
- batch 256 条：~500-800ms
- batch 越大，单条摊下来越划算

如果要求延迟在 100ms 以内，可以本地部署小模型（all-MiniLM / bge-small），或者缓存 query 端的 embedding（见[缓存策略：Embedding Cache / Query Cache / Result Cache](../07-production/03-caching.md)）。

---

## 10. 跟 v1（ada-002）的主要差异

| 维度 | ada-002 | 3-series |
|------|---------|----------|
| Matryoshka | 不支持 | 支持 |
| 多语言 | 较弱 | 改进 |
| MTEB | 61.0 | 62.3-64.6 |
| 价格 | $0.10/M | $0.02/M（3-small）|
| dim 可调 | 固定 1536 | 可截到不超过原生维度的任意值 |

迁移代码：

```python
# 旧：
client.embeddings.create(model="text-embedding-ada-002", input=texts)

# 新：
client.embeddings.create(model="text-embedding-3-small", input=texts)
```

API 完全兼容，改个 model 名就行。

---

## 11. 同步和异步的速度对比

```python
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

这个例子只有 4 个批次，异步最多快到接近 4 倍；批次多的时候，上限取决于 rate limit。

---

## 12. 常见坑

| 坑 | 解 |
|----|----|
| ada-002 索引升到 3-series | 重建，不要混 |
| 单 input > 8191 tokens | 先切分（见[为什么 Chunking 决定 RAG 的上限](../04-chunking/01-why-chunking.md)） |
| 限流 | 加重试，确认自己所在 tier 的额度 |
| 想对已有向量"动态"换维度 | 维度必须一致，否则只能重建 |

---

## 13. 何时不选 OpenAI

- 合规要求数据不出公司：自部署 BGE
- 延迟要压到 50ms 以内：自部署小模型
- 跨语言检索占比高：Cohere embed-v5.0、千问 text-embedding-v4、Qwen3-Embedding 这类多语言模型

选型细节见 [MTEB 怎么看 + 选型决策树](./06-mteb-selection.md)。

---

## 14. 下一步

- Cohere / Voyage 对比：[Cohere 与 VoyageAI：OpenAI 之外的商业 API](./02-cohere-voyage.md)
- 开源模型：[开源模型：Qwen3-Embedding / BGE / Jina / Nomic](./03-open-source.md)
- 用 rerank 补强：[Rerank 模型：召回之后的精排](./05-rerank.md)
