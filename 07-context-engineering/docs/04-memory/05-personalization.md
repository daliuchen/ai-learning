# CE 04-05：用户画像与个性化上下文

> 个性化做的事情，就是把「这个用户是谁、偏好什么」持续而精炼地放进每次调用的上下文窗口。驱动它的是一份随会话演进的 user profile：先构建，再不断更新，按预算注入。同时有三件事不能做：注入隐私敏感信息，让画像无限膨胀，把 A 的画像泄漏给 B。

---

## 1. 个性化就是持续告诉模型「你是谁」

没有个性化的助手，每次都像第一次见你。有个性化的助手，每次窗口里都带着一小段关于你的上下文：

```
# ❌ 无画像：每次从零开始
System: 你是一个助手。
User: 推荐个餐厅

# ✅ 注入画像：模型知道你是谁
System: 你是一个助手。
[用户画像] 张三，素食者，住上海，预算敏感，偏好日料，不吃辣。
User: 推荐个餐厅   → 模型直接推上海的平价素食日料
```

画像在短期窗口里只占一小块，但它来自长期记忆（见 [短期记忆 vs 长期记忆](./01-short-vs-long.md)）：每次会话开始，把该用户的画像从存储里取出来，压成一段，放进窗口。它和按 query 语义召回的记忆片段（[记忆的存储与召回](./03-storage-recall.md)）互相补充：画像是常驻的核心身份，召回片段是随话题变化的相关经历。

---

## 2. user profile 的构建

画像不是一张一次填完的表单，它来自两条路：从交互里慢慢沉淀，以及主动询问。

| 来源 | 例子 | 特点 |
|------|------|------|
| 显式声明 | 用户设置里选了"简洁回复" | 可信度高，直接写 |
| 对话推断 | 用户多次问 Rust → 推断偏好 Rust | 需置信度，可能误判 |
| 行为信号 | 总是跳过长解释 | 隐性，需聚合 |

一个实用的 profile schema，有结构，也方便以后扩展：

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

画像不跟着交互更新，很快就会过时。更新动作分增、改、删三种，[记忆的遗忘与更新](./06-forgetting.md) 会详细展开。这里先看最常见的改：

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

更新时机沿用 [记忆的存储与召回](./03-storage-recall.md) 里的结论：偏好类异步抽取就够了，过敏、禁忌这类强约束要在热路径上立即更新。

---

## 4. 注入示例（端到端）

把画像放进一次真实调用：

```python
import anthropic

client = anthropic.Anthropic()

# 1. 会话开始：从长期存储加载画像（这里用内存对象示意，生产走 DB）
profile = load_profile("u_42")   # 返回 UserProfile

# 2. 注入到 system —— 画像放 system 区，常驻且位置靠前（重要信息别埋中间）
def chat(profile: UserProfile, user_input: str, history: list[dict]) -> str:
    system = [
        # 通用指令：所有用户相同，放最前面，够长时可以加 cache_control 共享缓存
        {"type": "text", "text": "你是一个贴心的生活助手，回答时遵循用户画像里的偏好和约束。"},
        # 画像：因人而异，跟在通用指令后面
        {"type": "text", "text": profile.to_context_block()},
    ]
    resp = client.messages.create(
        model="claude-sonnet-5-5",
        max_tokens=1024,
        system=system,                  # ← 画像注入这里
        messages=[*history[-8:], {"role": "user", "content": user_input}],
    )
    return next(b.text for b in resp.content if b.type == "text")

# 模型据此个性化：素食 + 不吃辣 + 上海 + 预算敏感 → 给出贴合的推荐
print(chat(profile, "推荐个周末聚餐的地方", history=[]))
```

画像放在 system 区：位置靠前，对同一个用户整段会话都不变，同一用户的后续轮次可以命中缓存。它因人而异，所以要排在所有用户共用的通用指令之后，别让它打断通用前缀的缓存（[系统指令层](../02-anatomy/01-system-instructions.md) 说的「别把用户信息焊进 system」，指的就是把它拼进通用指令中间）。也不要每轮都重新塞进 user 消息，那样只会重复。

---

## 5. 个性化 vs 隐私

个性化做得越深，隐私风险越大。下面几条原则：

| 原则 | 做法 |
|------|------|
| 最小必要 | 只存对体验有用的，别囤敏感信息（身份证、密码、健康细节除非业务必需） |
| 用户可见可控 | 让用户能查看 / 编辑 / 删除自己的记忆（OpenAI memory、Letta 都提供） |
| 敏感分级 | 高敏感字段单独加密 / 单独授权才注入 |
| 注入有度 | 别把全部画像一股脑塞窗口，按当前任务相关性裁剪 |
| 合规 | GDPR/个保法的"被遗忘权"：删除要真删，含向量库副本 |

```python
# ✅ 注入前过滤敏感字段，别让它进窗口/进日志
SENSITIVE = {"id_number", "health_detail", "payment"}

def safe_block(profile: UserProfile) -> str:
    filtered = {k: v for k, v in profile.preferences.items() if k not in SENSITIVE}
    return UserProfile(user_id=profile.user_id, preferences=filtered).to_context_block()
```

---

## 6. 多用户隔离：别把 A 的记忆泄漏给 B

个性化系统最容易出事故的地方是记忆串户。在共享的记忆库里，检索或加载时漏了 `user_id`，别人的画像就会进入当前用户的窗口，既是隐私事故，回答质量也会一塌糊涂。

```python
# ❌ 致命：检索时没按用户过滤，召回全库
hits = qdrant.query_points(collection_name="memories", query=v, limit=3).points

# ✅ 强制 user_id 过滤，且把它放在数据访问层而非业务层（防漏）
from qdrant_client.models import Filter, FieldCondition, MatchValue

def recall_for_user(user_id: str, v: list[float], top_k: int = 3):
    return qdrant.query_points(
        collection_name="memories",
        query=v,
        query_filter=Filter(must=[
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        ]),
        limit=top_k,
    ).points
```

工程上可以落地的措施：

| 措施 | 说明 |
|------|------|
| 物理隔离 | 每个用户独立 namespace / collection / 分区，从源头不可能串 |
| 访问层强制注入 user_id | 隔离逻辑下沉到数据层，业务代码想漏也漏不了 |
| 加 tenant 维度 | 多租户场景再叠一层 org_id，双重过滤 |
| 测试覆盖 | 专门写"用 A 的身份查不到 B 的记忆"的测试 |

多用户隔离应该当作安全边界来对待。我宁可隔离得过度一点，也不要串户一次。

---

## 7. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 画像无限增长 | 注入 token 暴涨、注意力被稀释 | 设上限 + 衰减（见 [记忆的遗忘与更新](./06-forgetting.md)） |
| 偏好只增不改 | 用户改了主意但画像还是旧的 | 按 key 覆盖更新 |
| 把画像塞进每轮 user 消息 | 重复 token、破坏缓存 | 放 system 区，稳定靠前 |
| 检索漏 user_id | 记忆串户 | 数据访问层强制过滤 |
| 存敏感信息 | 隐私/合规风险 | 最小必要 + 敏感字段过滤 |

---

## 8. 下一步

- 画像的过期、冲突、衰减怎么处理：[记忆的遗忘与更新](./06-forgetting.md)
- 画像背后的存储与召回：[记忆的存储与召回](./03-storage-recall.md)
- 画像注入该占多少预算：[Context Budget](../01-foundations/05-context-budget.md)
- 放在 system 区和 prompt 缓存怎么配合：[上下文的成本与延迟模型](../01-foundations/04-cost-latency.md)

## 参考资料

- OpenAI，"Memory and new controls for ChatGPT"：https://openai.com/index/memory-and-new-controls-for-chatgpt/
- Letta 记忆块（memory blocks）文档：https://docs.letta.com/
