# Cohere / VoyageAI：商业 API 替代

> **一句话**：Cohere `embed-v3` 在多语言 / 跨语言强，VoyageAI `voyage-3` 在英文 / 代码场景 MTEB 屠榜——这两家都是 OpenAI 之外值得考虑的商业 embedding API。

---

## 1. 三家速览

| 厂商 | 旗舰模型 | dim | MTEB avg | 单价/1M |
|------|----------|-----|----------|---------|
| OpenAI | text-embedding-3-large | 3072 | 64.6 | $0.13 |
| Cohere | embed-english-v3.0 | 1024 | ~64.5 | $0.10 |
| Cohere | embed-multilingual-v3.0 | 1024 | - | $0.10 |
| VoyageAI | voyage-3 | 1024 | ~65.5 | $0.06 |
| VoyageAI | voyage-3-large | 1024 | ~67 | $0.18 |
| VoyageAI | voyage-code-3 | 1024 | - | $0.18 |

---

## 2. Cohere 用法

```python
import cohere

co = cohere.Client()


def embed_cohere(texts, input_type="search_document", model="embed-english-v3.0"):
    resp = co.embed(
        texts=texts,
        model=model,
        input_type=input_type,
    )
    return resp.embeddings


# 文档（建索引时）
doc_vecs = embed_cohere(my_docs, input_type="search_document")

# Query（搜索时）
query_vec = embed_cohere(["如何取消订阅"], input_type="search_query")[0]
```

⚠️ **必须区分 input_type**：

| input_type | 用于 |
|------------|------|
| `search_document` | 建索引（文档侧） |
| `search_query` | 检索时（查询侧） |
| `classification` | 分类任务 |
| `clustering` | 聚类任务 |

模型对不同 type 的输出**不同**——同一段文字用错类型，相似度会偏。

---

## 3. Cohere 模型清单

| 模型 | 语言 | dim | 备注 |
|------|------|-----|------|
| `embed-english-v3.0` | 英文 | 1024 | 英文最强 |
| `embed-multilingual-v3.0` | 100+ | 1024 | 多语言 / 跨语言 |
| `embed-english-light-v3.0` | 英文 | 384 | 小模型，快 |
| `embed-multilingual-light-v3.0` | 100+ | 384 | 小+多语 |

---

## 4. VoyageAI 用法

```python
import voyageai

vo = voyageai.Client()


resp = vo.embed(
    texts=["如何取消订阅"],
    model="voyage-3",
    input_type="document",  # 或 "query"
)
vecs = resp.embeddings
```

类似 Cohere，要分 `document` / `query`。

---

## 5. VoyageAI 模型清单

| 模型 | 用途 | dim |
|------|------|-----|
| `voyage-3` | 通用，强 | 1024 |
| `voyage-3-large` | 最强，质量敏感 | 1024 |
| `voyage-3-lite` | 便宜，多语言 | 512 |
| `voyage-code-3` | **代码专用** | 1024 |
| `voyage-finance-2` | 金融领域 | 1024 |
| `voyage-law-2` | 法律领域 | 1024 |
| `voyage-multimodal-3` | 多模态（图+文） | 1024 |

VoyageAI 强在**领域专用模型**——金融 / 法律 / 代码 finetune 过的版本比通用模型领先 5-10 分。

---

## 6. Cohere vs Voyage vs OpenAI 抉择树

```
英文为主，质量敏感：
  → voyage-3-large（最强）
  → 或 text-embedding-3-large

英文，预算敏感：
  → voyage-3（$0.06/M，比 OpenAI 便宜）
  → 或 cohere-embed-english-v3

多语言 / 跨语言：
  → cohere-embed-multilingual-v3（业界共识最强）
  → 或 voyage-3-lite

代码：
  → voyage-code-3（专门优化）

领域专（金融 / 法律 / 医疗）：
  → voyage-finance-2 / law-2 等专用版

中文为主：
  → 通常自部署 BGE-zh / GTE-zh 更划算（详见 03-open-source）

数据合规出不去：
  → 必须自部署（详见 03-open-source）
```

---

## 7. 完整对比 demo

```python
# demos/models/02_cohere_voyage.py
import numpy as np
from openai import OpenAI
import cohere
import voyageai


openai_client = OpenAI()
co = cohere.Client()
vo = voyageai.Client()


def embed_openai(texts):
    resp = openai_client.embeddings.create(model="text-embedding-3-small", input=texts)
    return np.array([d.embedding for d in resp.data])


def embed_cohere(texts, is_query=False):
    input_type = "search_query" if is_query else "search_document"
    resp = co.embed(texts=texts, model="embed-multilingual-v3.0", input_type=input_type)
    return np.array(resp.embeddings)


def embed_voyage(texts, is_query=False):
    input_type = "query" if is_query else "document"
    resp = vo.embed(texts=texts, model="voyage-3", input_type=input_type)
    return np.array(resp.embeddings)


query = "如何取消订阅"
docs = [
    "如何关闭自动续费",
    "如何登录账号",
    "停止订阅的方法",
    "重置密码教程",
]


for name, fn_q, fn_d in [
    ("OpenAI", lambda q: embed_openai([q])[0], lambda d: embed_openai(d)),
    ("Cohere", lambda q: embed_cohere([q], is_query=True)[0], lambda d: embed_cohere(d)),
    ("Voyage", lambda q: embed_voyage([q], is_query=True)[0], lambda d: embed_voyage(d)),
]:
    qv = fn_q(query)
    dvs = fn_d(docs)
    sims = dvs @ qv
    top = np.argsort(-sims)
    print(f"\n[{name}]")
    for i in top:
        print(f"  {docs[i]:<25} sim={sims[i]:.4f}")
```

3 家都应该把"停止订阅的方法"和"如何关闭自动续费"排前两。

---

## 8. 限流 & 重试

```python
import time
import cohere
from cohere.errors import TooManyRequestsError


co = cohere.Client()


def embed_with_retry(texts, max_retries=5):
    for attempt in range(max_retries):
        try:
            return co.embed(texts=texts, model="embed-english-v3.0", input_type="search_document").embeddings
        except TooManyRequestsError:
            wait = 2 ** attempt
            print(f"Rate limited, sleeping {wait}s")
            time.sleep(wait)
    raise RuntimeError("max retries exceeded")
```

每家都有不同 tier 的 RPM / TPM 限制——按合同看。

---

## 9. 跨家迁移注意

```
OpenAI 1536 维 → Cohere 1024 维：
  ❌ 必须重建（dim 不同）

Cohere v2 → v3：
  ❌ 必须重建（向量分布不同）

切换 input_type（如 document → classification）：
  ⚠️ 同一模型但 output 不同，需重 embed
```

**实战**：选型时一并考虑"未来要不要换"——量大场景一次重建几小时 + 数十美元。

---

## 10. 数据合规

- **OpenAI**：默认数据不用于训练（zero data retention），但传海外
- **Cohere**：同上，可选 AWS / Azure / Oracle 部署
- **VoyageAI**：可选 zero-retention（额外配置）

合规要求出不了海外 → 自部署（详见 [03-open-source.md](./03-open-source.md)）。

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| Cohere 不指定 `input_type` | 默认 `search_document`，query 时召回偏 |
| Voyage 用 query 模型 embed 文档 | 反过来错位 |
| 切模型后没重建索引 | 召回会崩 |
| 多语言场景用单语模型 | 跨语言效果差 |

---

## 12. 下一步

- 📖 开源 SOTA（自部署）→ [03-open-source.md](./03-open-source.md)
- 📖 sentence-transformers 工具链 → [04-sentence-transformers.md](./04-sentence-transformers.md)
- 📖 选型决策完整版 → [06-mteb-selection.md](./06-mteb-selection.md)
