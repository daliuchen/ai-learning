# Cohere 与 VoyageAI：OpenAI 之外的商业 API

> 除了 OpenAI，商业 embedding API 里最值得看的是 Cohere 和 VoyageAI。Cohere 的长处在多语言、多模态（能处理图文混合的 PDF）和长上下文；Voyage 的长处在检索质量，以及代码、金融、法律这类领域专用模型。这篇对比两家的模型和用法，帮你判断要不要换掉 OpenAI。

版本：以 `cohere 7.3.0`、`voyageai 0.5.0` 和两家 2026 年 11 月的官方文档（[Cohere embed](https://docs.cohere.com/docs/cohere-embed)、[Voyage embeddings](https://docs.voyageai.com/docs/embeddings)）为准；旗舰已换成 embed-v5.0 和 voyage-4，价格请看各自定价页。

---

## 1. 速览

| 厂商 | 当前模型（官方文档列出） | 默认 dim | 上下文 | 特点 |
|------|----------|-----|--------|------|
| OpenAI | text-embedding-3-large / small | 3072 / 1536 | 8191 | 生态通用，未换代 |
| Cohere | `embed-v5.0-pro` / `embed-v5.0-fast` | 2048（可选 256-1536） | 128k | 文本+图片+图文混合；pro 建索引、fast 查询（共享向量空间） |
| Cohere | `embed-v4.0` | 1536（可选 256/512/1024） | 128k | 上一代，仍可用 |
| Cohere | `embed-multilingual-v3.0` 等 v3 | 1024 / 384(light) | 512 | 老模型，上下文短 |
| VoyageAI | `voyage-4-large` / `voyage-4` / `voyage-4-lite` | 1024（可选 256/512/2048） | 32k | 4 系列向量彼此兼容（大模型建索引、lite 查询可混用） |
| VoyageAI | `voyage-code-4` | 1024 | 32k | 代码检索 |
| VoyageAI | `voyage-finance-2` / `voyage-law-2` | 1024 | 32k / 16k | 领域专用 |
| VoyageAI | `voyage-4-nano` | 同 4 系列 | 32k | 开源权重（Hugging Face） |

---

## 2. Cohere 用法（v2 客户端）

```python
import os
import cohere

co = cohere.ClientV2(api_key=os.environ["COHERE_API_KEY"])


def embed_cohere(texts, input_type="search_document", model="embed-v5.0-pro"):
    resp = co.embed(
        texts=texts,
        model=model,
        input_type=input_type,
        embedding_types=["float"],
    )
    return resp.embeddings.float_      # v2 返回按类型分组的对象


# 文档（建索引时）
doc_vecs = embed_cohere(my_docs, input_type="search_document")

# Query（搜索时）——官方建议用 fast 模型查询，与 pro 共享向量空间
query_vec = embed_cohere(["如何取消订阅"], input_type="search_query", model="embed-v5.0-fast")[0]
```

**一定要区分 input_type**，v2 接口对 v3 及以上的模型要求必填，不传会直接报错：

| input_type | 用于 |
|------------|------|
| `search_document` | 建索引（文档侧） |
| `search_query` | 检索时（查询侧） |
| `classification` | 分类任务 |
| `clustering` | 聚类任务 |
| `image` | 图片输入 |

模型对不同 type 给出的向量不一样，同一段文字用错了类型，相似度就会偏。截维用 `output_dimension=`。

---

## 3. Cohere 模型清单

| 模型 | 模态 | dim | 备注 |
|------|------|-----|------|
| `embed-v5.0-pro` | 文本/图片/混合 | 2048 默认 | 最新旗舰，128k 上下文 |
| `embed-v5.0-fast` | 文本/图片/混合 | 2048 默认 | 低延迟，适合查询侧 |
| `embed-v4.0` | 文本/图片/混合 | 1536 默认 | 上一代 |
| `embed-multilingual-v3.0` | 文本，100+ 语言 | 1024 | 老模型，512 token |
| `embed-english-v3.0` | 英文 | 1024 | 老模型，512 token |
| `*-light-v3.0` | 英文 / 多语 | 384 | 老模型小版本 |

---

## 4. VoyageAI 用法

```python
import os
import voyageai

vo = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])


resp = vo.embed(
    texts=["如何取消订阅"],
    model="voyage-4",
    input_type="document",  # 或 "query"
    output_dimension=1024,  # 可选 256 / 512 / 1024 / 2048
)
vecs = resp.embeddings
```

和 Cohere 一样，要区分 `document` 和 `query`。

---

## 5. VoyageAI 模型清单

| 模型 | 用途 | dim |
|------|------|-----|
| `voyage-4-large` | 通用 + 多语言，质量最高 | 1024（256/512/2048） |
| `voyage-4` | 通用 + 多语言 | 1024 |
| `voyage-4-lite` | 延迟 / 成本优先 | 1024 |
| `voyage-code-4` | 代码专用 | 1024 |
| `voyage-finance-2` | 金融领域 | 1024 |
| `voyage-law-2` | 法律领域 | 1024 |
| `voyage-4-nano` | 开源权重，可自部署 | 同 4 系列 |

Voyage 有两点别家没有：一是领域专用模型，二是 4 系列的向量互通。你可以用大模型建索引、用 lite 模型做在线查询，不用重建索引。

---

## 6. Cohere、Voyage、OpenAI 怎么选

```
英文为主，质量敏感：
  → voyage-4-large / embed-v5.0-pro
  → 或 text-embedding-3-large

英文，预算敏感：
  → voyage-4-lite / text-embedding-3-small
  → 价格以各家定价页为准

多语言 / 跨语言：
  → embed-v5.0 / voyage-4 / 千问 text-embedding-v4 / BGE-M3
  → 自己的多语言 evalset 上跑一遍再定

代码：
  → voyage-code-4（专门优化）

领域专（金融 / 法律 / 医疗）：
  → voyage-finance-2 / law-2 等专用版

中文为主：
  → 千问 text-embedding-v4（API）或自部署 Qwen3-Embedding / BGE-M3（详见 03-open-source）

数据合规出不去：
  → 必须自部署（详见 03-open-source）
```

---

## 7. 三家放在一起对比

Cohere 和 Voyage 的部分需要各自的 API key，OpenAI 兼容的部分用千问 v4 的 key 就能跑。

```python
import os
import numpy as np
from openai import OpenAI
import cohere
import voyageai


qwen = OpenAI(api_key=os.environ["DASHSCOPE_API_KEY"],
              base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
co = cohere.ClientV2()
vo = voyageai.Client()


def embed_qwen(texts):
    resp = qwen.embeddings.create(model="text-embedding-v4", input=texts, dimensions=1024)
    return np.array([d.embedding for d in resp.data])


def embed_cohere(texts, is_query=False):
    input_type = "search_query" if is_query else "search_document"
    resp = co.embed(texts=texts, model="embed-v5.0-pro", input_type=input_type, embedding_types=["float"])
    return np.array(resp.embeddings.float_)


def embed_voyage(texts, is_query=False):
    input_type = "query" if is_query else "document"
    resp = vo.embed(texts=texts, model="voyage-4", input_type=input_type)
    return np.array(resp.embeddings)


query = "如何取消订阅"
docs = [
    "如何关闭自动续费",
    "如何登录账号",
    "停止订阅的方法",
    "重置密码教程",
]


for name, fn_q, fn_d in [
    ("Qwen", lambda q: embed_qwen([q])[0], embed_qwen),
    ("Cohere", lambda q: embed_cohere([q], is_query=True)[0], embed_cohere),
    ("Voyage", lambda q: embed_voyage([q], is_query=True)[0], embed_voyage),
]:
    qv = fn_q(query)
    dvs = fn_d(docs)
    sims = dvs @ qv / (np.linalg.norm(dvs, axis=1) * np.linalg.norm(qv))
    top = np.argsort(-sims)
    print(f"\n[{name}]")
    for i in top:
        print(f"  {docs[i]:<25} sim={sims[i]:.4f}")
```

几家都应该把"停止订阅的方法"和"如何关闭自动续费"排在前两位。三家的相似度数值分布不同，只比较各自的排序，不要跨家比绝对值。

---

## 8. 限流 & 重试

```python
import time
import cohere
from cohere.errors import TooManyRequestsError


co = cohere.ClientV2()


def embed_with_retry(texts, max_retries=5):
    for attempt in range(max_retries):
        try:
            return co.embed(texts=texts, model="embed-v5.0-pro", input_type="search_document",
                            embedding_types=["float"]).embeddings.float_
        except TooManyRequestsError:
            wait = 2 ** attempt
            print(f"Rate limited, sleeping {wait}s")
            time.sleep(wait)
    raise RuntimeError("max retries exceeded")
```

每家按 tier 有不同的 RPM / TPM 限制，具体以你的合同为准。

---

## 9. 跨家迁移注意

```
OpenAI 1536 维 → Cohere 1024 维：
  ❌ 必须重建（dim 不同）

Cohere v3 → v4 → v5 / Voyage 3 → 4（跨代）：
  ❌ 必须重建（向量分布不同；Voyage 4 系列内部互通除外）

切换 input_type（如 document → classification）：
  ⚠️ 同一模型但 output 不同，需重 embed
```

选型时就要把"以后会不会换模型"考虑进去。数据量大的话，重建一次要几个小时，外加几十美元。

---

## 10. 数据合规

- OpenAI：默认不用 API 数据训练模型，但请求数据会保留一段时间；零数据保留（ZDR）要单独申请。数据要传到海外
- Cohere：数据使用和保留以官方条款为准；也可以通过 AWS / Azure / Oracle 等云平台使用
- VoyageAI：可以开 zero-retention，需要额外配置

合规上数据出不了境，就只能自部署（见[开源模型：Qwen3-Embedding / BGE / Jina / Nomic](./03-open-source.md)）。

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| Cohere 不指定 `input_type` | v3 及以上模型直接报错；查询和文档两侧都要按用途传对 |
| Voyage 用 query 类型 embed 文档 | 两侧错位，召回变差 |
| 切模型后没重建索引 | 召回会崩 |
| 多语言场景用单语模型 | 跨语言效果差 |

---

## 12. 下一步

- 自部署开源模型：[开源模型：Qwen3-Embedding / BGE / Jina / Nomic](./03-open-source.md)
- 本地跑模型的工具：[sentence-transformers 工具链](./04-sentence-transformers.md)
- 完整的选型流程：[MTEB 怎么看 + 选型决策树](./06-mteb-selection.md)
