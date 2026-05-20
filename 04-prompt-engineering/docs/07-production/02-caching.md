# PE Production 02：Prompt Caching 实战

> **一句话**：把不变的 system / few-shot / 知识库放前面，每次只换 user message——三家 API 都有 prompt caching 可让"重复输入"的 token 价格降到 1/10。本篇讲怎么改 prompt 让缓存命中率最大化。

---

## 1. Prompt Caching 是什么

API 商提供的能力：发送相同前缀的 prompt 时，**缓存**这部分计算，下次直接复用，省 token cost：

| 厂家 | 缓存命中价格 | 缓存写入开销 |
|------|--------------|--------------|
| Anthropic | 10% 原价 | 125% 原价 |
| OpenAI | 50% 原价 | 与原价同 |
| Gemini | 25% 原价 | 与原价同 |

Anthropic 折扣最大——但写入开销也最大（首次贵 25%）。

---

## 2. 命中条件

三家都要求：**完全相同的前缀** + **最短长度**：

| 厂家 | 最短缓存长度 | TTL |
|------|--------------|-----|
| Anthropic | 1024 tokens | 5min |
| OpenAI | 1024 tokens | 自动 |
| Gemini | 32768 tokens（标准） / 4096（隐式） | 1h+ |

低于最短长度不缓存。

---

## 3. Prompt 设计：把"不变的"放前面

错误设计：

```
[User message]
任务: 分类客服反馈                          ← 指令
反馈内容: "{actual_feedback}"               ← 数据
约束: ...                                   ← 指令
示例: ...                                   ← 静态
```

每次 user 内容变 → cache key 改 → 全部重算。

正确设计：

```
[System]
任务: 分类客服反馈                          ← 静态
约束: ...                                   ← 静态
示例: ...                                   ← 静态

[User]
{actual_feedback}                          ← 只这部分变
```

system 完全相同 → 5min 内重复调用 → 缓存命中。

---

## 4. Anthropic 显式缓存

```python
resp = client.messages.create(
    model="claude-sonnet-4-6",
    system=[
        {"type": "text", "text": "...固定指令..."},
        # 在你想"切到缓存"的地方加 cache_control
        {"type": "text", "text": "...更多固定...", "cache_control": {"type": "ephemeral"}},
    ],
    messages=[{"role": "user", "content": "{user_input}"}],
)

# 看响应里的 usage
print(resp.usage)
# {
#   "input_tokens": 50,            ← 这次实际 input
#   "cache_creation_input_tokens": 0,  ← 写入缓存的（首次会有）
#   "cache_read_input_tokens": 1500,   ← 从缓存读的（命中部分）
# }
```

可以多个 `cache_control` 锚点（最多 4 个），形成"分层缓存"：

```python
system=[
    {"type": "text", "text": "...通用指令..."},
    {"type": "text", "text": "...产品 A 知识...", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "...产品 A 客户 X 历史..."},  # 这部分会一起被缓存
],
```

---

## 5. OpenAI 自动缓存

OpenAI 自动缓存——但有"未缓存的代价":

```python
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "...长 system..."},  # 自动缓存
        {"role": "user", "content": "..."},
    ],
)
print(resp.usage)
# {
#   "prompt_tokens": 1500,
#   "prompt_tokens_details": {"cached_tokens": 1200},  ← 命中数
# }
```

OpenAI 不需要 `cache_control` 字段——自动按前缀匹配。

---

## 6. Gemini 显式缓存

Gemini 有 explicit caching API：

```python
from google.genai import types
cache = client.caches.create(
    model="gemini-2.0-flash",
    config=types.CreateCachedContentConfig(
        system_instruction="...",
        contents=[long_context_doc],
        ttl="3600s",  # 1 小时
    ),
)

# 后续调用引用 cache
resp = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="user question",
    config=types.GenerateContentConfig(cached_content=cache.name),
)
```

---

## 7. 命中率优化技巧

### 7.1 把高频 user 放成 system
把高频提问写成 system 一部分：

```
SYSTEM = """你是 XXX 助手。
{base_instructions}
{static_examples}
{kb_summary}    ← 把固定 KB 放这
"""
```

而不是每次拼到 user。

### 7.2 RAG context 的缓存
RAG 的 retrieved context 每次都变——但**热门查询**可缓存：

```python
# 缓存 top-10 高频问题的 retrieval 结果
HOT_QUESTIONS_CACHE = {
    "怎么退款": "...",
    "怎么改密码": "...",
}

if query in HOT_QUESTIONS_CACHE:
    # 命中: 同样 prompt 同样 context
    return cached_call(query)
```

### 7.3 Few-shot 放 system 而非 messages
```python
# ❌ Few-shot 在 messages，每次重新计算前面所有 turn
messages=[
    {"role": "user", "content": "示例 1 输入"},
    {"role": "assistant", "content": "示例 1 输出"},
    ...
    {"role": "user", "content": "真实输入"},
]

# ✅ Few-shot 在 system，cacheable
system=f"""任务...

<examples>
...
</examples>
"""
messages=[{"role": "user", "content": "真实输入"}]
```

### 7.4 Tool 描述也是 cacheable
长 tool 描述放 tools 参数——可缓存。

---

## 8. 监控缓存命中

```python
def call_and_log(messages, system):
    resp = client.messages.create(...)
    usage = resp.usage
    cached = usage.cache_read_input_tokens
    total = usage.input_tokens + cached
    hit_rate = cached / total if total else 0
    
    log.info("cache_metrics",
             cache_hit_rate=hit_rate,
             cached_tokens=cached,
             new_tokens=usage.input_tokens)
    return resp
```

监控指标：
- 命中率（目标 80%+）
- 写入开销
- 整体 token 节省 %

---

## 9. 成本计算示例

场景：客服分类器，每天 10 万次调用，每次 system 1500 token + user 100 token。

无缓存：
```
每次成本: (1500 + 100) * input_price + output_price
日成本: 10 万 * 全量
```

有缓存（5min TTL 内大量命中）：
```
首次: 1500 * 1.25x + 100 * 1x（写入 + 新增）
之后: 1500 * 0.1x + 100 * 1x（缓存命中 + 新增）

节省: 90% * 1500 = 1350 token / 次 节省
```

10 万次 × 1350 token × 单价 = 大几百美元 / 天 节省。

---

## 10. 缓存设计反例

| 反例 | 问题 |
|------|------|
| user message 含静态指令 | 整段不可缓存 |
| 时间戳放 system 头部 | 每分钟变一次，全失效 |
| 缓存内容 < 1024 token | 没命中条件 |
| 高频更新 system | 永远 cache miss |

---

## 11. demo：测量缓存效果

```python
# demos/production/02_caching_test.py
import time, anthropic
client = anthropic.Anthropic()


LONG_SYSTEM = """你是企业知识助手。""" + "..." * 500  # 拼到 > 1024 token

def call_cached(question: str):
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=[
            {"type": "text", "text": LONG_SYSTEM, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": question}],
    )
    return resp


# 首次调用（缓存写入）
print("=== 首次 ===")
r1 = call_cached("怎么改密码")
print(r1.usage)
print(f"用时: ...")

# 间隔 < 5min 再调（命中）
time.sleep(2)
print("\n=== 第二次 ===")
r2 = call_cached("怎么退款")
print(r2.usage)
print(f"cache_read_input_tokens 应 > 0")
```

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| **system 不变但 user 每次小变化** | OK，system 仍缓存 |
| **system 含动态内容（时间戳）** | 整段失效；动态部分挪 user |
| **缓存内容 < 1024 token** | 没命中条件，扩长 |
| **没记 usage** | 不知道命中率 |
| **TTL 过期重写** | 高频接口要考虑 5min TTL，可能不够 |
| **跨用户共享缓存（隐私？）** | 仅缓存 prompt 内容，不缓存输出，安全 |

---

## 13. 下一步

- 📖 Templating → [03-templating.md](./03-templating.md)
- 📖 A/B 与可观测 → [04-ab-observability.md](./04-ab-observability.md)

## 参考资料

- Anthropic Prompt Caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- OpenAI Prompt Caching: https://platform.openai.com/docs/guides/prompt-caching
- Gemini Context Caching: https://ai.google.dev/gemini-api/docs/caching
