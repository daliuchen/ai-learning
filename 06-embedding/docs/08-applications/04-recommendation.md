# 推荐系统 with Embedding

> **一句话**：把"用户兴趣"和"物品特征"分别 embed 到同一空间，**用户向量 × 物品向量** 算相似度做候选召回——传统推荐系统的现代化方式。

---

## 1. 推荐系统结构

```
[User behavior history]
   ↓
[User Embedding]  ← 用户兴趣向量
   ↓
[ANN 召回] ← 在物品库找最近 top-N
   ↓
[Ranking / Rerank] ← 业务策略加权
   ↓
[Top 推荐列表]
```

---

## 2. User Embedding 怎么来

### 方案 A：行为聚合

把用户最近交互的物品 embedding 平均：

```python
def user_embedding(user_id):
    # 用户最近 50 个点击/购买的物品
    recent_items = db.query("SELECT item_id FROM events WHERE user_id=%s ORDER BY ts DESC LIMIT 50", user_id)
    
    item_vecs = [item_embeddings[item_id] for item_id in recent_items]
    
    # 简单平均
    import numpy as np
    user_vec = np.mean(item_vecs, axis=0)
    user_vec /= np.linalg.norm(user_vec)
    return user_vec
```

### 方案 B：加权聚合

近期权重高、买的比看的权重高：

```python
def weighted_user_embedding(user_id):
    events = db.query("""
        SELECT item_id, action, ts FROM events
        WHERE user_id=%s ORDER BY ts DESC LIMIT 100
    """, user_id)
    
    now = time.time()
    vecs = []
    weights = []
    for e in events:
        item_vec = item_embeddings[e["item_id"]]
        
        # 时间衰减
        age_days = (now - e["ts"]) / 86400
        time_weight = math.exp(-age_days / 30)   # 半衰期 30 天
        
        # 行为权重
        action_weight = {"view": 1, "click": 2, "cart": 5, "purchase": 10}.get(e["action"], 1)
        
        vecs.append(item_vec)
        weights.append(time_weight * action_weight)
    
    weights = np.array(weights) / sum(weights)
    user_vec = np.sum([v * w for v, w in zip(vecs, weights)], axis=0)
    user_vec /= np.linalg.norm(user_vec)
    return user_vec
```

### 方案 C：用专门的双塔模型

```
用户特征 (history) → User Tower → user_vec
物品特征 (title/cat) → Item Tower → item_vec

训练目标：(user, clicked_item) 相似度高
```

工业级用 Two-Tower / DSSM 等专门架构。简单场景方案 B 够用。

---

## 3. Item Embedding

跟语义搜索一样：

```python
def item_to_text(item):
    return f"{item['title']} | {item['category']} | {item['tags']} | {item['description']}"


item_embeddings = {
    item["id"]: embed(item_to_text(item))
    for item in all_items
}
```

或用 CLIP 给电商商品图 embed（详见 [03-multimodal.md](./03-multimodal.md)）。

---

## 4. 召回

```python
def recommend(user_id, top_k=20):
    user_vec = user_embedding(user_id)
    
    hits = vector_db.search(
        collection_name="items",
        query_vector=user_vec.tolist(),
        limit=top_k * 2,
    )
    
    # 过滤已看过 / 已购买
    seen = set(get_user_seen_items(user_id))
    fresh = [h for h in hits if h.id not in seen]
    
    return fresh[:top_k]
```

---

## 5. 多样性

直接 cosine 召回的物品**太相似**——用户体验差：

```python
def diverse_recommend(user_id, top_k=20, diversity=0.5):
    """MMR (Maximal Marginal Relevance)"""
    user_vec = user_embedding(user_id)
    candidates = vector_db.search("items", query_vector=user_vec, limit=100)
    
    selected = []
    selected_vecs = []
    
    while len(selected) < top_k and candidates:
        if not selected:
            # 第一个：相似度最高
            best = max(candidates, key=lambda c: c.score)
        else:
            # 后续：相似度高 + 跟已选不太像
            def mmr_score(c):
                sim_to_user = c.score
                max_sim_to_selected = max([
                    float(np.array(c.vector) @ s) for s in selected_vecs
                ])
                return (1 - diversity) * sim_to_user - diversity * max_sim_to_selected
            
            best = max(candidates, key=mmr_score)
        
        selected.append(best)
        selected_vecs.append(np.array(best.vector))
        candidates.remove(best)
    
    return selected
```

`diversity=0` 全是最相似，`diversity=1` 拼命要多样。常用 0.3-0.5。

---

## 6. 业务策略加权

```python
def business_rerank(user_id, candidates):
    user = get_user(user_id)
    
    scored = []
    for c in candidates:
        item = c.payload
        score = c.score
        
        # 加权
        if item["category"] in user.prefer_categories:
            score *= 1.2
        if item["stock"] == 0:
            score *= 0.1
        if item.get("promotional", False):
            score *= 1.1
        if item["price"] > user.budget_max:
            score *= 0.5
        
        scored.append((c, score))
    
    return sorted(scored, key=lambda x: -x[1])
```

---

## 7. 冷启动

新用户 / 新物品没数据：

```python
def recommend_cold_user(user_id):
    """新用户没行为"""
    user = get_user(user_id)
    
    if user.signup_categories:
        # 注册时让用户选了感兴趣的
        seed_vec = np.mean([category_embeddings[c] for c in user.signup_categories], axis=0)
    elif user.demographic:
        # 按人口学找相似用户的"平均偏好"
        seed_vec = average_embedding_of_similar_users(user.demographic)
    else:
        # 完全冷 → 热门
        return get_trending_items()
    
    return vector_db.search("items", query_vector=seed_vec.tolist())


def index_cold_item(item_id):
    """新物品没交互数据"""
    item = get_item(item_id)
    vec = embed(item_to_text(item))
    vector_db.upsert("items", points=[PointStruct(id=item_id, vector=vec.tolist(), payload=item)])
    # 即可被召回
```

---

## 8. 实时更新

用户每次点击 → 更新 user_embedding：

```python
async def on_user_click(user_id, item_id):
    # 写事件
    await db.insert("events", {"user_id": user_id, "item_id": item_id, "action": "click", "ts": now()})
    
    # 异步更新 user_embedding
    user_vec = weighted_user_embedding(user_id)
    await cache.set(f"user_vec:{user_id}", user_vec, ttl=3600)
```

---

## 9. 完整 demo

```python
# demos/applications/04_recommendation.py
import numpy as np
from openai import OpenAI


client = OpenAI()


ITEMS = [
    {"id": 1, "title": "Python 异步编程指南", "tags": ["python", "async", "tech"]},
    {"id": 2, "title": "JavaScript ES6 入门", "tags": ["js", "es6", "tech"]},
    {"id": 3, "title": "深度学习实战", "tags": ["ai", "ml", "tech"]},
    {"id": 4, "title": "野外摄影技巧", "tags": ["photo", "travel"]},
    {"id": 5, "title": "意大利美食游", "tags": ["food", "travel"]},
    {"id": 6, "title": "Rust 编程语言介绍", "tags": ["rust", "tech"]},
    {"id": 7, "title": "Vue.js 进阶", "tags": ["js", "vue", "tech"]},
    {"id": 8, "title": "瑜伽入门", "tags": ["health", "fitness"]},
]


# Item embeddings
texts = [f"{it['title']} | {' '.join(it['tags'])}" for it in ITEMS]
item_vecs = np.array([
    d.embedding for d in client.embeddings.create(model="text-embedding-3-small", input=texts).data
])
item_lookup = {it["id"]: i for i, it in enumerate(ITEMS)}


# 用户行为
user_history = {
    "alice": [1, 3, 6],      # Python / 深度学习 / Rust → 偏技术
    "bob": [4, 5, 8],         # 摄影 / 美食 / 瑜伽 → 偏生活
}


def user_embedding(user_id):
    history = user_history[user_id]
    vecs = [item_vecs[item_lookup[hid]] for hid in history]
    u = np.mean(vecs, axis=0)
    return u / np.linalg.norm(u)


def recommend(user_id, top_k=3):
    uv = user_embedding(user_id)
    sims = item_vecs @ uv
    seen = set(user_history[user_id])
    
    candidates = [(i, sims[i]) for i in range(len(ITEMS)) if ITEMS[i]["id"] not in seen]
    return sorted(candidates, key=lambda x: -x[1])[:top_k]


for user in ["alice", "bob"]:
    print(f"\n=== Recommend for {user} ===")
    for i, score in recommend(user):
        print(f"  {score:.4f}  {ITEMS[i]['title']}")
```

---

## 10. 跟传统协同过滤对比

| | 协同过滤 (CF) | Embedding-based |
|---|---|---|
| 数据 | User-item 交互矩阵 | User / item 内容 + 交互 |
| 冷启动 | 难（新用户 / 新物品没数据） | 容易（内容 embed 即可） |
| 解释性 | 弱 | 强（同义物品聚一起） |
| 内容相似 | ❌ | ✅ |
| 计算复杂度 | 高（矩阵分解）| 中（embed 一次，查询 ANN）|

实战常**两者结合**：CF 抓"行为相似"，Embedding 抓"内容相似"。

---

## 11. 常见坑

| 坑 | 解 |
|----|----|
| User vector 偏老兴趣 | 加时间衰减 |
| 推荐都太像 | 加 MMR 多样性 |
| Item embedding 不更新 | 物品改动后要 re-embed |
| 没过滤已看 | seen set 必须做 |

---

## 12. 下一步

- 📖 去重 / 聚类 / 异常检测 → [05-deduplication.md](./05-deduplication.md)
- 📖 多模态推荐（电商图 + 描述）→ [03-multimodal.md](./03-multimodal.md)
