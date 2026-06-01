# CE 04-06：记忆的遗忘与更新

> **一句话**：记忆系统最反直觉的真相——「会遗忘」才好用。只增不减的记忆会膨胀、过期、自相矛盾，召回质量随之崩坏。一个成熟的记忆层必须主动做三件事：清理过期失效的、更新冲突变化的、按重要性 / 访问频率衰减不重要的。遗忘不是缺陷，是给召回降噪、给画像保鲜的必要机制。

---

## 1. 为什么记忆不能只增不减

朴素直觉：记得越多越好。工程现实：恰恰相反。

```
# ❌ 只增不减的记忆库（用一年后）
- "用户喜欢详细解释"        （3 个月前说的）
- "用户说还是简洁点吧"      （上周说的，和上面冲突）
- "用户在调研 A 方案"        （早结束了，失效）
- "用户问今天天气"          （一次性的，根本不该长期记）
- ... 几千条，大半是噪声 ...
```

后果连锁反应：

| 问题 | 表现 |
|------|------|
| 膨胀 | 记忆库越来越大，检索变慢、注入 token 涨 |
| 过期 | 早结束的任务/旧状态还被当成"当前" |
| 冲突 | 新旧偏好并存，模型不知道听谁的 |
| 噪声淹没 | 一次性琐事稀释了真正重要的事实，召回质量下降 |

所以遗忘机制 = 给记忆库**降噪 + 保鲜**，直接决定召回质量。

---

## 2. 三种遗忘 / 更新动作

| 动作 | 触发 | 例子 |
|------|------|------|
| 过期清理（expire） | 记忆带 TTL 或被标记为一次性/已完成 | "今天的会议安排"过了今天就删 |
| 冲突更新（update） | 同一 key 出现新值 | 偏好从 detailed 改成 concise，覆盖 |
| 衰减淘汰（decay） | 重要性低 + 长期没被访问 | 几个月没用到的边角事实，降权或删除 |

下面逐个上代码。

---

## 3. 过期清理

写入时就给记忆定性：哪些是永久事实（过敏），哪些有寿命（当前任务、临时状态）。带 TTL 的记忆到点清理：

```python
from datetime import datetime, timezone, timedelta

def is_expired(item: dict, now: datetime) -> bool:
    # 一次性/临时记忆带 ttl_days；永久事实不带，永不过期
    ttl = item.get("ttl_days")
    if ttl is None:
        return False
    created = datetime.fromisoformat(item["created_at"])
    return now - created > timedelta(days=ttl)

def sweep_expired(items: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [it for it in items if not is_expired(it, now)]

# 写入时定寿命
fact_permanent = {"key": "allergy", "value": "peanut"}                  # 无 ttl，永久
task_temp = {"key": "investigating", "value": "A方案", "ttl_days": 14}  # 两周后清
```

任务类记忆还可以**事件驱动清理**：任务一标记 completed，就把对应记忆降级或删除，而不是干等 TTL。

---

## 4. 冲突更新：用户改了偏好

最常见也最坑——用户改主意了。处理原则：**同 key 覆盖，别并存**。这就是为什么 [03-storage-recall.md](./03-storage-recall.md) 的 schema 里要有 `key` 字段。

```python
def upsert_memory(store: dict[str, dict], new: dict) -> None:
    """按 (user_id, key) 去重覆盖：新值替换旧值，而不是再插一条。"""
    composite = (new["user_id"], new["key"])
    old = store.get(composite)
    if old and old["value"] != new["value"]:
        # 冲突：记一笔历史（可选，便于审计/回滚），然后覆盖
        new["previous_value"] = old["value"]
    new["updated_at"] = datetime.now(timezone.utc).isoformat()
    store[composite] = new

# 用户先说喜欢详细，后说要简洁
upsert_memory(store, {"user_id": "u_42", "key": "verbosity", "value": "detailed"})
upsert_memory(store, {"user_id": "u_42", "key": "verbosity", "value": "concise"})
# store 里只剩 concise，previous_value=detailed
```

向量库场景没有天然的 key 主键，要靠**先检索近似项，相似度超阈值就视为同一事实做更新**，否则才新增——否则同一偏好会存十遍。一些记忆框架（如 Mem0）内置了这种「检索-决策-更新」的合并逻辑。

---

## 5. 重要性衰减与访问频率

不是所有记忆都该平权对待。借鉴「人脑越不用越淡忘」的思路，给记忆打**重要性分**和**访问频率**，综合算一个「记忆强度」，低于阈值就降权召回或淘汰：

```python
import math
from datetime import datetime, timezone

def memory_strength(item: dict, now: datetime) -> float:
    """强度 = 基础重要性 × 时间衰减 × 访问频率加成。"""
    importance = item.get("importance", 0.5)       # 写入时打的分（过敏=1.0，琐事=0.2）
    last = datetime.fromisoformat(item.get("last_accessed") or item["created_at"])
    days_idle = (now - last).days
    recency = math.exp(-days_idle / 30)            # 30 天半衰期式衰减
    freq_boost = min(1.0, 0.1 * item.get("access_count", 0))
    return importance * (0.6 * recency + 0.4) + freq_boost

def prune(items: list[dict], threshold: float = 0.25) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [it for it in items if memory_strength(it, now) >= threshold or it.get("importance", 0) >= 0.9]
    #                                                    ↑ 高重要性(过敏等)豁免淘汰

# 每次召回命中要回写 last_accessed / access_count++，让常用记忆"保鲜"
```

设计要点：

| 维度 | 作用 |
|------|------|
| importance | 过敏/禁忌这类即使久不访问也别淘汰（设高分 + 豁免） |
| recency（时间衰减） | 越久没碰强度越低 |
| access_count（频率） | 常被召回的记忆加成，越用越牢 |
| 豁免线 | 高重要性硬事实永不自动删 |

这正好对应 MemGPT/Letta 的「内存压力」思路（见 [04-agent-memory-arch.md](./04-agent-memory-arch.md)）——窗口/存储紧张时，优先换出低强度记忆。

---

## 6. 为什么「会遗忘」反而更好用

把上面三招连起来看，遗忘带来的全是收益：

| 收益 | 机制 |
|------|------|
| 召回更准 | 噪声/过期记忆被清掉，top-k 命中真正相关的 |
| 画像保鲜 | 冲突更新让画像永远反映"用户现在的样子" |
| 成本可控 | 库不无限膨胀，检索快、注入 token 稳 |
| 更像人 | 记住重要的、淡忘琐碎的——符合用户对"贴心助手"的预期 |
| 隐私友好 | 过期即删，天然契合最小留存 / 被遗忘权 |

一句话：**记忆系统的质量，一半看你记住了什么，另一半看你忘掉了什么。**

---

## 7. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 把一次性琐事当永久事实存 | 噪声膨胀 | 写入时就分级，临时的给 TTL |
| 冲突偏好并存 | 模型听旧的/纠结 | 按 key 覆盖更新 |
| 衰减误删高价值事实 | 忘了用户过敏（危险） | 高重要性豁免淘汰 |
| 召回不回写 access | 常用记忆也被当成不活跃淘汰 | 命中即更新 last_accessed/count |
| "被遗忘权"只删了主库 | 向量库/副本里残留 | 删除要级联到所有副本 |

---

## 8. 下一步

- 📖 整个记忆系统回到分层架构的视角 → [04-agent-memory-arch.md](./04-agent-memory-arch.md)
- 📖 画像的更新与衰减如何配合 → [05-personalization.md](./05-personalization.md)
- 📖 遗忘是压缩的近亲，系统看压缩章 → [05-compaction/01-why-compact.md](../05-compaction/01-why-compact.md)
- 📖 记忆 schema（key/importance/last_accessed）回看 → [03-storage-recall.md](./03-storage-recall.md)

## 参考资料

- Park et al., "Generative Agents"（记忆 importance + recency 检索打分的经典来源）：https://arxiv.org/abs/2304.03442
- Mem0 记忆合并/更新机制：https://github.com/mem0ai/mem0
- Letta 记忆管理文档：https://docs.letta.com/concepts/memory
