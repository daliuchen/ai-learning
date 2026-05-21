# 去重 / 聚类 / 异常检测

> **一句话**：embedding 不止能做"搜索"——同样的相似度计算可以**去重**（相似度 > 阈值合并）、**聚类**（K-means / HDBSCAN）、**异常检测**（跟正常样本距离远的）。

---

## 1. 去重：找几乎一样的文档

### 1.1 严格去重（md5）

只能找一字不差的：

```python
import hashlib
seen = set()
unique = []
for d in docs:
    h = hashlib.md5(d["content"].encode()).hexdigest()
    if h not in seen:
        seen.add(h)
        unique.append(d)
```

不能处理"改了个标点 / typo / 同义改写"。

### 1.2 Embedding 去重（semantic）

```python
import numpy as np


def dedupe_by_embedding(docs, threshold=0.95):
    """相似度 > threshold 的当重复"""
    vecs = np.array([embed(d["content"]) for d in docs])
    
    unique = []
    unique_vecs = []
    
    for i, doc in enumerate(docs):
        v = vecs[i]
        if not unique_vecs:
            unique.append(doc)
            unique_vecs.append(v)
            continue
        
        # 跟已保留的算最大相似度
        sims = np.array(unique_vecs) @ v
        if sims.max() < threshold:
            unique.append(doc)
            unique_vecs.append(v)
    
    return unique


unique_docs = dedupe_by_embedding(my_docs, threshold=0.95)
print(f"去重前 {len(my_docs)} → 去重后 {len(unique_docs)}")
```

`threshold` 调参：

- 0.95：严苛（typo 也算同）
- 0.90：宽松
- 0.85：可能误合并

---

## 2. 大规模去重（百万级）

每对算 cosine 是 O(N²) → 慢。用 LSH / FAISS：

```python
import faiss


def dedupe_faiss(vecs, threshold=0.95):
    """用 FAISS 找近邻"""
    d = vecs.shape[1]
    index = faiss.IndexFlatIP(d)   # 假设已归一化
    index.add(vecs)
    
    # 每个 vec 找 top-2 邻居
    D, I = index.search(vecs, 2)
    
    keep = [True] * len(vecs)
    for i in range(len(vecs)):
        neighbor_idx = I[i, 1]
        neighbor_sim = D[i, 1]
        if neighbor_sim > threshold and i > neighbor_idx:
            keep[i] = False  # 保留靠前的
    
    return np.where(keep)[0]


keep_idx = dedupe_faiss(my_vecs, threshold=0.95)
unique_docs = [docs[i] for i in keep_idx]
```

---

## 3. 聚类：K-means

把 embedding 分成 K 组：

```python
from sklearn.cluster import KMeans


def cluster_docs(docs, k=10):
    vecs = np.array([embed(d) for d in docs])
    
    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = kmeans.fit_predict(vecs)
    
    clusters = {i: [] for i in range(k)}
    for doc, label in zip(docs, labels):
        clusters[label].append(doc)
    
    return clusters


clusters = cluster_docs(my_articles, k=5)


for i, items in clusters.items():
    print(f"\n=== Cluster {i} ({len(items)} items) ===")
    for item in items[:3]:
        print(f"  {item['title']}")
```

适合：

- 文章主题归类
- 用户分群
- FAQ 整理

---

## 4. 聚类：HDBSCAN（自动定 K）

不知道 K 多少？用 HDBSCAN：

```python
# pip install hdbscan
import hdbscan


def auto_cluster(docs, min_cluster_size=5):
    vecs = np.array([embed(d) for d in docs])
    
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",  # 归一化向量后 L2 = √(2 - 2cos)
    )
    labels = clusterer.fit_predict(vecs)
    
    # label = -1 表示噪声（异常）
    clusters = {}
    noise = []
    for doc, label in zip(docs, labels):
        if label == -1:
            noise.append(doc)
        else:
            clusters.setdefault(label, []).append(doc)
    
    return clusters, noise


clusters, noise = auto_cluster(my_docs)
print(f"自动聚出 {len(clusters)} 类，{len(noise)} 个异常")
```

---

## 5. 给每个 cluster 自动取名

让 LLM 给聚类起标签：

```python
def name_cluster(docs_in_cluster):
    sample = docs_in_cluster[:5]
    sample_text = "\n".join(d["title"] for d in sample)
    
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "为这组文档起一个 5-10 字的标签。只输出标签。"},
            {"role": "user", "content": sample_text},
        ],
    )
    return resp.choices[0].message.content.strip()


for cluster_id, docs in clusters.items():
    name = name_cluster(docs)
    print(f"Cluster {cluster_id}: {name} ({len(docs)} docs)")
```

---

## 6. 异常检测

跟"正常"距离远的就是异常：

### 6.1 基于"中心向量"

```python
def detect_anomaly(items, threshold=0.3):
    """跟集合中心距离远的"""
    vecs = np.array([embed(i) for i in items])
    
    # 中心
    center = vecs.mean(axis=0)
    center /= np.linalg.norm(center)
    
    # 每个 item 跟中心的相似度
    sims = vecs @ center
    
    anomalies = []
    for item, sim in zip(items, sims):
        if sim < threshold:
            anomalies.append({"item": item, "sim_to_center": sim})
    
    return anomalies


# 用例：找跟主流不一致的客服工单
weird_tickets = detect_anomaly(today_tickets, threshold=0.4)
```

### 6.2 基于"局部密度"

```python
from sklearn.neighbors import LocalOutlierFactor


def lof_anomaly(items, n_neighbors=20):
    vecs = np.array([embed(i) for i in items])
    
    lof = LocalOutlierFactor(n_neighbors=n_neighbors, novelty=False)
    is_outlier = lof.fit_predict(vecs)  # -1 = 异常
    
    return [item for item, flag in zip(items, is_outlier) if flag == -1]
```

---

## 7. 实战：FAQ 自动整理

```python
# 1. 拿过去一个月的所有用户问题
queries = db.query("SELECT query FROM logs WHERE created_at > NOW() - INTERVAL '30 days'")


# 2. embed + 聚类
clusters, noise = auto_cluster(queries, min_cluster_size=10)


# 3. 给每个 cluster 起名 + 找典型样本
faq_candidates = []
for cluster_id, items in clusters.items():
    name = name_cluster(items)
    
    # 找最靠近 cluster 中心的 query
    vecs = np.array([embed(q) for q in items])
    center = vecs.mean(axis=0)
    sims = vecs @ center
    representative = items[sims.argmax()]
    
    faq_candidates.append({
        "topic": name,
        "count": len(items),
        "representative_query": representative,
        "all_queries": items,
    })


# 按频次排
faq_candidates.sort(key=lambda x: -x["count"])
```

---

## 8. 重复检测：实时

新内容入库时检查是否跟已有重复：

```python
async def add_with_dedup_check(new_doc, threshold=0.92):
    new_vec = embed(new_doc["content"])
    
    hits = vector_db.search("docs", query_vector=new_vec.tolist(), limit=1)
    
    if hits and hits[0].score > threshold:
        return {"status": "duplicate", "similar_to": hits[0].payload}
    
    # 不重复，正常入库
    await vector_db.upsert(...)
    return {"status": "added"}
```

---

## 9. 完整 demo

```python
# demos/applications/05_dedup_cluster.py
import numpy as np
from sklearn.cluster import KMeans
from openai import OpenAI


client = OpenAI()


queries = [
    "如何取消订阅",
    "怎么取消订阅",
    "停止订阅的方法",
    "怎样停止扣费",
    "怎么登录",
    "登录失败怎么办",
    "登录不进去",
    "改密码",
    "重置密码",
    "如何修改密码",
    "支付方式有哪些",
    "支持哪些付款方式",
    "今天天气怎么样",   # 异常（跟产品无关）
]


vecs = np.array([
    d.embedding for d in client.embeddings.create(model="text-embedding-3-small", input=queries).data
])


# 聚类
kmeans = KMeans(n_clusters=4, random_state=42, n_init=10)
labels = kmeans.fit_predict(vecs)


# 输出
for cluster_id in range(4):
    cluster_queries = [queries[i] for i in range(len(queries)) if labels[i] == cluster_id]
    print(f"\n=== Cluster {cluster_id} ({len(cluster_queries)} items) ===")
    for q in cluster_queries:
        print(f"  {q}")


# 异常检测：跟整体中心距离远的
center = vecs.mean(axis=0)
center /= np.linalg.norm(center)
sims_to_center = vecs @ center


print("\n=== 跟中心距离远的（可能异常）===")
for q, sim in sorted(zip(queries, sims_to_center), key=lambda x: x[1])[:3]:
    print(f"  sim={sim:.4f}  {q}")
```

---

## 10. 应用场景汇总

| 场景 | 方法 |
|------|------|
| 内容去重（爬虫 / UGC）| Embedding + threshold（0.95）|
| FAQ 整理 | HDBSCAN 聚类 |
| 用户分群 | K-means user_embedding |
| 异常 query 检测 | LOF / 中心距离 |
| 内容审核 | 跟违规样本相似 |
| 知识库整理 | 聚类后人工 review |
| Trending topic | 时间窗口 + 聚类增量 |

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| K-means 不归一化 | normalize embeddings → 用 cosine 距离 |
| threshold 一刀切 | 不同 cluster 自适应阈值 |
| 异常检测召太多 | 提高阈值 / 加业务规则 |
| 聚类结果没意义 | 试别的 K / HDBSCAN |

---

## 12. 章节小结：08-applications

走完这 5 篇你会用 embedding 做：

- ✅ 完整 RAG 知识库问答
- ✅ 电商语义搜索
- ✅ 多模态：图搜图 / 文搜图
- ✅ 推荐系统
- ✅ 去重 / 聚类 / 异常检测

---

## 13. 全本手册完结

44 篇覆盖：

| 章 | 篇数 |
|---|------|
| 01-foundations（原理） | 6 |
| 02-models（模型） | 6 |
| 03-vector-db（向量库） | 7 |
| 04-chunking（切片） | 5 |
| 05-retrieval（检索） | 6 |
| 06-evaluation（评测） | 4 |
| 07-production（生产） | 5 |
| 08-applications（应用） | 5 |
| **合计** | **44** |

走完你能：

- 看懂 embedding 怎么工作 / 选什么模型 / 截哪个维度
- 选合适向量库 + 配 metadata filter
- 设计 chunking 策略（含 small-to-big）
- 实现混合检索 + rerank + HyDE
- 用 evalset 做端到端评测
- 部署 TEI / 监控 / 增量更新
- 搭 RAG / 语义搜索 / 多模态 / 推荐 / 去重

---

## 14. 跟其它手册的关联

- **01-langchain RAG 章节**：本手册补底层，那本讲 SDK
- **04-prompt-engineering**：评测方法论
- **05-openai-agents-sdk FileSearchTool**：托管 vs 自搭 RAG
- **03-mcp**：检索能力可包成 MCP Server

去做点真事吧。
