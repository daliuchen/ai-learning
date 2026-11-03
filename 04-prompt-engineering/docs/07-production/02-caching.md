# PE Production 02：Prompt Caching 实战

> 三家 API 都提供 prompt caching：请求里重复出现的那段输入，命中缓存的部分只按基础输入价的一小部分计费（Claude 默认 0.1×，最新几款更低）。前提是 prompt 的结构配合，不变的 system、few-shot、知识库都放在前面，每次只换 user message。这篇讲怎么调整 prompt，让缓存命中率尽量高。

---

## 1. Prompt Caching 是什么

这是 API 厂商提供的能力：如果你发送的 prompt 前缀和之前完全一样，这部分的计算结果会被缓存，下次直接复用，按更低的价格计费。

| 厂家 | 缓存命中价格 | 缓存写入开销 |
|------|--------------|--------------|
| Anthropic | 默认 0.1×；Opus 5.5 / Sonnet 5.5 为 0.05×（Sonnet 5.5 是 $0.10/M）；Fable 5.1 为 0.025× | 5 分钟档 1.25×，1 小时档 2× |
| OpenAI | GPT-6.1 Sol / Terra 为 0.05×；GPT-5.6 及之后的多数模型为 0.1× | 1.25×（默认 30 分钟）；Responses API 的 12 小时档 2.5× |
| Gemini | 以官方定价页为准 | 显式缓存另按存储时长计费 |

表里的倍数都是相对基础输入价。OpenAI 以前「命中打五折、写入不收费」的规则已经不适用，现在写入同样有溢价。几家的模式越来越接近：写入贵一点，读取很便宜，适合写一次、读很多次的用法。价格随模型更新会变，估成本时按所用模型查各家定价页。

---

## 2. 命中条件

按前缀匹配的缓存（OpenAI 自动缓存、Anthropic `cache_control`、Gemini 隐式缓存）都要求前缀完全相同，并且达到最短长度。Gemini 的显式缓存是另一种机制：先创建 cache 对象，后续请求按名字引用它（见第 6 节），不按前缀匹配。

| 厂家 | 最短缓存长度 | TTL |
|------|--------------|-----|
| Anthropic | 因模型而异（约 512–4096 tokens），太短会静默不缓存 | 默认 5 分钟，可选 1 小时 |
| OpenAI | 1024 tokens 量级 | 默认至少 30 分钟，可用 `prompt_cache_options` 调整 |
| Gemini | 隐式缓存自动 / 显式缓存按官方页的最小长度 | 显式缓存可设 ttl |

低于最短长度就不会缓存。怎么确认缓存生效了：看 `usage.cache_read_input_tokens`（Claude）或 `cached_tokens`（OpenAI），连续请求里如果一直是 0，说明有什么东西悄悄改了前缀。

---

## 3. 把不变的部分放在前面

错误的结构：

```
[User message]
任务: 分类客服反馈                          ← 指令
反馈内容: "{actual_feedback}"               ← 数据
约束: ...                                   ← 指令
示例: ...                                   ← 静态
```

每次 user 内容一变，前缀就变了，整段都要重算。

正确的结构：

```
[System]
任务: 分类客服反馈                          ← 静态
约束: ...                                   ← 静态
示例: ...                                   ← 静态

[User]
{actual_feedback}                          ← 只这部分变
```

system 每次都一样，后续调用就能命中缓存（Claude 还要在 system 末尾打 `cache_control` 断点，默认 TTL 5 分钟）。

---

## 4. Anthropic 显式缓存

```python
resp = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=1000,
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

也可以在 `create()` 顶层写一个 `cache_control={"type": "ephemeral"}`，让 API 自动选断点，不需要精细控制时这样最省事。缓存按前缀匹配，渲染顺序是 `tools → system → messages`，前缀里任何一个字节变了，后面全部失效。

`cache_control` 锚点可以设多个（最多 4 个），做成分层缓存：

```python
system=[
    {"type": "text", "text": "...通用指令..."},
    {"type": "text", "text": "...产品 A 知识...", "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "...产品 A 客户 X 历史..."},  # 最后一个断点之后的内容不进缓存，每次按原价算
],
```

---

## 5. OpenAI 自动缓存

OpenAI 的缓存是自动的。官方建议把可复用的指令放在 prompt 最前面，也就是请求参数里靠前的位置：

```python
resp = client.chat.completions.create(
    model="gpt-6.1-terra",
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

不需要写 `cache_control`，按前缀自动匹配。

---

## 6. Gemini 显式缓存

Gemini 提供显式的缓存 API：

```python
from google.genai import types
cache = client.caches.create(
    model="gemini-3.8-flash",
    config=types.CreateCachedContentConfig(
        system_instruction="...",
        contents=[long_context_doc],
        ttl="3600s",  # 1 小时
    ),
)

# 后续调用引用 cache
resp = client.models.generate_content(
    model="gemini-3.8-flash",
    contents="user question",
    config=types.GenerateContentConfig(cached_content=cache.name),
)
```

---

## 7. 提高命中率的几个办法

### 7.1 固定内容挪进 system
经常要用到的固定内容，写进 system：

```
SYSTEM = """你是 XXX 助手。
{base_instructions}
{static_examples}
{kb_summary}    ← 把固定 KB 放这
"""
```

不要每次都拼到 user 里。

### 7.2 RAG context：答案缓存和 prompt caching 是两回事
RAG 检索回来的内容每次都不同，放在 prompt 前部会让前缀每次都变。这里容易混的是两种缓存：

- 答案缓存（应用层）：自己维护一张「问题 → 最终回答」的表，热门问题命中就直接返回，根本不调模型。它省的是整次调用，但只适合答案不随用户、时间变化的问题，而且要自己处理失效。
- Prompt caching（厂商侧）：模型照常调用，只是重复的前缀按缓存价计费。它不缓存输出，每次回答都是新生成的。

```python
# 答案缓存：应用层，命中就不调模型
ANSWER_CACHE = {
    "怎么退款": "...",
    "怎么改密码": "...",
}

if query in ANSWER_CACHE:
    return ANSWER_CACHE[query]
```

想让 RAG 场景吃到 prompt caching，要调整 prompt 结构：固定的 system、few-shot 放最前面，检索结果和用户问题放最后，这样至少固定部分能命中；同一份文档被反复提问时（比如对一份长合同连续追问），把整份文档放在前缀里，后续追问都能命中。

### 7.3 Few-shot 放在变化内容之前
缓存按 `tools → system → messages` 的顺序做前缀匹配，few-shot 放在 system 里，或者写成 messages 开头的几轮固定对话，都能被缓存，只要它们每次一模一样、并且排在真实输入之前：

```python
# ❌ 示例按输入动态挑选（或顺序每次打乱），前缀每次都不同
system = BASE + format_examples(pick_examples(user_input))

# ✅ 固定示例放在前面，真实输入放最后
system=f"""任务...

<examples>
...
</examples>
"""
messages=[{"role": "user", "content": "真实输入"}]
```

### 7.4 工具描述也能缓存
很长的工具描述放在 tools 参数里，同样可以被缓存。

---

## 8. 监控命中率

```python
def call_and_log(messages, system):
    resp = client.messages.create(...)
    usage = resp.usage
    cached = usage.cache_read_input_tokens
    # Claude 的 input_tokens 不含缓存读写部分，总输入要三项相加
    total = usage.input_tokens + cached + usage.cache_creation_input_tokens
    hit_rate = cached / total if total else 0
    
    log.info("cache_metrics",
             cache_hit_rate=hit_rate,
             cached_tokens=cached,
             new_tokens=usage.input_tokens)
    return resp
```

要盯的指标有三个：命中率（目标 80% 以上）、写入开销、整体节省的 token 比例。

---

## 9. 算一笔账

场景：客服分类器，每天 10 万次调用，每次 system 1500 token，user 100 token。

不开缓存：
```
每次成本: (1500 + 100) * input_price + output_price
日成本: 10 万 * 全量
```

开缓存（5 分钟 TTL 内大量命中）：
```
首次: 1500 * 1.25x + 100 * 1x（写入 + 新增）
之后: 1500 * 0.1x + 100 * 1x（缓存命中 + 新增）

节省: 90% * 1500 = 1350 token / 次 节省
```

这里按 Claude 默认的 0.1× 读取价算；Opus 5.5 / Sonnet 5.5 读取价是 0.05×，省得更多。每天 10 万次，就是少付 1.35 亿 token 的全价输入费，乘上所用模型的单价就是每天省下的钱。

---

## 10. 缓存设计的反例

| 反例 | 问题 |
|------|------|
| user message 里带着静态指令 | 整段没法缓存 |
| 时间戳放在 system 开头 | 每分钟变一次，缓存全部失效 |
| 缓存内容低于最短长度 | 不会缓存（Claude 因模型而异，OpenAI 约 1024 token） |
| system 频繁更新 | 一直 cache miss |

---

## 11. demo：测量缓存效果

```python
import time, anthropic
client = anthropic.Anthropic()


LONG_SYSTEM = "你是企业知识助手。\n" + "（这里放一份固定的公司政策文本）\n" * 800  # 凑到几千 token；最短缓存长度因模型而异

def call_cached(question: str):
    resp = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system=[
            {"type": "text", "text": LONG_SYSTEM, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": question}],
    )
    return resp


# 首次调用（缓存写入）
print("=== 首次 ===")
r1 = call_cached("怎么改密码")
print(r1.usage)  # cache_creation_input_tokens > 0

# 间隔 < 5min 再调（命中）
time.sleep(2)
print("\n=== 第二次 ===")
r2 = call_cached("怎么退款")
print(r2.usage)  # cache_read_input_tokens 应 > 0
```

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| system 不变，user 每次有小变化 | 没问题，system 照样能缓存 |
| system 里有动态内容（时间戳） | 整段失效；把动态部分挪到 user |
| 缓存内容低于最短长度 | 达不到缓存条件，静默不缓存 |
| 没记录 usage | 不知道命中率是多少 |
| TTL 过期后重新写入 | 低频接口要考虑 5 分钟 TTL 会不会过期（高频请求命中会刷新有效期） |
| 跨用户共享缓存有没有隐私问题 | 缓存在厂商侧按组织隔离，别的客户读不到；缓存的是输入前缀，不缓存输出 |

---

## 13. 下一步

- [Prompt Templating（Jinja 与框架内置模板）](./03-templating.md)
- [A/B 与可观测](./04-ab-observability.md)

## 参考资料

- Anthropic Prompt Caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- OpenAI Prompt Caching: https://platform.openai.com/docs/guides/prompt-caching
- Gemini Context Caching: https://ai.google.dev/gemini-api/docs/caching
