# 静态注入 vs 动态检索

> **一句话**：知识小而稳，就**静态拼进 system 一次（还能吃 prompt cache）**；知识大而散，就**每轮按 query 动态检索 top-k**——选错了不是性能问题就是钱包问题，大多数生产系统是两者混用。

---

## 1. 两种把知识放进窗口的方式

外部知识进上下文，本质只有两条路：

```
静态注入 (Static)                    动态检索 (Dynamic / RAG)
─────────────────                   ──────────────────────
固定文本拼进 system / 前缀            每轮按当前 query 检索 top-k
启动时确定，每轮一样                  运行时确定，每轮可能不同
              \                     /
               用同一个上下文窗口
```

- **静态注入**：把一段固定知识（产品手册摘要、API 规范、风格指南、几条 FAQ）直接写进 system prompt 或固定前缀。每一轮都带着它，内容不随 query 变。
- **动态检索**：不预先放知识，每轮拿用户 query 去检索系统捞 top-k，把捞回来的那几段拼进当前轮。内容随 query 变化。

---

## 2. 静态注入：适合小而稳的知识

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
"""  # 全文不到 1K token，且很少变

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=512,
    system=[
        {"type": "text", "text": "你是客服助手，依据下方政策回答。"},
        {
            "type": "text",
            "text": COMPANY_KB,
            "cache_control": {"type": "ephemeral"},  # 命中缓存，几乎不花钱
        },
    ],
    messages=[{"role": "user", "content": "定制的杯子能退吗？"}],
)
```

静态注入的**杀手锏是 prompt caching**：固定前缀只在首轮算全价，后续命中缓存，输入 token 成本骤降（Claude 缓存命中约为原价 1/10）。详见本手册「production」章的缓存篇。

适用前提（三个都要满足）：

- **小**：能塞进几 K token，不挤占窗口。
- **稳**：很少变，不至于每天改前缀打破缓存。
- **全程相关**：每轮对话都用得上，而不是偶尔需要。

---

## 3. 动态检索：适合大而散的知识库

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

- **大**：知识库几百上千篇文档，根本塞不进窗口。
- **散**：每个 query 只用得上其中一小撮，且哪一撮取决于 query。
- **会变**：文档常更新，检索能拿到最新版本，不用改 prompt。

代价：每轮多一次检索延迟、召回质量直接决定答案质量、且**动态部分破坏 prompt cache**（每轮注入内容不同，前缀缓存只能覆盖到检索块之前）。

---

## 4. 混用：固定前缀 + 动态尾部（生产标配）

现实系统几乎都是两者结合——**把稳定的放静态前缀（吃缓存），把易变的放动态尾部**：

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

关键是**顺序**：可缓存的静态内容放最前面，动态检索内容放后面。一旦把动态内容插到前缀中间，后面所有 token 的缓存全部失效。详见缓存篇对「缓存前缀」的解释。

---

## 5. 决策表：什么时候选哪个

| 维度 | 静态注入 | 动态检索 |
|------|----------|----------|
| 知识量 | 小（几 K token 内） | 大（放不进窗口） |
| 变化频率 | 低（很少改） | 高（频繁更新） |
| 每轮相关性 | 全程都用得上 | 因 query 而异 |
| 延迟 | 零额外延迟 | 多一次检索往返 |
| 成本 | 可吃 prompt cache，极省 | 检索成本 + 动态部分不缓存 |
| 召回质量依赖 | 无（人工选定） | 强依赖检索质量 |
| 典型例子 | 风格指南、API 规范、核心 FAQ | 文档库 QA、客服知识库、代码库检索 |

速记决策：

```
知识能塞下、几乎不变、每轮都用 ──→ 静态注入（+ 缓存）
知识塞不下、经常变、按需用     ──→ 动态检索
两者都有                       ──→ 混用：稳的进前缀，散的动态拼
```

- **不要把大知识库硬塞静态**：贵、慢、context rot（第 1 篇）。
- **不要把全程必用的小指令做成动态检索**：白白多一次检索、还可能漏召回。
- **拿不准就先静态**：实现最简单、能吃缓存；等知识量涨到塞不下、或更新太频繁，再迁到动态检索。

---

## 下一步

- [03-just-in-time.md](./03-just-in-time.md)：第三条路——不预检索，让模型用工具按需查
- [04-rank-trim.md](./04-rank-trim.md)：动态检索回来一堆 chunk，进窗口前怎么排序裁剪
- 跨章：[../07-long-context/03-prompt-caching.md](../07-long-context/03-prompt-caching.md) prompt 缓存的工程细节
