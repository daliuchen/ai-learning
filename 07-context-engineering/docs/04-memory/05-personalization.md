# CE 04-05：用户画像与个性化上下文

> **一句话**：个性化的本质是「把『这个用户是谁、偏好什么』持续、精炼地注入每次的上下文窗口」。它由一个随会话演进的 user profile 驱动——构建它、更新它、按预算注入它。同时三条红线不能碰：别注入隐私敏感的东西、别让画像无限膨胀、别把 A 的画像泄漏给 B。

---

## 1. 个性化 = 持续注入「你是谁」

无个性化的助手每次都像第一次见你。个性化助手则在每次窗口里都带上一小段「关于你」的上下文：

```
# ❌ 无画像：每次从零开始
System: 你是一个助手。
User: 推荐个餐厅

# ✅ 注入画像：模型知道你是谁
System: 你是一个助手。
[用户画像] 张三，素食者，住上海，预算敏感，偏好日料，不吃辣。
User: 推荐个餐厅   → 模型直接推上海的平价素食日料
```

注意画像是**短期窗口里的一小块**，但它的来源是**长期记忆**（见 [01-short-vs-long.md](./01-short-vs-long.md)）——每次会话开始，把该用户的画像从存储里取出、压成一段，注入窗口。它跟「按 query 语义召回的记忆片段」（[03-storage-recall.md](./03-storage-recall.md)）互补：画像是**常驻的核心身份**，召回片段是**随话题变化的相关经历**。

---

## 2. user profile 的构建

画像不是一次写死的表单，是从交互里**逐步沉淀 + 主动询问**两条路汇合：

| 来源 | 例子 | 特点 |
|------|------|------|
| 显式声明 | 用户设置里选了"简洁回复" | 可信度高，直接写 |
| 对话推断 | 用户多次问 Rust → 推断偏好 Rust | 需置信度，可能误判 |
| 行为信号 | 总是跳过长解释 | 隐性，需聚合 |

一个实用的 profile schema（结构化 + 可演进）：

```python
from pydantic import BaseModel, Field
from datetime import datetime

class UserProfile(BaseModel):
    user_id: str
    display_name: str | None = None
    locale: str = "zh-CN"
    # 偏好：key -> value，便于增量更新和冲突处理
    preferences: dict[str, str] = Field(default_factory=dict)
    # 约束/事实（过敏、禁忌、硬性要求）
    constraints: list[str] = Field(default_factory=list)
    # 长期目标/正在进行的事
    goals: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None

    def to_context_block(self, max_chars: int = 400) -> str:
        """压成一段注入窗口的文本，控预算。"""
        parts = []
        if self.display_name:
            parts.append(f"称呼：{self.display_name}")
        if self.preferences:
            parts.append("偏好：" + "；".join(f"{k}={v}" for k, v in self.preferences.items()))
        if self.constraints:
            parts.append("约束：" + "；".join(self.constraints))
        if self.goals:
            parts.append("目标：" + "；".join(self.goals))
        block = "[用户画像] " + "。".join(parts)
        return block[:max_chars]
```

---

## 3. 画像的更新

画像必须随交互演进，否则很快过时。更新有三种动作，分别对应 [06-forgetting.md](./06-forgetting.md) 会展开的「增 / 改 / 删」：

```python
def update_profile(profile: UserProfile, key: str, value: str) -> UserProfile:
    """偏好更新：同 key 直接覆盖（用户改了主意），这就是冲突更新。"""
    profile.preferences[key] = value   # 覆盖而非追加，避免"既喜欢A又喜欢A的反面"
    profile.updated_at = datetime.now()
    return profile

# 用户先说"喜欢详细解释"，后来又说"还是简洁点吧"
update_profile(p, "verbosity", "detailed")
update_profile(p, "verbosity", "concise")   # 覆盖，画像里只剩 concise
```

更新时机延续 [03-storage-recall.md](./03-storage-recall.md) 的讨论：偏好类异步抽取即可，强约束（过敏、禁忌）热路径立即更新。

---

## 4. 注入示例（端到端）

把画像注入到一次真实调用里：

```python
import anthropic

client = anthropic.Anthropic()

# 1. 会话开始：从长期存储加载画像（这里用内存对象示意，生产走 DB）
profile = load_profile("u_42")   # 返回 UserProfile

# 2. 注入到 system —— 画像放 system 区，常驻且位置靠前（重要信息别埋中间）
def chat(profile: UserProfile, user_input: str, history: list[dict]) -> str:
    system = (
        "你是一个贴心的生活助手，回答时遵循用户画像里的偏好和约束。\n"
        + profile.to_context_block()
    )
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        system=system,                  # ← 画像注入这里
        messages=[*history[-8:], {"role": "user", "content": user_input}],
    )
    return resp.content[0].text

# 模型据此个性化：素食 + 不吃辣 + 上海 + 预算敏感 → 给出贴合的推荐
print(chat(profile, "推荐个周末聚餐的地方", history=[]))
```

要点：画像放 **system 区**（稳定、靠前、便于 prompt 缓存命中），别每轮都重新塞进 user 消息里造成重复。

---

## 5. 个性化 vs 隐私

个性化越深，隐私风险越大。几条原则：

| 原则 | 做法 |
|------|------|
| 最小必要 | 只存对体验有用的，别囤敏感信息（身份证、密码、健康细节除非业务必需） |
| 用户可见可控 | 让用户能查看 / 编辑 / 删除自己的记忆（OpenAI memory、Letta 都提供） |
| 敏感分级 | 高敏感字段单独加密 / 单独授权才注入 |
| 注入有度 | 别把全部画像一股脑塞窗口，按当前任务相关性裁剪 |
| 合规 | GDPR/个保法的"被遗忘权"——删除要真删，含向量库副本 |

```python
# ✅ 注入前过滤敏感字段，别让它进窗口/进日志
SENSITIVE = {"id_number", "health_detail", "payment"}

def safe_block(profile: UserProfile) -> str:
    filtered = {k: v for k, v in profile.preferences.items() if k not in SENSITIVE}
    return UserProfile(user_id=profile.user_id, preferences=filtered).to_context_block()
```

---

## 6. 多用户隔离：别把 A 的记忆泄漏给 B

这是个性化系统最容易出事故的地方——**记忆串户**。一个共享的记忆库里，如果检索/加载时漏了 `user_id`，就会把别人的画像注入当前用户的窗口，既是隐私事故也是质量灾难。

```python
# ❌ 致命：检索时没按用户过滤，召回全库
hits = qdrant.search(collection_name="memories", query_vector=v, limit=3)

# ✅ 强制 user_id 过滤，且把它放在数据访问层而非业务层（防漏）
from qdrant_client.models import Filter, FieldCondition, MatchValue

def recall_for_user(user_id: str, v: list[float], top_k: int = 3):
    return qdrant.search(
        collection_name="memories",
        query_vector=v,
        query_filter=Filter(must=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        ]),
        limit=top_k,
    )
```

工程上的硬措施：

| 措施 | 说明 |
|------|------|
| 物理隔离 | 每个用户独立 namespace / collection / 分区，从源头不可能串 |
| 访问层强制注入 user_id | 隔离逻辑下沉到数据层，业务代码想漏也漏不了 |
| 加 tenant 维度 | 多租户场景再叠一层 org_id，双重过滤 |
| 测试覆盖 | 专门写"用 A 的身份查不到 B 的记忆"的测试 |

记住：**多用户隔离不是功能，是安全边界。** 宁可隔离过度，不可串户一次。

---

## 7. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 画像无限增长 | 注入 token 爆、稀释 | 设上限 + 衰减（见 06） |
| 偏好只增不改 | 用户改了主意但画像还是旧的 | 按 key 覆盖更新 |
| 把画像塞进每轮 user 消息 | 重复 token、破坏缓存 | 放 system 区，稳定靠前 |
| 检索漏 user_id | 记忆串户 | 数据访问层强制过滤 |
| 存敏感信息 | 隐私/合规风险 | 最小必要 + 敏感字段过滤 |

---

## 8. 下一步

- 📖 画像里的过期/冲突/衰减怎么处理 → [06-forgetting.md](./06-forgetting.md)
- 📖 画像的存储与召回底座 → [03-storage-recall.md](./03-storage-recall.md)
- 📖 画像注入占多少预算 → [01-foundations/05-context-budget.md](../01-foundations/05-context-budget.md)
- 📖 注入到 system 区与 prompt 缓存的配合 → [01-foundations/04-cost-latency.md](../01-foundations/04-cost-latency.md)

## 参考资料

- OpenAI，"Memory and new controls for ChatGPT"：https://openai.com/index/memory-and-new-controls-for-chatgpt/
- Letta 记忆块（memory blocks）文档：https://docs.letta.com/concepts/memory
