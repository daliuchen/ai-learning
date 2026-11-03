# RAG 作为上下文来源

> 从上下文工程的角度看，RAG 是在生成之前动态拼装上下文窗口的一种手段，而不只是一个问答系统。它把模型需要、但权重里没有的知识按需放进窗口，要回答的始终是同一个问题：哪些 token 值得占用这块窗口。这一章就从这个角度讲检索。

---

## 1. 换个角度：RAG 负责供给上下文

大多数 RAG 教程从召回率、向量库、chunk 切分讲起，那是检索系统的角度。这本手册换成上下文工程的角度：窗口就那么大（哪怕有 1M），每个 token 都在花钱，也都在稀释注意力。RAG 要回答的是：

> 这一轮生成，窗口里应该放哪些外部知识？

```
       上下文窗口 = [ system | 工具定义 | 历史 | 【检索注入】 | 当前 query ]
                                              ↑
                            RAG 负责动态填充这一段
```

embedding、向量库、混合检索这些底层机制，第 6 本《Embedding & 向量检索》手册已经讲过（向量和关键词检索的区别、BM25 融合、HyDE、重排流水线等，见那本手册的检索章节）。这一章不再重复，只讲检索回来的内容怎么进窗口、进多少、放在哪、怎么标引用。

---

## 2. 为什么不把全部知识塞进窗口

窗口到了 200K、1M，总有人问：反正放得下，为什么不把整个知识库灌进去？有三个硬约束：

| 约束 | 说明 | 后果 |
|------|------|------|
| 成本 | input token 按量计费，10 万 token 的固定前缀每一轮都要付钱 | 1M 上下文每次调用可能要几美元，规模一大就扛不住 |
| Context Rot（上下文腐烂） | 窗口越满，模型对中间内容的注意力越差（lost in the middle） | 塞 50 篇文档，关键的那篇被淹没，准确率反而下降 |
| 知识更新 | 权重和静态前缀里的知识是快照，过期了也改不了 | 检索可以接实时数据源，全量灌入做不到增量更新 |

```
# ❌ 反模式：把整个知识库拼进 system
system = "以下是公司全部 800 篇文档：\n" + "\n".join(all_docs)  # 60 万 token
# 贵、慢、还因为 context rot 找不到重点

# ✅ 正解：按 query 检索 top-k，只注入相关的几段
chunks = retrieve(query, k=5)        # 3000 token
context = "\n\n".join(c.text for c in chunks)
```

窗口放得下，不代表应该全放进去：5 段相关内容约 3000 token，整库是 60 万 token，差了两百倍，准确率还可能更低。[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md) 一篇讲了机理和示意数据。

---

## 3. 检索注入的基本流程

最朴素的「检索 → 拼接 → 生成」分五步：

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
        model="claude-sonnet-5-5",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    # 5. 返回（生产中还要做引用归因，见本章第 5 篇）
    return next(b.text for b in resp.content if b.type == "text")
```

从上下文工程的角度，这段代码里有三处要留意：

- 分区要清楚。用 `<context>` 标签或 `[文档 N]` 把检索内容和指令、query 隔开，模型才知道哪些是参考资料、哪些是要执行的指令。
- 来源要跟着走。每个 chunk 带上 `source` 和编号，给后面的引用归因（第 5 篇）和调试留好线索。
- 指令要求只依据资料。system 里明确写「只依据 context」，防止模型脱离资料乱编。

---

## 4. k 选多大：召回和噪声的权衡

`k`，也就是注入几条 chunk，是 RAG 里最容易被忽视的参数。它不是越大越好：

```
k 太小 → 漏掉答案所在的 chunk（召回不足）
k 太大 → 噪声 chunk 稀释注意力 + 烧 token + context rot
```

| k 值 | 适用场景 | 风险 |
|------|----------|------|
| 1~3 | 事实问答，答案集中在一处 | 召回不足，问题稍复杂就漏 |
| 5~8 | 通用问答、客服（最常用的区间） | 比较平衡，大多数场景默认从这里开始 |
| 15~30 | 综述类，需要跨多篇文档归纳 | 必须配重排，否则噪声压过信号 |
| 50+ | 几乎都是错的（除非是给 reranker 当召回池） | context rot 严重，成本也高 |

实际做法是分两阶段取，再按预算裁，k 不写死：

```python
# ✅ 推荐：召回放宽，重排收紧，再按 token 预算裁剪
candidates = retriever.search(query, k=50)        # 召回宽，宁多勿漏
ranked = reranker.rerank(query, candidates)       # 精排（机制见 Embedding 手册）
final = pack_to_budget(ranked, max_tokens=4000)   # 按预算贪心填充（见本章第 4 篇）
```

召回阶段 k 取大，注入阶段 k 取小。召回时宁可多拿（top-50），靠 reranker 把真正相关的排到前面，最后只注入放得进 token 预算的那几条。k 还应该随 query 的复杂度调整：单点的事实问答 k=3 就够了，「对比 A 和 B 两个方案」这类要看多篇文档的，就把 k 调大并配上重排。另外，chunk 长短不一，按 token 填充比固定取 5 条更好控制，第 4 篇会细讲。

---

## 5. RAG 不只是检索一次

很多人以为 RAG 就是检索一次、拼进去、生成完，但这只是静态的单轮 RAG。实际中还有：

- 动态检索：每一轮对话都按当前 query 重新检索（第 2 篇）。
- Just-in-time 检索，也叫 agentic 检索：不预先检索，把搜索做成工具，让模型自己决定查什么、查几次（第 3 篇）。Claude Code 这类 coding agent 找代码时主要就是这么做的。

在上下文工程里，RAG 指的是这一整类动态供给上下文的方法，单轮拼接只是其中最简单的一种。

---

## 下一步

- [静态注入 vs 动态检索](./02-static-vs-dynamic.md)：什么时候选哪个
- [Just-in-time 检索 / Agentic Search](./03-just-in-time.md)：把检索做成工具，让模型按需查
- [检索结果的排序、裁剪、去重](./04-rank-trim.md)：检索结果进窗口前的处理
- 跨章回顾窗口预算分配：[按预算分配上下文（Context Budget）](../01-foundations/05-context-budget.md)
