# 静态注入 vs 动态检索

> 知识少而且稳定，就静态拼进 system，只写一次，还能吃到 prompt cache；知识多而且分散，就每一轮按 query 动态检索 top-k。选错了，要么效果差，要么花冤枉钱。大多数生产系统是两种方式混着用，这篇讲怎么判断和怎么组合。

---

## 1. 把知识放进窗口的两种方式

外部知识进上下文，只有两条路：

```
静态注入 (Static)                    动态检索 (Dynamic / RAG)
─────────────────                   ──────────────────────
固定文本拼进 system / 前缀            每轮按当前 query 检索 top-k
启动时确定，每轮一样                  运行时确定，每轮可能不同
              \                     /
               用同一个上下文窗口
```

静态注入是把一段固定的知识（产品手册摘要、API 规范、风格指南、几条 FAQ）直接写进 system prompt 或固定前缀，每一轮都带着它，内容不随 query 变化。动态检索则不预先放知识，每一轮拿用户的 query 去检索系统捞 top-k，把捞回来的几段拼进当前这一轮，内容随 query 变化。

---

## 2. 静态注入：适合少而稳的知识

```python
# ✅ 静态：知识固定，写死在 system，并标记为可缓存
from anthropic import Anthropic

client = Anthropic()

COMPANY_KB = """
# 退款政策（截至 2026-01）
- 7 天无理由退款，订单金额原路退回
- 定制商品不支持退款
# 配送
- 默认 3-5 个工作日，偏远地区 7 天
"""  # 示例只列了几行；真实的政策块通常几 K token，且很少变

resp = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=2048,
    system=[
        {"type": "text", "text": "你是客服助手，依据下方政策回答。"},
        {
            "type": "text",
            "text": COMPANY_KB,
            "cache_control": {"type": "ephemeral"},  # 前缀不足最小缓存长度（5.x 为 512 token）时不会缓存
        },
    ],
    messages=[{"role": "user", "content": "定制的杯子能退吗？"}],
)
```

静态注入最大的好处是能用上 prompt caching：固定前缀只在第一轮按全价算，之后命中缓存，输入 token 的成本大幅下降（Claude 缓存读默认是基础输入价的 0.1×，Sonnet 5.5 是 0.05×）。缓存的细节见 [Prompt Caching 与上下文复用](../07-long-context/03-prompt-caching.md)。

用静态注入要同时满足三个前提：

- 量小：几 K token 就能放下，不挤占窗口。
- 稳定：很少改动，不会天天改前缀导致缓存失效。
- 全程相关：每一轮对话都用得上，而不是偶尔才用。

---

## 3. 动态检索：适合大而分散的知识库

```python
# ✅ 动态：知识库太大放不下，每轮按 query 捞 top-k
def build_messages(query: str, history: list, retriever) -> list:
    chunks = retriever.search(query, k=5)   # 随 query 变化（机制见 Embedding 手册）
    context = "\n\n".join(
        f"[文档 {i+1}] {c.text}" for i, c in enumerate(chunks)
    )
    return history + [{
        "role": "user",
        "content": f"<context>\n{context}\n</context>\n\n问题：{query}",
    }]
```

适用场景：

- 量大：知识库有几百上千篇文档，窗口根本放不下。
- 分散：每个 query 只用得上其中一小部分，具体是哪部分取决于 query。
- 会变：文档经常更新，检索能拿到最新版本，不用改 prompt。

代价有三个：每一轮多一次检索的延迟；召回质量直接决定答案质量；动态部分会破坏 prompt cache，因为每轮注入的内容不同，前缀缓存只能覆盖到检索块之前。

---

## 4. 混用：固定前缀加动态尾部

实际系统几乎都把两者结合起来，稳定的放在静态前缀里吃缓存，易变的放在动态尾部：

```python
# ✅ 混合：稳定知识静态化吃缓存，长尾知识动态检索
system = [
    {"type": "text", "text": SYSTEM_INSTRUCTIONS},
    {"type": "text", "text": CORE_POLICY,        # 小而稳 → 静态
     "cache_control": {"type": "ephemeral"}},
]
# 检索块放在 messages 里（动态，不进前缀缓存）
messages = build_messages(query, history, retriever)
```

顺序很重要：可缓存的静态内容放在最前面，动态检索的内容放在后面。一旦把动态内容插进前缀中间，它后面所有 token 的缓存都会失效。缓存前缀的规则，缓存篇里有详细说明。

---

## 5. 决策表：什么时候选哪个

| 维度 | 静态注入 | 动态检索 |
|------|----------|----------|
| 知识量 | 小（几 K token 以内） | 大（窗口放不下） |
| 变化频率 | 低，很少改 | 高，经常更新 |
| 每轮相关性 | 全程都用得上 | 因 query 而异 |
| 延迟 | 没有额外延迟 | 多一次检索往返 |
| 成本 | 能吃 prompt cache，非常省 | 检索成本，加上动态部分不能缓存 |
| 对召回质量的依赖 | 无，人工选定 | 强依赖检索质量 |
| 常见例子 | 风格指南、API 规范、核心 FAQ | 文档库问答、客服知识库、代码库检索 |

速记：

```
知识能塞下、几乎不变、每轮都用 ──→ 静态注入（+ 缓存）
知识塞不下、经常变、按需用     ──→ 动态检索
两者都有                       ──→ 混用：稳的进前缀，散的动态拼
```

两个反方向的错误都要避免。不要把大知识库硬塞成静态的，又贵又慢，还有 context rot（见第 1 篇）；也不要把每轮都要用的小段指令做成动态检索，白白多一次检索，还可能漏召回。拿不准的话，我建议先用静态：实现最简单，能吃缓存，等知识量涨到放不下或者更新太频繁，再迁到动态检索。

---

## 下一步

- [Just-in-time 检索 / Agentic Search](./03-just-in-time.md)：第三条路，不预先检索，让模型用工具按需查
- [检索结果的排序、裁剪、去重](./04-rank-trim.md)：动态检索回来一堆 chunk，进窗口前怎么排序和裁剪
- 跨章，prompt 缓存的工程细节：[Prompt Caching 与上下文复用](../07-long-context/03-prompt-caching.md)
