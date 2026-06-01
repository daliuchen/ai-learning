# CE 07-01：1M token 时代——长上下文模型现状

> **一句话**：到 2025-2026，主流模型的上下文窗口已经从几年前的几千 token 飙到 128K～2M，「把整本书塞进去」从科幻变成日常。但窗口大是一回事，用得好是另一回事——长窗口既不免费，也不无损，能塞进去 ≠ 模型真的读进去了。

---

## 1. 现状盘点：各家窗口有多大

先把账摆清楚。截至 2026 年初，主流模型的上下文窗口大致是这个量级：

| 模型家族 | 标准窗口 | 扩展窗口 | 备注 |
|----------|----------|----------|------|
| Claude Opus / Sonnet 4.x | 200K | 1M（beta，需 header） | 1M 档输入超 200K 部分加价 |
| Claude Haiku | 200K | — | 小模型也给到 200K |
| GPT-4o | 128K | — | 输出上限 16K |
| GPT-4.1 系 | 1M | — | OpenAI 把长上下文做进了主线 |
| GPT-5 系 | 128K～400K（按档位） | — | 不同档位窗口不同 |
| Gemini 1.5 Pro | 1M | 2M | 最早把 1M 商用化的 |
| Gemini 2.x Pro / Flash | 1M | 部分 2M | Flash 也给 1M |

几个观察：

- **1M 已经是「大模型」标配**，不再是某一家的独门绝技。Gemini 先发、GPT-4.1 跟上、Claude 用 1M beta 补齐。
- **2M 仍是 Gemini 的领先区**，但实际用满 2M 的场景极少（成本和延迟劝退）。
- **窗口大小要看「档位 / beta 开关」**。Claude 的 1M 不是默认开的，要带 `context-1m-2025-08-07` 这类 beta header，且计费规则不同。

> ⚠️ 窗口数字变化很快，写代码时**别 hardcode**「这个模型就是 200K」。以官方 model card / API 文档为准，并把窗口大小做成可配置项。

---

## 2. 长上下文是怎么实现的（点到为止）

为什么几年前模型只能吃 2K、4K，现在能吃 1M？不是简单地「把 max_length 调大」就行，背后有几块工程。

### 2.1 位置编码外推：RoPE 及其扩展

Transformer 本身不知道 token 的先后，靠 **位置编码** 注入位置信息。现在主流用 **RoPE（旋转位置编码）**——把位置信息编码成旋转角度。

RoPE 的好处是**有一定外推性**：训练时见过 4K，理论上能往更长推。但直接外推效果会崩，于是有一系列技巧把它「拉长」：

- **位置插值（Position Interpolation）**：把超出训练长度的位置「压缩」回训练见过的范围，相当于把刻度尺拉长。
- **NTK-aware scaling / YaRN**：按频率分段调整 RoPE 的基底，让高频低频各自合理外推，是把 4K/8K 模型扩到 128K+ 的常用路线。

这部分你不用自己实现，但要知道：**长窗口是「训练 + 外推技巧」共同换来的，不是天生的**，所以越靠近窗口上限，质量越可能下降。

### 2.2 注意力的计算优化

self-attention 是 **O(n²)**：序列翻倍，计算量翻 4 倍。1M token 的朴素注意力根本算不动。所以长上下文模型都依赖注意力侧的工程优化：

- **FlashAttention**：不改变数学结果，靠分块计算 + 减少显存读写，把 attention 算得又快又省显存。是长上下文能跑起来的基础设施。
- **稀疏 / 分组注意力**：不是每个 token 都和所有 token 算注意力，而是只算一部分（局部窗口、跨步、全局少数 token），把 O(n²) 降下来。
- **GQA（Grouped-Query Attention）**：多个 query 头共享 KV，显著压缩 KV cache 体积——这对长上下文的显存占用至关重要。

一句话：**RoPE 外推解决「位置编得对不对」，注意力优化解决「算得动算不动」**，两者凑齐才有今天的 1M。

---

## 3. 长窗口不等于免费

这是工程上最该清醒的一点。窗口能塞 1M，不代表你应该塞 1M。

### 3.1 成本：输入 token 是真金白银

输入 token 按量计费。把 500K token 塞进去，每一次调用都付 500K 的输入费。多轮对话 / Agent 循环里，这笔钱会**线性累加甚至爆炸**：

```python
# ❌ 每轮都把整个长文档重新塞进去
for question in questions:          # 10 个问题
    messages = [
        {"role": "user", "content": full_doc + "\n\n问题：" + question},
        # full_doc ≈ 400K token，每轮都付一遍 → 总共 4M input token
    ]
    client.messages.create(model="claude-opus-4-20250514", messages=messages, max_tokens=1000)
```

400K × 10 = 4M 输入 token，这账单很可观。解法是 **prompt caching**（见 [03-prompt-caching.md](./03-prompt-caching.md)），把稳定的长文档缓存住，命中后输入费打到一两折。

### 3.2 延迟：prefill 要时间

模型在生成第一个输出 token 之前，要先「读完」整个输入——这一步叫 **prefill**。输入越长，prefill 越久，**首 token 延迟（TTFT）** 越高。

400K token 的 prefill 可能要好几秒甚至十几秒。对话式 / 实时场景里，用户盯着空白屏幕等十几秒是灾难。

### 3.3 Context Rot：长了就「烂」

最隐蔽的成本。即使模型号称 1M，**实际有效注意力随长度增长而衰减**：

- **Lost-in-the-middle**：埋在长上下文中间的关键信息，模型经常「看不见」，开头和结尾的内容获得更稳定的注意力。
- **整体稀释**：序列越长，每个 token 分到的注意力份额被摊薄，关键信号被噪声淹没。

```
有效信息利用率（经验性示意）
高 │■                                ■
   │ ■■                           ■■
   │   ■■                      ■■
低 │     ■■■■■■■■■■■■■■■■■■■■
   └────────────────────────────────
    开头         中间           结尾
```

这就是为什么「benchmark 上 1M 大海捞针（needle-in-haystack）满分」和「真实任务里长上下文掉点」可以同时成立——大海捞针是单针检索，真实任务要综合理解、多跳推理，难度高得多。

---

## 4. 能塞进去 ≠ 用得好

把上面三点合起来，得到长上下文工程的核心原则：

> **窗口是上限，不是目标。** 你要装的是「足够且相关」的上下文，不是「能装多少装多少」。

实操上的几条经验：

- **先想能不能不塞**。检索出最相关的 50K，往往比硬塞 500K 又快又准又便宜（长上下文 vs RAG 的取舍见 [02-long-context-vs-rag.md](./02-long-context-vs-rag.md)）。
- **关键信息放头尾**，对抗 lost-in-the-middle（实操见 [04-attention-optimization.md](./04-attention-optimization.md)）。
- **稳定内容做成可缓存前缀**，省钱省延迟（见 [03-prompt-caching.md](./03-prompt-caching.md)）。
- **窗口用量做监控**。别等线上账单爆了才发现每次都在塞 800K。

---

## 5. 常见坑

| 坑 | 说明 |
|----|------|
| hardcode 窗口大小 | 各家窗口频繁变、按档位变，做成可配置 |
| 把 1M beta 当默认 | Claude 1M 要 beta header 且计费不同，没开就报错或截断 |
| 拿大海捞针成绩当真实表现 | 单针检索 ≠ 综合长文理解，真实任务会掉点 |
| 无脑塞满窗口 | 成本线性涨、延迟飙、还触发 context rot |
| 忘了 prefill 延迟 | 长输入首 token 等很久，实时场景体验差 |
| 多轮重复塞长文档 | 不用缓存的话每轮都全价付一遍 |

---

## 6. 下一步

- 📖 既然能全塞了，还要不要 RAG → [02-long-context-vs-rag.md](./02-long-context-vs-rag.md)
- 📖 用 prompt caching 把长上下文成本打下来 → [03-prompt-caching.md](./03-prompt-caching.md)
- 📖 对抗 lost-in-the-middle 的实操 → [04-attention-optimization.md](./04-attention-optimization.md)
- 📖 Context Rot 的机制细讲 → [../01-foundations/03-context-rot.md](../01-foundations/03-context-rot.md)
- 📖 上线后怎么监控窗口用量 → [../08-production/01-observability.md](../08-production/01-observability.md)

## 参考资料

- Anthropic 1M context: https://docs.anthropic.com/en/docs/build-with-claude/context-windows
- Gemini long context: https://ai.google.dev/gemini-api/docs/long-context
- Su et al., "RoFormer: Enhanced Transformer with Rotary Position Embedding": https://arxiv.org/abs/2104.09864
- Dao et al., "FlashAttention": https://arxiv.org/abs/2205.14135
- Liu et al., "Lost in the Middle": https://arxiv.org/abs/2307.03172
