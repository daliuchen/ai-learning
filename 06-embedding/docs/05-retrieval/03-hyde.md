# HyDE：用 LLM 生成假设答案

> HyDE（Hypothetical Document Embeddings）的思路是先让 LLM 针对用户问题写一段"假装的答案"，再拿这段假答案去做 embedding 检索。假答案在措辞和长度上更像知识库里的真实文档，所以召回率经常比直接用 query 检索要高。代价是每次检索多一次 LLM 调用，这篇也会讲怎么控制这部分成本。

---

## 1. 为什么需要 HyDE

```
传统：
  query: "如何取消订阅" (10 字)
  embed(query) → 跟文档 embedding 比

问题：query 短且抽象，跟文档（实际答案）语义距离仍有 gap

HyDE：
  step 1: LLM 假装答："要取消订阅，请登录账户后进入设置，点击订阅..."
  step 2: embed(假答案)
  step 3: 用这个 embedding 检索
  
直觉：假答案 ≈ 真文档（文风、词汇都更像），相似度更高
```

---

## 2. 基本实现

```python
from openai import OpenAI


client = OpenAI()


def hyde_search(query: str, top_k: int = 5):
    # Step 1: LLM 生成假设答案
    hypothesis = client.chat.completions.create(
        model="gpt-6-luna",
        messages=[
            {"role": "system", "content": "你是一个助手。请按知识库文档的风格回答用户问题。直接给答案，不要寒暄。"},
            {"role": "user", "content": query},
        ],
        max_completion_tokens=1000,   # 新模型用这个参数名（max_tokens 已弃用）；推理 token 也计入，别设太小
    ).choices[0].message.content
    
    # Step 2: 用假答案 embed
    hyde_vec = client.embeddings.create(
        model="text-embedding-3-small",
        input=[hypothesis],
    ).data[0].embedding
    
    # Step 3: 检索
    return vector_db.search(hyde_vec, top_k=top_k)


results = hyde_search("怎么取消订阅")
```

---

## 3. 改进：合并 query + hypothesis embedding

```python
def hyde_combined_search(query, top_k=5):
    hypothesis = generate_hypothesis(query)
    
    # 两个 embedding 平均
    q_vec = embed(query)
    h_vec = embed(hypothesis)
    
    import numpy as np
    combined = (np.array(q_vec) + np.array(h_vec)) / 2
    combined /= np.linalg.norm(combined)
    
    return vector_db.search(combined, top_k=top_k)
```

或者两路各自召回，再用 RRF 融合：

```python
def hyde_rrf_search(query, top_k=5):
    hypothesis = generate_hypothesis(query)
    
    q_hits = vector_db.search(embed(query), top_k=20)
    h_hits = vector_db.search(embed(hypothesis), top_k=20)
    
    return rrf([
        [h.id for h in q_hits],
        [h.id for h in h_hits],
    ], top_k=top_k)
```

---

## 4. 生成多个假设（增强召回）

```python
def multi_hypothesis_search(query, n_hypotheses=3, top_k=5):
    # 生成 N 个不同的假答案
    hypotheses = client.chat.completions.create(
        model="gpt-6-luna",
        messages=[
            {"role": "system", "content": "为下面问题生成 3 个不同风格的可能答案（FAQ 风、文档风、教程风），每个一段。用 --- 分隔。"},
            {"role": "user", "content": query},
        ],
    ).choices[0].message.content.split("---")
    
    hypotheses = [h.strip() for h in hypotheses if h.strip()]
    
    # 多路召回 + RRF
    rankings = []
    for h in hypotheses:
        hits = vector_db.search(embed(h), top_k=20)
        rankings.append([hit.id for hit in hits])
    
    # 也加上原 query
    rankings.append([h.id for h in vector_db.search(embed(query), top_k=20)])
    
    return rrf(rankings, top_k=top_k)
```

---

## 5. 何时 HyDE 有效

适合用 HyDE 的情况：

- Query 短而抽象（"取消订阅" / "如何认证"）
- 文档长且具体（步骤说明 / 教程）
- 用户的说法和文档差别大（口语 vs 正式）

效果不明显的情况：

- Query 本身已经写得很具体
- 专业领域里 LLM 容易编造
- 文档是结构化数据或代码

---

## 6. 性能成本

```
传统：
  1 次 embed(query)
  延迟：~150ms
  成本：10 个 token 的 query，按 3-small 的 $0.02 / 百万 token 不到 $0.000001

HyDE：
  1 次 LLM 生成（约 200 输出 token）
  1 次 embed(hypothesis)
  延迟：~1500ms
  成本：主要是 LLM 这一次调用，按所用模型的输入/输出单价算
```

（延迟是量级示意。）检索本身的 embed 费用几乎可以忽略，HyDE 的成本基本全在多出来的那次 LLM 调用上，延迟也高一个数量级。如果召回率能提升 5-10%，对高价值的 query 还是划算的。

---

## 7. 优化：缓存 HyDE 结果

```python
import hashlib
import json
import redis


r = redis.Redis()


def cached_hyde(query: str, ttl=3600):
    key = "hyde:" + hashlib.md5(query.encode()).hexdigest()
    cached = r.get(key)
    if cached:
        return json.loads(cached)
    
    hypothesis = generate_hypothesis(query)
    r.setex(key, ttl, json.dumps({"hypothesis": hypothesis}))
    return {"hypothesis": hypothesis}
```

热门 query 命中缓存后就不再产生 LLM 费用。

---

## 8. 跟 query rewrite 的区别

| | HyDE | Query Rewrite |
|---|---|---|
| 输出 | 假设的答案 | 改写后的 query |
| 长度 | 较长（200+ tokens） | 短（和原 query 差不多） |
| 用来 embed 的 | 假答案 | 改写后的 query |
| 适合 | 答案文档写得具体 | query 模糊、不规范 |

例：

```
Query: "怎么搞那个续费"

Rewrite: "如何取消自动续费"

HyDE: "要取消自动续费，请登录账户后..."
```

实际项目里两者经常一起用。

---

## 9. 完整 demo

```python
import numpy as np
from openai import OpenAI


client = OpenAI()


corpus = [
    "如何取消订阅：登录账户 → 设置 → 账户 → 订阅 → 取消订阅按钮。系统会要求确认，确认后订阅将在当前周期结束后停止。",
    "退款政策：年付订阅 7 天内可全额退款，超过则按比例。月付不支持退款。请联系客服 support@example.com。",
    "如何登录：访问 example.com 点击右上角'登录'，输入邮箱和密码。",
    "重置密码：登录页点击'忘记密码'，输入注册邮箱，系统发送重置链接。",
    "支付方式：支持信用卡（Visa / Master）、PayPal、Apple Pay、Google Pay。",
]


corpus_vecs = np.array([
    d.embedding for d in client.embeddings.create(
        model="text-embedding-3-small", input=corpus
    ).data
])


def embed(text):
    return np.array(client.embeddings.create(model="text-embedding-3-small", input=[text]).data[0].embedding)


def search_basic(query, top_k=3):
    q_vec = embed(query)
    sims = corpus_vecs @ q_vec
    return [(i, corpus[i], float(sims[i])) for i in np.argsort(-sims)[:top_k]]


def search_hyde(query, top_k=3):
    hypothesis = client.chat.completions.create(
        model="gpt-6-luna",
        messages=[
            {"role": "system", "content": "请按 FAQ 文档风格回答。直接答案，不要寒暄。100 字内。"},
            {"role": "user", "content": query},
        ],
        max_completion_tokens=1000,   # 新模型用这个参数名（max_tokens 已弃用）；推理 token 也计入，别设太小
    ).choices[0].message.content
    
    print(f"  HyDE 生成的假答案: {hypothesis}")
    
    h_vec = embed(hypothesis)
    sims = corpus_vecs @ h_vec
    return [(i, corpus[i], float(sims[i])) for i in np.argsort(-sims)[:top_k]]


query = "怎么把自动续费关了"


print(f"\n=== Basic ===")
for i, doc, s in search_basic(query):
    print(f"  {s:.4f}  {doc[:50]}")

print(f"\n=== HyDE ===")
for i, doc, s in search_hyde(query):
    print(f"  {s:.4f}  {doc[:50]}")
```

---

## 10. 跟 LangChain / LlamaIndex 集成

### LangChain

```python
# langchain 1.x 已搬到 langchain-classic
from langchain_classic.chains.hyde.base import HypotheticalDocumentEmbedder
from langchain_openai import OpenAIEmbeddings, ChatOpenAI


hyde_embedder = HypotheticalDocumentEmbedder.from_llm(
    llm=ChatOpenAI(model="gpt-6-luna"),
    base_embeddings=OpenAIEmbeddings(model="text-embedding-3-small"),
    prompt_key="web_search",   # 内置模板，另有 "sci_fact" / "fiqa" / "trec_covid" 等
)


result_vec = hyde_embedder.embed_query("怎么取消订阅")
```

### LlamaIndex

```python
from llama_index.core.indices.query.query_transform.base import HyDEQueryTransform
from llama_index.core.query_engine import TransformQueryEngine


hyde = HyDEQueryTransform(include_original=True)
hyde_query_engine = TransformQueryEngine(base_query_engine, hyde)
response = hyde_query_engine.query("怎么取消订阅")
```

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| LLM 的假答案瞎编 | system prompt 里加"如果不知道，就给通用描述" |
| 假答案太长（>500 tokens） | prompt 里要求简短（如"100 字内"），再配合 max_completion_tokens 兜底 |
| 每个 query 都跑 HyDE | 加缓存，只对短而抽象的 query 开 HyDE |
| LLM 费用暴涨 | 用 gpt-6-luna 或自部署 LLM |

---

## 12. 下一步

- [Multi-query / Sub-query 拆解](./04-multi-query.md)
- [重排 Pipeline：召回 → 精排](./05-rerank-pipeline.md)
- [Self-Query / Metadata Filter](./06-self-query.md)
