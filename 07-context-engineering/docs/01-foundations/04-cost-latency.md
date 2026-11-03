# CE 04：上下文的成本与延迟模型

> 塞进窗口的每个 token 都要付钱，也都会拖慢响应。input token 按量计费，上下文越长成本线性上涨；prefill（把输入读进模型）的耗时也随上下文长度增长。这篇把成本和延迟各算一遍账，后面讲预算、压缩、缓存时，都会回到这两笔账上。

---

## 1. 计费模型：input 和 output 分开算

LLM API 基本都按 token 计费，input 和 output 单价不同，output 通常更贵：

```
单次调用成本 = input_tokens × input单价 + output_tokens × output单价
```

input token 的多少由你往里塞了多少决定。上下文里多塞 10K 的检索片段，这 10K 每次调用都要付钱。多轮对话的历史还会累积，第 10 轮要把前 9 轮全带上，input 像滚雪球一样增长。

官方标价（2026-11 查询，每百万 token，美元；价格变动频繁，下单前以官方定价页为准）：

| 模型 | input | output | 出处 |
|------|-------|--------|------|
| Claude Fable 5.1 | $10 | $50 | Anthropic Pricing |
| Claude Opus 5.5 | $4 | $20 | 同上 |
| Claude Sonnet 5.5 | $2 | $10 | 同上 |
| Claude Haiku 5.5 | $0.10（提示超 100K token 时 $0.50） | $0.50（超 100K 时 $2.50） | 同上 |
| GPT-6 Astra | $10 | $50 | OpenAI Models 页 |
| GPT-6.1 Sol | $2 | $10 | 同上 |
| GPT-6.1 Terra | $1 | $5 | 同上 |
| GPT-6 Luna | $0.10 | $0.50 | 同上 |
| Gemini 4 Argon | $2（官方页注明 2026-12-01 起涨到 $4） | $10（涨到 $20） | Gemini API Pricing |
| Gemini 3.8 Flash | $0.75（2027-01-01 起 $1.50） | $3.75 | 同上 |

千问各模型的价格请到阿里云百炼的模型详情页查，这里不列具体数字。

> 这张表有两个结构上的变化。一是旗舰和小模型的价差拉到了 10 倍到 100 倍，比以前大，按任务路由到不同模型更划算了。二是 Haiku 5.5 这类模型按提示长度分档计价，长上下文会落进更贵的档位，估算成本时别只看起步价。

> 这张表里 output 单价都是 input 的 5 倍，但在长上下文场景里，input 量大，反而是成本的主体。

---

## 2. 上下文越长，成本线性上涨

input token 翻倍，input 成本就翻倍，没有批量优惠。来算一笔账。

按 Claude Sonnet 5.5 的标价 $2 / 1M input 算，每次 output 固定约 500 token，每天调用 10,000 次，估算每月的 input 成本：

| 每次上下文长度 | 单次 input 成本 | 每天（×1万） | 每月（×30） |
|----------------|-----------------|--------------|-------------|
| 2K | $0.004 | $40 | ~$1,200 |
| 8K | $0.016 | $160 | ~$4,800 |
| 32K | $0.064 | $640 | ~$19,200 |
| 128K | $0.256 | $2,560 | ~$76,800 |
| 200K | $0.4 | $4,000 | ~$120,000 |

同样的业务，上下文从 8K 涨到 128K，月成本从约 4,800 美元涨到约 7.7 万美元，是 16 倍。那些为了以防万一多塞的上下文，花的都是真金白银。

```python
# 一个简单的成本估算函数
def monthly_cost(ctx_tokens, out_tokens, calls_per_day,
                 in_price_per_m, out_price_per_m):
    per_call = (ctx_tokens / 1e6 * in_price_per_m
                + out_tokens / 1e6 * out_price_per_m)
    return per_call * calls_per_day * 30

# Claude Sonnet 5.5（$2 / $10），128K 上下文 vs 8K 上下文
big   = monthly_cost(128_000, 500, 10_000, 2, 10)
small = monthly_cost(8_000,   500, 10_000, 2, 10)
print(f"128K: ${big:,.0f}/月")    # ≈ $78,300
print(f"8K:   ${small:,.0f}/月")  # ≈ $6,300
print(f"省下: ${big - small:,.0f}/月")  # ≈ $72,000
```

---

## 3. 延迟：prefill 随上下文增长

成本之外还有延迟。一次推理分两个阶段：

```
[Prefill 预填充]  把所有输入 token 读进去、算 KV cache
        ↓  这一步耗时随 输入长度 增长（受 O(n²) attention 影响）
[Decode 解码]     一个一个吐出 output token
        ↓  这一步耗时随 输出长度 增长
```

首 token 延迟（TTFT，Time To First Token）主要由 prefill 决定。输入越长，用户等第一个字出现的时间越久。

| 上下文长度 | prefill 体感（量级，因模型/硬件而异） |
|------------|----------------------------------------|
| 2K | 几乎瞬间，TTFT < 0.5s |
| 32K | 能感觉到在等，TTFT 1～3s |
| 128K | 明显卡顿，TTFT 数秒 |
| 200K | TTFT 可能 5～10s 以上 |

对话类产品里，TTFT 直接决定体验。一个 200K 的请求，用户要干等好几秒才看到第一个字，而这些上下文里很可能有一半是多余的。

---

## 4. Prompt Caching：把固定前缀省下来

如果上下文里有大段固定不变的前缀，比如 system prompt、工具定义、固定的知识库，prompt caching 可以把这部分缓存起来，之后命中缓存的 input 既便宜又省去了 prefill 时间：

- 命中缓存的 input token 比标准价便宜得多：Anthropic 默认是基础输入价的 10%，Opus 5.5 / Sonnet 5.5 是 5%，Fable 5.1 是 2.5%；OpenAI GPT-5.6 及之后的多数模型读缓存是 0.1×（GPT-6.1 Sol / Terra 是 0.05×）。但写缓存要多付钱：Anthropic 5 分钟写入是 1.25×，1 小时写入是 2×；OpenAI 写入是 1.25×。各家规则不一样，见 [Prompt Caching 与上下文复用](../07-long-context/03-prompt-caching.md)。
- 命中缓存就跳过了重复的 prefill，TTFT 会大幅下降。

```python
# Claude prompt caching：给固定前缀打 cache 标记
import anthropic
client = anthropic.Anthropic()
client.messages.create(
    model="claude-opus-5-5",
    max_tokens=1024,
    system=[{
        "type": "text",
        "text": LONG_FIXED_SYSTEM_PROMPT,      # 大段固定内容
        "cache_control": {"type": "ephemeral"}, # ← 缓存它
    }],
    messages=[{"role": "user", "content": user_input}],
)
```

> 缓存只对前缀完全一致的部分生效。所以固定的内容放前面、变化的内容放后面，这条排布规则在后面很多篇里都会用到。第 07 章有 prompt caching 的专题。

---

## 5. 用成本和延迟看日常决策

记住每个 token 都要花钱、都会拖慢响应，下面这些决策就好做了：

| 决策 | 从成本和延迟看 |
|------|----------------------|
| RAG 召回 top-5 还是 top-50 | 检索片段那部分的 input 约是 10 倍；整个请求涨多少，取决于 system、历史等其余部分占多大比例。片段多了也更慢，还会 rot |
| 历史留 20 轮还是摘要成 1 段 | 原样留着，input 越滚越大；摘要省钱也省时间 |
| 工具定义全塞还是按需加载 | 用不到的工具 schema 纯属浪费 |
| 用旗舰还是中档、小模型 | 同一代里旗舰的标价是小模型的几倍到几十倍（如 Claude Opus 5.5 $4 vs Haiku 5.5 $0.10 / 1M input），能降档就降 |

---

## 6. 常见坑

| 坑 | 实际情况 |
|----|------|
| 「只盯着 output 成本」 | 长上下文里 input 才是成本主体 |
| 「上下文长点无所谓，反正便宜」 | 乘上调用量和天数，线性涨成一个吓人的数 |
| 「延迟是模型慢，跟我没关系」 | TTFT 主要由你的输入长度决定 |
| 「缓存会自动生效」 | 看厂商：OpenAI 按前缀自动缓存；Anthropic 要打 `cache_control` 断点或在请求顶层开自动缓存，什么都不设就不缓存；千问、Gemini 有隐式缓存，也支持显式缓存。不管哪家，都要前缀完全一致才会命中 |
| 「换个大窗口模型就都解决了」 | 窗口变大不改变 token 计费和 prefill 的规律 |

---

## 7. 下一步

- 每个 token 都有代价，那就当预算来分配：[按预算分配上下文（Context Budget）](./05-context-budget.md)
- 少给上下文，同时省钱、省时间、提准确率：[最少必要上下文原则](./06-minimal-context.md)
- 长上下文的另一个代价，质量退化：[Context Rot 与 Lost in the Middle](./03-context-rot.md)
- prompt caching 的完整用法：[Prompt Caching 与上下文复用](../07-long-context/03-prompt-caching.md)

## 参考资料

- Anthropic Prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- OpenAI Pricing: https://developers.openai.com/api/docs/pricing
- Anthropic Pricing: https://platform.claude.com/docs/en/about-claude/pricing
