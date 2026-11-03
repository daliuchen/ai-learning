# 用 Embedding 做去重、聚类与异常检测

> embedding 的用处不止搜索。同样一套相似度计算，可以拿来去重（相似度超过阈值就合并）、聚类（K-means / HDBSCAN），也可以做异常检测（离正常样本远的就是异常）。这一篇把这三种用法各写一遍。

---

## 1. 去重：找几乎一样的文档

### 1.1 严格去重（md5）

md5 只能找出一字不差的重复：

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

改了个标点、有错别字、做了同义改写，它就认不出来了。

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

`threshold` 的取值大致是：0.95 比较严，只有错别字级别的差异才算重复；0.90 偏宽松；到 0.85 就可能把不同的内容误合并。

---

## 2. 大规模去重（百万级）

两两算 cosine 是 O(N²)，数据一多就太慢了，要用 LSH 或 FAISS：

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
    vecs = np.array([embed(d["content"]) for d in docs])
    
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

适合文章主题归类、用户分群、FAQ 整理这类任务。

---

## 4. 聚类：HDBSCAN（自动定 K）

事先不知道该分几类的话，用 HDBSCAN，它会自己决定簇的数量：

```python
# pip install hdbscan（scikit-learn 1.3 起也自带 sklearn.cluster.HDBSCAN）
import hdbscan


def auto_cluster(docs, min_cluster_size=5):
    # docs 这里是字符串列表（第 7 节直接传用户 query）；是 dict 的话先取出文本
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

聚出来的簇可以让 LLM 起个名字：

```python
def name_cluster(docs_in_cluster):
    sample = docs_in_cluster[:5]
    # 文档是 dict 时取标题，直接是字符串（比如用户 query）时原样用
    sample_text = "\n".join(d["title"] if isinstance(d, dict) else d for d in sample)
    
    resp = client.chat.completions.create(
        model="gpt-6-luna",
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

思路是离正常样本远的点就算异常，有两种衡量方式。

### 6.1 基于中心向量

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

### 6.2 基于局部密度

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

新内容入库前，先检查它和已有内容是否重复：

```python
async def add_with_dedup_check(new_doc, threshold=0.92):
    new_vec = embed(new_doc["content"])
    
    hits = vector_db.query_points("docs", query=new_vec.tolist(), limit=1).points
    
    if hits and hits[0].score > threshold:
        return {"status": "duplicate", "similar_to": hits[0].payload}
    
    # 不重复，正常入库
    await vector_db.upsert(...)
    return {"status": "added"}
```

---

## 9. 完整 demo

```python
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
| 内容审核 | 和已知违规样本做相似度比对 |
| 知识库整理 | 聚类后人工 review |
| Trending topic | 时间窗口 + 聚类增量 |

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| K-means 前没有归一化 | 先 normalize embeddings，按 cosine 距离聚 |
| 所有数据用同一个阈值 | 按 cluster 设自适应阈值 |
| 异常检测报出来的太多 | 提高阈值，或者叠加业务规则 |
| 聚类结果看不出意义 | 换个 K，或者改用 HDBSCAN |

---

## 12. 章节小结：08-applications

应用这一章的五篇，分别用 embedding 搭了一个完整的 RAG 知识库问答、一个电商语义搜索、一套图搜图和文搜图、一个推荐系统，以及这一篇的去重、聚类和异常检测。

---

## 13. 全书回顾

全书 44 篇，各章篇数如下：

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
| 合计 | 44 |

做自己的项目时，按遇到的问题回到对应章节：选模型看 02，切片和检索质量看 04、05，评测看 06，部署和增量更新看 07。

---

## 14. 跟其它手册的关联

- [LangChain 全家桶系列教程](../../../01-langchain/README.md)的 RAG 章节：那本讲 SDK 怎么用，这本补底层组件
- [Prompt Engineering 实战手册](../../../04-prompt-engineering/README.md)：评测的方法论是相通的
- [OpenAI Agents SDK 实战手册](../../../05-openai-agents-sdk/README.md)的 FileSearchTool：可以对照着看托管 RAG 和自己搭的取舍
- [MCP 学习手册](../../../03-mcp/README.md)：检索能力可以包成 MCP Server，给各种 Agent 共用
