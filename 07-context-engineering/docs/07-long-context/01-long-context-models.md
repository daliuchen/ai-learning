# CE 07-01：长上下文模型现状

> 到 2026 年，主流旗舰模型的上下文窗口已经从几年前的几千 token 涨到 1M，把一整本书塞进一次请求成了日常操作。不过窗口大和用得好是两回事。长窗口既不免费也不无损，塞得进去，不等于模型真的读进去了。

---

## 1. 现状盘点：各家窗口有多大

截至 2026 年 11 月，按各家官方文档，主流旗舰的上下文窗口都在 1M 量级：

| 模型家族 | 窗口 | 最大输出 | 备注（均来自官方文档） |
|----------|------|----------|------------------------|
| Claude Fable 5.1 / Opus 5.5 / Sonnet 5.5 / Haiku 5.5 | 1M | 128K | 1M 是默认，不需要 beta header；除 Haiku 5.5 外，长上下文按标准价计费；Haiku 5.5 提示超过 100K token 时单价更高（$0.10 → $0.50 / 1M input） |
| Claude 4.5 及更早（如 Sonnet 4.5，已 deprecated） | 200K | — | 窗口较小，不再是主力 |
| OpenAI GPT-6 系（Astra / Sol / Terra / Luna） | 1.05M | 128K | 四个档位窗口相同，差别在能力与价格 |
| Gemini 4 Argon | 1M（输入、思考、输出共享） | 最高可 1M | Gemini 3.x 及 2.5 是 1M 输入 / 64K 输出，且不共享 |
| 千问（qwen3.8-max / qwen3.7-plus / qwen3.8-flash 等） | 以百炼各模型详情页为准 | — | 本手册不写具体数字（官方列表页没有列出，详情页随版本变） |

从这张表能读出几件事：

- 1M 已经是主力模型的标配，不再是哪一家独有。以前「2M 只有 Gemini 有」的说法已经过时。
- 窗口大小要按具体模型和档位查。Anthropic 的 Models API 能直接返回 `max_input_tokens`；老模型（如 Sonnet 4.5）还是 200K，同一个家族里窗口也不一定一样。
- 价格可能按长度分档（Haiku 5.5），也可能随时间调整（Gemini 的定价页就公布了涨价日程），估算长上下文成本要看当前的定价页。
- 1M token 折合多少文字也在变。Anthropic 文档说明，新 tokenizer（Opus 4.7 起）下 1M token 约合 55.5 万英文词，之前的模型约 75 万词。同一份文档放到新模型上，会占用更多 token。

> 注意：窗口数字变化很快，写代码时别把「这个模型就是 200K」写死。以官方 model card 和 API 文档为准，把窗口大小做成配置项，或者用 Models API 动态查询。

---

## 2. 长上下文是怎么实现的（点到为止）

几年前模型只能吃 2K、4K，现在能吃 1M，并不是把 max_length 调大这么简单，背后有几块工程。

### 2.1 位置编码外推：RoPE 及其扩展

Transformer 本身不知道 token 的先后顺序，要靠位置编码注入位置信息。现在主流用的是 RoPE（旋转位置编码），把位置信息编码成旋转角度。

RoPE 有一定的外推性，训练时见过 4K，理论上可以往更长推。但直接外推效果会崩，于是有一系列把它拉长的技巧：

- 位置插值（Position Interpolation）：把超出训练长度的位置压缩回训练时见过的范围，相当于把刻度尺拉长。
- NTK-aware scaling / YaRN：按频率分段调整 RoPE 的基底，让高频和低频各自合理外推。把 4K/8K 模型扩到 128K 以上，常走这条路。

这部分不用自己实现，但要知道长窗口是训练加外推技巧一起换来的，所以越靠近窗口上限，质量越可能下降。

### 2.2 注意力的计算优化

self-attention 的复杂度是 O(n²)，序列长度翻倍，计算量翻 4 倍。1M token 的朴素注意力根本算不动，所以长上下文模型都依赖注意力这一侧的工程优化：

- FlashAttention：数学结果不变，靠分块计算和减少显存读写，让 attention 又快又省显存，是长上下文能跑起来的基础设施。
- 稀疏或分组注意力：不让每个 token 都和所有 token 算注意力，只算一部分（局部窗口、跨步、少数全局 token），把 O(n²) 降下来。
- GQA（Grouped-Query Attention）：多个 query 头共享 KV，大幅压缩 KV cache 的体积，对长上下文的显存占用影响很大。

RoPE 外推管的是位置编得对不对，注意力优化管的是算不算得动，两样都有了才有今天的 1M。

---

## 3. 长窗口不等于免费

窗口能塞 1M，不代表应该塞 1M。这一点在工程上最需要想清楚。

### 3.1 成本：输入 token 都要付钱

输入 token 按量计费。塞进去 500K token，每次调用就付 500K 的输入费。到了多轮对话或 Agent 循环里，这笔钱会线性累加，甚至失控：

```python
# ❌ 每轮都把整个长文档重新塞进去
for question in questions:          # 10 个问题
    messages = [
        {"role": "user", "content": full_doc + "\n\n问题：" + question},
        # full_doc ≈ 400K token，每轮都付一遍 → 总共 4M input token
    ]
    client.messages.create(model="claude-opus-5-5", messages=messages, max_tokens=1000)
```

400K × 10 = 4M 输入 token，账单相当可观。解法是 prompt caching（见 [Prompt Caching 与上下文复用](./03-prompt-caching.md)），把稳定的长文档缓存住，命中后输入费能降到一折甚至更低。

### 3.2 延迟：prefill 要时间

模型在生成第一个输出 token 之前，要先把整个输入读完，这一步叫 prefill。输入越长，prefill 越久，首 token 延迟（TTFT）就越高。

400K token 的 prefill 可能要好几秒甚至十几秒。在对话或实时场景里，让用户对着空白屏幕等十几秒，体验很糟。

### 3.3 Context Rot：长了就「烂」

这是最不容易察觉的成本。即使模型号称 1M，实际有效的注意力也会随长度增长而衰减：

- Lost-in-the-middle：埋在长上下文中间的关键信息，模型经常看不到，开头和结尾的内容拿到的注意力更稳定。
- 整体稀释：序列越长，每个 token 分到的注意力越少，关键信号被噪声淹没。

```
有效信息利用率（经验性示意）
高 │■                                ■
   │ ■■                           ■■
   │   ■■                      ■■
低 │     ■■■■■■■■■■■■■■■■■■■■
   └────────────────────────────────
    开头         中间           结尾
```

所以 benchmark 上 1M 大海捞针（needle-in-haystack）拿满分，和真实任务里长上下文掉点，两件事可以同时成立。大海捞针是单针检索，真实任务要综合理解、多跳推理，难度高得多。

---

## 4. 能塞进去 ≠ 用得好

上面三点合在一起，就是长上下文工程的基本原则：

> 要装的是足够且相关的上下文，1M 只是上限，不是要填满的目标。

落到实操上有几条经验：

- 先想能不能不塞。检索出最相关的 50K，往往比硬塞 500K 更快、更准、更便宜（长上下文和 RAG 怎么取舍，见[长上下文 vs RAG](./02-long-context-vs-rag.md)）。
- 关键信息放在头尾，对抗 lost-in-the-middle（实操见[长上下文的注意力优化实操](./04-attention-optimization.md)）。
- 稳定的内容做成可缓存的前缀，省钱也省延迟（见 [Prompt Caching 与上下文复用](./03-prompt-caching.md)）。
- 监控窗口用量。别等线上账单涨上去了，才发现每次请求都塞了 800K。

---

## 5. 常见坑

| 坑 | 说明 |
|----|------|
| 把窗口大小写死 | 各家窗口经常变、按档位不同，做成可配置 |
| 把旧模型的 200K 当成现在的窗口 | Claude 5.x / GPT-6 / Gemini 4 都是 1M 量级，但旧型号还是 200K 或 128K，按模型查；计费也可能分档（如 Haiku 5.5 超 100K） |
| 拿大海捞针成绩当真实表现 | 单针检索 ≠ 综合长文理解，真实任务会掉点 |
| 不加筛选塞满窗口 | 成本线性涨、延迟变高，还会触发 context rot |
| 忘了 prefill 延迟 | 长输入的首 token 要等很久，实时场景体验差 |
| 多轮重复塞长文档 | 不用缓存的话，每轮都按全价付一遍 |

---

## 6. 下一步

- 既然能全塞，还要不要 RAG：[有了长上下文还要不要 RAG](./02-long-context-vs-rag.md)
- 用缓存把长上下文的成本降下来：[Prompt Caching 与上下文复用](./03-prompt-caching.md)
- 对抗 lost-in-the-middle 的实操：[长上下文的注意力优化实操](./04-attention-optimization.md)
- Context Rot 的机制：[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)
- 上线后怎么监控窗口用量：[上下文可观测性](../08-production/01-observability.md)

## 参考资料

- Anthropic Context windows: https://platform.claude.com/docs/en/build-with-claude/context-windows
- Anthropic Models overview: https://platform.claude.com/docs/en/about-claude/models/overview
- OpenAI Models: https://developers.openai.com/api/docs/models
- Gemini long context: https://ai.google.dev/gemini-api/docs/long-context
- Su et al., "RoFormer: Enhanced Transformer with Rotary Position Embedding": https://arxiv.org/abs/2104.09864
- Dao et al., "FlashAttention": https://arxiv.org/abs/2205.14135
- Liu et al., "Lost in the Middle": https://arxiv.org/abs/2307.03172
