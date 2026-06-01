# RAG 作为上下文来源

> **一句话**：从上下文工程的视角看，RAG 不是"问答系统"，而是**在生成前动态拼装上下文窗口的一种手段**——检索把"模型需要但不在权重里的知识"按需注入，核心问题永远是「哪些 token 值得占用这块窗口」。

---

## 1. 换个视角：RAG 是 context 的供给侧

大多数 RAG 教程从「召回率 / 向量库 / chunk 切分」讲起，那是检索系统视角。本手册讲的是**上下文工程视角**：你的窗口就那么大（哪怕 200K），每一个 token 都在花钱、都在稀释注意力。RAG 的职责是回答一个问题——

> 这一轮生成，窗口里应该放哪些外部知识？

```
       上下文窗口 = [ system | 工具定义 | 历史 | 【检索注入】 | 当前 query ]
                                              ↑
                            RAG 负责动态填充这一段
```

embedding、向量库、混合检索这些**底层机制**，第 6 本《Embedding & 向量检索》手册已经讲透（向量 vs 关键词、BM25 融合、HyDE、重排 pipeline 等，详见 Embedding 手册的检索章节）。本章不重复，只聚焦：**检索回来的内容，怎么进窗口、进多少、放哪里、怎么标引用**。

---

## 2. 为什么不把全部知识塞进窗口

200K / 1M 窗口的时代，总有人问："反正放得下，为啥不把整个知识库灌进去？" 三个硬约束：

| 约束 | 说明 | 后果 |
|------|------|------|
| **成本** | input token 按量计费，10 万 token 的固定前缀每轮都烧钱 | 1M 上下文每次调用可能几美元，规模化即破产 |
| **Context Rot（上下文腐烂）** | 窗口越满，模型对中间内容的注意力越差（lost-in-the-middle） | 塞 50 篇文档，关键那篇被淹没，准确率反而下降 |
| **知识更新** | 权重 / 静态前缀里的知识是"快照"，过期了改不动 | 检索可以连实时数据源，全量灌入做不到增量更新 |

```
# ❌ 反模式：把整个知识库拼进 system
system = "以下是公司全部 800 篇文档：\n" + "\n".join(all_docs)  # 60 万 token
# 贵、慢、还因为 context rot 找不到重点

# ✅ 正解：按 query 检索 top-k，只注入相关的几段
chunks = retrieve(query, k=5)        # 3000 token
context = "\n\n".join(c.text for c in chunks)
```

经验法则：**能放下 ≠ 应该放**。窗口是预算，不是仓库。详见本手册「长上下文」章对 context rot 的实测。

---

## 3. 检索注入的基本流程

最朴素的「检索 → 拼接 → 生成」长这样，五个步骤：

```python
from anthropic import Anthropic

client = Anthropic()

def rag_answer(query: str, retriever, k: int = 5) -> str:
    # 1. 检索：拿回 top-k 候选（机制见 Embedding 手册）
    chunks = retriever.search(query, k=k)

    # 2. 拼装：把 chunk 格式化进上下文，带来源编号
    context_block = "\n\n".join(
        f"[文档 {i+1}] (来源: {c.source})\n{c.text}"
        for i, c in enumerate(chunks)
    )

    # 3. 构造 prompt：把检索内容和指令分区
    system = (
        "你是知识库问答助手。只依据 <context> 中的内容回答；"
        "若 context 没有答案，明确说『资料中未提及』，不要编造。"
    )
    user = f"<context>\n{context_block}\n</context>\n\n问题：{query}"

    # 4. 生成
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    # 5. 返回（生产中还要做引用归因，见本章第 5 篇）
    return resp.content[0].text
```

几个上下文工程层面的注意点：

- **分区清晰**：用 `<context>` 标签或 `[文档 N]` 把检索内容和指令、query 隔开，模型才知道"哪些是参考资料、哪些是要执行的指令"。
- **来源随行**：每个 chunk 带 `source` / 编号，为后续引用归因（第 5 篇）和调试留钩子。
- **指令 grounding**：system 明确要求"只依据 context"，对抗模型脱离资料胡编。

---

## 4. k 选多大：召回与噪声的权衡

`k`（注入几条 chunk）是 RAG 里最被低估的旋钮。它不是越大越好：

```
k 太小 → 漏掉答案所在的 chunk（召回不足）
k 太大 → 噪声 chunk 稀释注意力 + 烧 token + context rot
```

| k 值 | 适用场景 | 风险 |
|------|----------|------|
| 1~3 | 事实问答、答案集中在单点 | 召回不足，问题稍复杂就漏 |
| 5~8 | 通用 QA、客服（最常用区间） | 平衡点，多数场景默认从这里起 |
| 15~30 | 综述类、需要跨多文档归纳 | 必须配重排，否则噪声压垮信号 |
| 50+ | 几乎总是错的（除非给 reranker 当召回池） | context rot 严重，钱包痛 |

实战策略不是拍一个固定 k，而是**两阶段 + 预算驱动**：

```python
# ✅ 推荐：召回放宽，重排收紧，再按 token 预算裁剪
candidates = retriever.search(query, k=50)        # 召回宽，宁多勿漏
ranked = reranker.rerank(query, candidates)       # 精排（机制见 Embedding 手册）
final = pack_to_budget(ranked, max_tokens=4000)   # 按预算贪心填充（见本章第 4 篇）
```

要点：

- **召回阶段 k 大、注入阶段 k 小**。召回宁可多召（top-50），靠 reranker 把真正相关的提到前面，最后只注入能放进 token 预算的那几条。
- **k 应随 query 复杂度动态调整**：单点事实问答 k=3 够了；"对比 A 和 B 两个方案"这类需要多文档，k 调大并配重排。
- **盯住 token 预算而非固定条数**：chunk 长度不一，按 token 填充比"固定 5 条"更可控（第 4 篇详述）。

---

## 5. 一个常见误区：RAG ≠ 一次性检索

很多人以为 RAG 就是"检索一次、拼进去、生成完"。这只是**静态/单轮 RAG**。现实里还有：

- **动态检索**：每轮对话按当前 query 重新检索（第 2 篇）。
- **Just-in-time / agentic 检索**：不预检索，把搜索做成工具，让模型自己决定查什么、查几次（第 3 篇）——这是 2025-2026 Claude / GPT agent 的主流方向。

RAG 在上下文工程里的定位，是「上下文的动态供给机制」这一整类方法，单轮拼接只是其中最简单的一种。

---

## 下一步

- [02-static-vs-dynamic.md](./02-static-vs-dynamic.md)：静态注入 vs 动态检索，什么时候选哪个
- [03-just-in-time.md](./03-just-in-time.md)：把检索做成工具，让模型按需查
- [04-rank-trim.md](./04-rank-trim.md)：检索结果进窗口前的排序、裁剪、去重
- 跨章：[../01-foundations/05-context-budget.md](../01-foundations/05-context-budget.md) 回顾窗口预算分配
