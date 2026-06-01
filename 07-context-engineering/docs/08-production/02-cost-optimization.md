# CE 08-02：上下文成本优化实战

> **一句话**：上下文成本 = 每次调用的 token 量 × 单价 × 调用次数。优化的正确顺序是「先量后砍」——拿上一篇的 token 占比表找出最大头，对症下药，而不是一上来就压历史。在 Agent 场景里，工具定义和历史往往才是吞金兽，而它们恰好是最容易被 prompt caching 干掉的部分。

---

## 1. 先搞清楚账单怎么算：input / output / cache 三笔钱

2025-2026 的主流计费拆成三档，单价差一个数量级，优化策略完全不同：

| 计费项 | 相对单价（以 input 为 1×） | 含义 | 优化抓手 |
|--------|---------------------------|------|----------|
| input（fresh） | 1× | 首次进入、未命中缓存的输入 token | 压缩、裁剪、减少检索 |
| cache write | ~1.25× | 写入 prefix 缓存（比普通 input 略贵） | 一次写、多次读才划算 |
| cache read（hit） | ~0.1× | 命中缓存的输入 token | **最大杠杆**：让稳定前缀全部命中 |
| output | 通常 3~5× input | 模型生成的 token | 限制 `max_tokens`、要结构化简短输出 |

关键洞察：**cache read 只要 input 的 1/10**。一个 6K token 的稳定系统前缀，命中后等于只花 600 token 的钱。这就是为什么「先把前缀缓存做对」往往比任何压缩都见效快。

---

## 2. 降本手段清单（按性价比排序）

| 手段 | 砍的是什么 | 典型收益 | 代价 / 风险 |
|------|-----------|----------|-------------|
| prompt caching | 稳定前缀（system + 工具定义 + 长指令）重复计费 | input 成本降 50~90% | 前缀必须逐字节稳定 |
| 压缩 / 摘要历史 | 远期对话历史的原文 token | 长会话省 30~70% | 摘要可能丢关键约束 |
| 裁剪工具定义 | 冗长的 JSON schema、用不上的工具 | 每次省几 K token | 裁错了 Agent 调不到工具 |
| 减少检索 k | 召回过多的低相关片段 | 检索段 token 减半 | k 太小会漏召回 → 幻觉 |
| 小模型做预处理 | 用 Haiku/mini 做改写、路由、抽取 | 整体成本降，主模型只干硬活 | 多一跳，链路变复杂 |
| batch API | 异步非实时任务 | 价格直接打 5 折 | 不能实时返回（分钟级延迟） |

---

## 3. 黄金法则：按占比找最大头先优化

别凭感觉。先跑上一篇 [01-observability.md](./01-observability.md) 的占比表，谁占大头先砍谁。

```
部分            tokens     占比
tools            5980    33.0%   ← 最大头，但稳定 → 用 caching 干掉
history          8120    44.8%   ← 次大头，且在变 → 压缩
retrieved        1450     8.0%   ← 占比小，别动它，动了反而漏召回
```

诊断逻辑：

- **占比大 + 稳定**（tools、长 system）→ 上 prompt caching，几乎零代价
- **占比大 + 变动**（history）→ 压缩 / 摘要 / 滑窗
- **占比小**（retrieved、user）→ 别折腾，省不出几个钱还容易把效果搞坏

```python
# ❌ 一上来就压检索片段 —— 它只占 8%，还压出了漏召回幻觉
retrieved = retrieved[:1]

# ✅ 先把占 33% 的稳定工具定义和 system 缓存掉
system = [{
    "type": "text",
    "text": SYSTEM_PROMPT + TOOLS_DESCRIPTION,   # 长且稳定的前缀
    "cache_control": {"type": "ephemeral"},       # 缓存断点打在这里
}]
```

---

## 4. prompt caching 实操（Anthropic 为例）

把**稳定前缀**和**变动尾部**切开，断点打在两者边界。前缀越长、复用越频繁，越赚。

```python
import anthropic
client = anthropic.Anthropic()

resp = client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    system=[{
        "type": "text",
        "text": LONG_STABLE_SYSTEM_PROMPT,     # 几千 token、每次都一样
        "cache_control": {"type": "ephemeral"},  # ← 缓存到此为止
    }],
    tools=MY_TOOLS,  # 工具定义也在缓存前缀内（顺序固定才命中）
    messages=conversation,  # 变动部分放后面
)
u = resp.usage
print(f"fresh={u.input_tokens} cache_read={u.cache_read_input_tokens} "
      f"cache_write={u.cache_creation_input_tokens} output={u.output_tokens}")
```

命中条件提醒：前缀**逐字节相同**才命中。所以别把时间戳、随机 session id、用户名拼在 system 开头——把它们挪到 messages 里，让前缀保持纯净。

---

## 5. 小模型做预处理：链路降本

不是所有步骤都需要旗舰模型。常见拆法：

```python
# 用便宜的小模型做「把用户原话改写成检索 query」这种简单活
cheap = client.messages.create(
    model="claude-haiku-4-5",   # 便宜、快
    max_tokens=128,
    messages=[{"role": "user", "content": f"把下面问题改写成 3 个检索关键词：{user_q}"}],
)
queries = parse(cheap)

# 主模型只在「带着精准检索结果做最终推理」时上场
answer = client.messages.create(model="claude-sonnet-4-5", messages=build_ctx(queries), max_tokens=1024)
```

路由、意图分类、查询改写、结果抽取——这些都让 Haiku/mini 干，旗舰模型只干「需要强推理」的最后一步。

---

## 6. 优化前后对比案例

一个客服 Agent，日均 10 万次调用，平均每次 18K input token、800 output token。Sonnet 假设 input \$3 / output \$15 每百万 token。

**优化前**：每次全是 fresh input。
- input：18000 × \$3/M = \$0.054
- output：800 × \$15/M = \$0.012
- 单次 ≈ \$0.066 → 日成本 ≈ **\$6,600**

**优化后**三板斧：
1. system + 工具定义（约 8K，占 44%）全部 prompt caching，命中后按 0.1× 计 → 8000 × \$0.3/M ≈ \$0.0024
2. 历史从 8K 压缩摘要到 2.5K → fresh 部分剩 ≈ 2500（含 user/检索）
3. output 限制到结构化短回答约 400 token

- cached input：8000 × \$0.3/M = \$0.0024
- fresh input：2500 × \$3/M = \$0.0075
- output：400 × \$15/M = \$0.006
- 单次 ≈ \$0.0159 → 日成本 ≈ **\$1,590**

**降幅约 76%**，而且没动检索内容、没掉效果。最大头来自 caching（干掉了 44% 占比的稳定前缀）和历史压缩。这就是「先量后砍」的威力。

---

## 7. 常见坑

| 坑 | 后果 | 修法 |
|----|------|------|
| 把动态内容放进缓存前缀 | 缓存永不命中，反而多付 1.25× 写入费 | 动态内容挪到 messages 尾部 |
| 为省钱把检索 k 砍到 1 | 漏召回 → 幻觉，省的钱赔在错误上 | k 用召回评测定，不是拍脑袋 |
| 历史无脑全摘要 | 关键约束被摘掉，前后矛盾 | 保留近 N 轮原文 + 远期摘要 |
| 用旗舰模型干路由/抽取 | 简单活付旗舰价 | 拆给小模型 |
| 实时接口忘了 batch 选项 | 离线任务付实时全价 | 非实时任务走 batch（5 折） |

---

## 下一步

- 📖 没有占比表就没法优化，先做可观测 → [01-observability.md](./01-observability.md)
- 📖 压历史压过头导致前后矛盾，怎么查 → [03-debugging.md](./03-debugging.md)
- 📖 减 k 之前先用召回评测定安全下限 → [04-evaluation.md](./04-evaluation.md)
- 📖 成本与延迟的基础模型 → [../01-foundations/04-cost-latency.md](../01-foundations/04-cost-latency.md)
- 📖 历史压缩/摘要的具体策略 → [../05-compaction/02-summarization.md](../05-compaction/02-summarization.md)

## 参考资料

- Anthropic Prompt Caching：https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- Anthropic Message Batches：https://docs.anthropic.com/en/docs/build-with-claude/batch-processing
- OpenAI Prompt Caching：https://platform.openai.com/docs/guides/prompt-caching
