# CE 04：上下文的成本与延迟模型

> **一句话**：每一个塞进窗口的 token 都要付费、都要拖慢响应。input token 按量计费、长上下文成本线性涨；prefill（把输入读进去）的延迟随上下文长度增长。建立「每个 token 都有代价」的工程意识，是 Context Engineering 的经济学地基。

---

## 1. 计费模型：input 和 output 分开算

LLM API 几乎都按 token 计费，且 **input 和 output 单价不同**（output 通常更贵）：

```
单次调用成本 = input_tokens × input单价 + output_tokens × output单价
```

关键点：**input token 是你「塞进去多少」决定的**。你往上下文里多塞 10K 检索片段，这 10K 每次调用都要付费。多轮对话里历史会累积——第 10 轮要把前 9 轮全带上，**input 是滚雪球式增长的**。

参考单价量级（2025-2026，每百万 token，单位美元，实际以官方为准）：

| 模型 | input | output |
|------|-------|--------|
| GPT-4o | ~$2.5 | ~$10 |
| GPT-4o mini | ~$0.15 | ~$0.6 |
| Claude Sonnet | ~$3 | ~$15 |
| Claude Opus | ~$15 | ~$75 |
| Claude Haiku | ~$0.8 | ~$4 |
| Gemini Flash | ~$0.1 | ~$0.4 |

> output 单价常是 input 的 4～5 倍。但在长上下文场景，**input 因为量大，反而是成本主体**。

---

## 2. 长上下文 = 成本线性涨

input token 翻倍，input 成本就翻倍——线性关系，没有「批量优惠」。来算一笔账：

假设用 Claude Sonnet（input ~$3 / 1M），每次 output 固定 ~500 token，每天 10,000 次调用，估算**每月** input 成本：

| 每次上下文长度 | 单次 input 成本 | 每天（×1万） | 每月（×30） |
|----------------|-----------------|--------------|-------------|
| 2K | $0.006 | $60 | ~$1,800 |
| 8K | $0.024 | $240 | ~$7,200 |
| 32K | $0.096 | $960 | ~$28,800 |
| 128K | $0.384 | $3,840 | ~$115,200 |
| 200K（塞满） | $0.6 | $6,000 | ~$180,000 |

看清楚了：**同样的业务，上下文从 8K 涨到 128K，月成本从 7 千刀飙到 11 万刀**——16 倍。这就是为什么「能塞 ≠ 该塞」。多塞的那些「以防万一」的上下文，是在烧真金白银。

```python
# 一个简单的成本估算函数
def monthly_cost(ctx_tokens, out_tokens, calls_per_day,
                 in_price_per_m, out_price_per_m):
    per_call = (ctx_tokens / 1e6 * in_price_per_m
                + out_tokens / 1e6 * out_price_per_m)
    return per_call * calls_per_day * 30

# Claude Sonnet，128K 上下文 vs 8K 上下文
big   = monthly_cost(128_000, 500, 10_000, 3, 15)
small = monthly_cost(8_000,   500, 10_000, 3, 15)
print(f"128K: ${big:,.0f}/月")    # ≈ $117,450
print(f"8K:   ${small:,.0f}/月")  # ≈ $9,450
print(f"省下: ${big - small:,.0f}/月")  # ≈ $108,000
```

---

## 3. 延迟：prefill 随上下文增长

成本之外还有延迟。一次推理分两阶段：

```
[Prefill 预填充]  把所有输入 token 读进去、算 KV cache
        ↓  这一步耗时随 输入长度 增长（受 O(n²) attention 影响）
[Decode 解码]     一个一个吐出 output token
        ↓  这一步耗时随 输出长度 增长
```

关键：**首 token 延迟（TTFT, Time To First Token）主要由 prefill 决定**。你的输入越长，用户等第一个字出现的时间越久。

| 上下文长度 | prefill 体感（量级，因模型/硬件而异） |
|------------|----------------------------------------|
| 2K | 几乎瞬间，TTFT < 0.5s |
| 32K | 明显感觉到等待，TTFT 1～3s |
| 128K | 卡顿明显，TTFT 数秒 |
| 200K 满窗 | TTFT 可能 5～10s+ |

对话类产品里，TTFT 是体验生死线。**一个塞满 200K 的请求，用户要干等好几秒才看到第一个字**——而这些上下文里大概率有一半是冗余的。

---

## 4. Prompt Caching：把固定前缀省下来

如果你的上下文有大段**固定不变的前缀**（system prompt、工具定义、固定知识库），prompt caching 能把这部分缓存起来，后续命中缓存的 input **既省钱又省 prefill 时间**：

- 命中缓存的 input token 通常只按 ~10% 价格计费（各家不同）。
- 缓存命中跳过重复 prefill，TTFT 大幅下降。

```python
# Claude prompt caching：给固定前缀打 cache 标记
import anthropic
client = anthropic.Anthropic()
client.messages.create(
    model="claude-opus-4-20250514",
    max_tokens=1024,
    system=[{
        "type": "text",
        "text": LONG_FIXED_SYSTEM_PROMPT,      # 大段固定内容
        "cache_control": {"type": "ephemeral"}, # ← 缓存它
    }],
    messages=[{"role": "user", "content": user_input}],
)
```

> 注意：缓存只对**前缀完全一致**的部分有效。所以把「固定的」放前面、「变化的」放后面，是 CE 的一个重要排布技巧。详见本手册第 07 章 prompt caching 专题。

---

## 5. 工程意识：每个 token 都要付费且拖慢响应

把这句话刻进脑子，CE 的很多决策就自然了：

| 决策 | 成本/延迟视角下的考量 |
|------|----------------------|
| RAG 召回 top-5 还是 top-50 | top-50 多花 10 倍 input 钱，还更慢、还 rot |
| 历史留 20 轮还是摘要成 1 段 | 原样留 → input 滚雪球；摘要 → 省钱省时 |
| 工具定义全塞还是按需加载 | 用不到的工具 schema 是纯浪费 |
| 用 Opus 还是 Sonnet/Haiku | 长上下文下 Opus 贵 5 倍，能降档就降 |

---

## 6. 常见坑

| 坑 | 真相 |
|----|------|
| 「只盯 output 成本」 | 长上下文里 input 才是成本主体 |
| 「上下文长点无所谓，反正便宜」 | 乘以调用量和天数，线性涨成惊人数字 |
| 「延迟是模型慢，跟我无关」 | TTFT 主要由你的输入长度决定 |
| 「缓存自动生效」 | 需显式标记，且前缀要完全一致才命中 |
| 「换大窗口模型解决一切」 | 窗口大不改变 token 计费和 prefill 规律 |

---

## 7. 下一步

- 📖 既然每 token 有代价，那就把它当预算分配 → [05-context-budget.md](./05-context-budget.md)
- 📖 最少必要上下文：省钱省时省准确率 → [06-minimal-context.md](./06-minimal-context.md)
- 📖 长上下文的另一个代价（质量退化） → [03-context-rot.md](./03-context-rot.md)
- 📖 prompt caching 的系统玩法，见本手册 → 07 章 Prompt Caching 专题

## 参考资料

- Anthropic Prompt caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- OpenAI Pricing: https://openai.com/api/pricing/
- Anthropic Pricing: https://www.anthropic.com/pricing
