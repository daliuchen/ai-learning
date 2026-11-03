# CE 08-02：上下文成本优化实战

> 上下文成本等于每次调用的 token 量乘以单价，再乘以调用次数。优化要先量再砍：用上一篇的 token 占比表找出最大的那块，对症处理，不要一上来就压缩历史。在 Agent 场景里，花钱最多的常常是工具定义和历史，而工具定义恰好最适合交给 prompt caching。

---

## 1. 先搞清楚账单怎么算：input / output / cache 三笔钱

2026 年主流的计费分成几档，单价差一个数量级，对应的优化策略也完全不同。下表的倍数以 Anthropic 官方定价页为准，其他家略有差异，见 [Prompt Caching 与上下文复用](../07-long-context/03-prompt-caching.md)：

| 计费项 | 相对单价（以 input 为 1×） | 含义 | 优化抓手 |
|--------|---------------------------|------|----------|
| input（fresh） | 1× | 首次进入、未命中缓存的输入 token | 压缩、裁剪、减少检索 |
| cache write | 1.25×（5 分钟）/ 2×（1 小时） | 写入 prefix 缓存（比普通 input 贵） | 一次写、多次读才划算 |
| cache read（hit） | 0.1×；Opus 5.5 / Sonnet 5.5 为 0.05×；Fable 5.1 为 0.025× | 命中缓存的输入 token | 收益最大：让稳定前缀全部命中 |
| output | 当前主力模型多为 5× input | 模型生成的 token | 限制 `max_tokens`、要求结构化的简短输出 |

cache read 只要 input 单价的 1/10，新一代 Claude 甚至只要 5%。一个 6K token 的稳定系统前缀，命中后只相当于 600 token（甚至 300 token）的钱。所以先把前缀缓存做对，通常比任何压缩手段见效都快。

---

## 2. 降本手段清单（按性价比排序）

| 手段 | 砍的是什么 | 常见收益（量级示意） | 代价 / 风险 |
|------|-----------|----------|-------------|
| prompt caching | 稳定前缀（system + 工具定义 + 长指令）的重复计费 | 命中部分按 0.1× 或更低计费 | 前缀必须逐字节稳定 |
| 压缩、摘要历史 | 远期对话历史的原文 token | 长会话省 30~70% | 摘要可能丢掉关键约束 |
| 裁剪工具定义 | 冗长的 JSON schema、用不上的工具 | 每次省几 K token | 裁错了 Agent 调不到工具 |
| 减少检索 k | 召回过多的低相关片段 | 检索段 token 减半 | k 太小会漏召回，引发幻觉 |
| 小模型做预处理 | 用 Haiku/mini 做改写、路由、抽取 | 整体成本降低，主模型只做难的部分 | 多一跳，链路变复杂 |
| batch API | 异步、非实时的任务 | 价格直接打 5 折 | 不能实时返回（分钟级延迟） |

---

## 3. 先看占比，从最大的那块下手

别凭感觉。先跑上一篇[上下文可观测性](./01-observability.md)里的占比表，谁占得多先砍谁。

```
部分            tokens     占比
history          8120    44.8%   ← 最大头，且在变 → 压缩
tools            5980    33.0%   ← 次大头，但稳定 → 用 caching
system           2010    11.1%   ← 稳定 → 和 tools 一起缓存
retrieved        1450     8.0%   ← 占比小，别动它，动了反而漏召回
user              570     3.1%
```

判断方法按占比和是否变动来分：

- 占比大且稳定（tools、长 system）：上 prompt caching，几乎没有代价。
- 占比大且在变（history）：压缩、摘要或滑动窗口。
- 占比小（retrieved、user）：别折腾，省不了几个钱，还容易把效果搞坏。

```python
# ❌ 一上来就压检索片段 —— 它只占 8%，还压出了漏召回幻觉
retrieved = retrieved[:1]

# ✅ 先把稳定的工具定义（33%）和 system（11%）缓存掉
system = [{
    "type": "text",
    "text": SYSTEM_PROMPT + TOOLS_DESCRIPTION,   # 长且稳定的前缀
    "cache_control": {"type": "ephemeral"},       # 缓存断点打在这里
}]
```

---

## 4. prompt caching 实操（Anthropic 为例）

把稳定前缀和变动尾部分开，断点打在两者的边界上。前缀越长、复用越频繁，省得越多。

```python
import anthropic
client = anthropic.Anthropic()

resp = client.messages.create(
    model="claude-sonnet-5-5",
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

再提醒一次命中条件：**前缀要逐字节相同**。别把时间戳、随机 session id、用户名拼在 system 开头，把它们挪到 messages 里，让前缀保持不变。

---

## 5. 小模型做预处理：链路降本

不是每一步都需要旗舰模型。常见的拆法是这样：

```python
# 用便宜的小模型做「把用户原话改写成检索 query」这种简单活
cheap = client.messages.create(
    model="claude-haiku-5-5",   # 便宜、快
    max_tokens=2048,
    messages=[{"role": "user", "content": f"把下面问题改写成 3 个检索关键词：{user_q}"}],
)
queries = parse(cheap)

# 主模型只在「带着精准检索结果做最终推理」时上场
answer = client.messages.create(model="claude-sonnet-5-5", messages=build_ctx(queries), max_tokens=1024)
```

路由、意图分类、查询改写、结果抽取，这些都可以交给 Haiku 或 mini 级别的模型，旗舰模型只做最后那一步需要强推理的工作。

---

## 6. 优化前后对比案例

一个客服 Agent，日均 10 万次调用，平均每次 18K input token、800 output token。按 Claude Sonnet 5.5 的官方标价算：input \$2、output \$10（每百万 token），cache read 是 0.05×，即每百万 token \$0.10（以官方定价页为准，价格会变）。

优化前，每次都是 fresh input：
- input：18000 × \$2/M = \$0.036
- output：800 × \$10/M = \$0.008
- 单次约 \$0.044，日成本约 \$4,400

优化时做了三件事：
1. system 和工具定义（约 8K，占 44%）全部走 prompt caching，命中后按 0.05× 计费：8000 × \$0.1/M ≈ \$0.0008
2. 历史从 8K 压缩摘要到 2.5K，加上没动的检索（约 1.45K）和 user（约 0.57K），fresh 部分约 4.5K
3. output 限制为结构化的短回答，约 400 token

优化后：
- cached input：8000 × \$0.1/M = \$0.0008
- fresh input：4500 × \$2/M = \$0.009
- output：400 × \$10/M = \$0.004
- 单次约 \$0.0138，日成本约 \$1,380（没算缓存写入和未命中的部分，实际会略高）

成本降了约 69%，检索内容没动。省下的钱主要来自 caching（处理掉了占 44% 的稳定前缀）和历史压缩。

---

## 7. 常见坑

| 坑 | 后果 | 修法 |
|----|------|------|
| 把动态内容放进缓存前缀 | 缓存永远不命中，还多付 1.25× 的写入费 | 动态内容挪到 messages 尾部 |
| 为省钱把检索 k 砍到 1 | 漏召回引发幻觉，省下的钱都赔在错误上 | k 用召回评测来定，别拍脑袋 |
| 历史一律全部摘要 | 关键约束被摘掉，前后矛盾 | 保留最近 N 轮原文，远期的做摘要 |
| 用旗舰模型做路由、抽取 | 简单的活付了旗舰的价 | 拆给小模型 |
| 离线任务忘了 batch 选项 | 离线任务付了实时的全价 | 非实时任务走 batch（5 折） |

---

## 下一步

- 没有占比表就没法优化，先做可观测：[上下文可观测性](./01-observability.md)
- 历史压过头导致前后矛盾，怎么查：[上下文相关故障排查](./03-debugging.md)
- 减小 k 之前，先用召回评测定出安全下限：[如何评测上下文质量](./04-evaluation.md)
- 成本与延迟的基础模型：[上下文的成本与延迟模型](../01-foundations/04-cost-latency.md)
- 历史压缩和摘要的具体策略：[摘要式压缩](../05-compaction/02-summarization.md)

## 参考资料

- Anthropic Prompt Caching：https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Anthropic Message Batches：https://platform.claude.com/docs/en/build-with-claude/batch-processing
- OpenAI Prompt Caching：https://developers.openai.com/api/docs/guides/prompt-caching
