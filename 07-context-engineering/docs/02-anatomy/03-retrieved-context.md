# 检索内容：按需放进窗口的外部知识

> RAG 检索回来的 chunk 是按需注入、用完就丢的外部知识，用来补模型参数里没有的、或者已经过期的事实。但放得越多，准确率可能越低（context rot）。要考虑的是放多少、放在哪、怎么标引用，而不是把窗口塞满。

---

## 1. 检索内容在上下文里的角色

模型的知识有两个来源：

| 来源 | 特点 | 适合 |
|------|------|------|
| 参数知识（在权重里） | 训练时学到的，不花 token，随取随用 | 通用常识、语言能力、稳定的事实 |
| 检索内容（注入上下文） | 实时、可更新、占 token、可以溯源 | 私有数据、最新信息、易变的事实 |

检索内容是临时的，只和当前这一轮相关，用完就丢。它和 system prompt 正好相反：system 常驻不变，检索结果每轮按 query 替换。

```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},        # 常驻
    {"role": "user", "content": f"""请基于以下资料回答问题。

<context>
{retrieved_chunks}
</context>

问题：{question}"""},                                     # 检索内容随 query 注入
]
```

---

## 2. 什么时候该检索

```text
问题答案稳定且通用（"快速排序的复杂度"）  → 信参数知识，不必检索
问题涉及私有/最新/易变（"我们公司Q3退款政策") → 必须检索，别信参数
```

参数知识有两个硬伤：会过期，训练截止之后的事它不知道；会幻觉，编出看似合理的细节。检索内容用真实文档补上这两个洞，代价是占预算，还要做一套工程（切块、向量化、召回、排序）。

---

## 3. 放多少：不是越多越好

直觉上多召回一些更保险，但**检索内容放多了准确率会下降**。下表的档位是常见经验值（示意），具体拐点要用自己的评测集量：

| 放入 chunk 数 | 现象 |
|--------------|------|
| 太少（1–2） | 召回不全，漏掉关键信息，答不全 |
| 适中（3–6） | 信号集中，命中率最高 |
| 太多（15+） | 关键 chunk 被噪声淹没，出现 lost in the middle，模型抓错段落，准确率反而下降 |

这就是 context rot：上下文越长，模型分给每个 token 的有效注意力越少。20 个 chunk 里只有 2 个相关，模型很可能被另外 18 个带偏。

```python
# ❌ 召回 top-20 全塞进去，赌模型自己挑
chunks = retriever.search(query, top_k=20)
context = "\n\n".join(c.text for c in chunks)

# ✅ 召回多、精排后只放少量高相关，并设 token 预算上限
candidates = retriever.search(query, top_k=20)
reranked = reranker.rerank(query, candidates)[:5]   # 重排后取前 5

budget, picked = 2000, []   # token 预算；下面按每 token 约 4 个字符粗估（英文口径，中文要用 tokenizer 实数）
for c in reranked:
    if (used := sum(len(x.text) for x in picked)) + len(c.text) > budget * 4:
        break
    picked.append(c)
context = "\n\n".join(f"[doc{i+1}] {c.text}" for i, c in enumerate(picked))
```

召回阶段可以放宽，注入阶段要收紧，中间靠 rerank 把信噪比提上来。

---

## 4. 放哪里：位置影响命中

长上下文里有 lost in the middle 的问题：开头和结尾的信息最受关注，正中间的最容易被忽略。

| 放置位置 | 说明 |
|---------|------|
| 检索内容放在 user 消息里，紧贴问题 | 最常见，相关性强，方便就近引用 |
| 关键 chunk 放在靠前或靠后的位置，别埋在中间 | 避开 lost in the middle |
| 检索内容不要放进 system | 它是易变的，会让 system 的缓存失效（见 [系统指令层：上下文里最稳定的一块](01-system-instructions.md)） |

```text
推荐顺序（单轮）：
  system（稳定指令）
  → <context>检索资料</context>
  → 问题（紧跟在资料后，让模型"读完就答"）
```

---

## 5. 引用格式：让答案可以溯源、可以核对

给每个 chunk 编号、标上来源，要求模型在答案里注明引用。这样能减少幻觉，方便核查，前端也可以做高亮跳转。

```python
context = "\n\n".join(
    f"[{i+1}] 来源：{c.source}\n{c.text}" for i, c in enumerate(picked)
)
system = """基于 <context> 回答。
- 每个论断后用 [编号] 标注依据。
- <context> 里没有的，明确说"资料中未提及"，不要编造。"""
# 期望输出： "Q3 起退款需在 7 天内申请 [2]，超时不予受理 [3]。"
```

> Anthropic 还提供原生的 Citations 功能，模型可以直接返回结构化的引用区间，不用自己解析。

---

## 6. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| 把 top_k 拉满当保险 | 准确率下降，成本飙升 | 召回放宽，rerank，注入收紧 |
| 检索内容写进 system | 缓存失效，指令被稀释 | 放进 user 消息 |
| 不标来源 | 没法核对，幻觉很难发现 | 编号，并强制引用 |
| chunk 切得太大 | 一段里混了好几个主题，召回不准 | 合理切块（见 Embedding 手册 [固定 vs 语义 vs 递归切分](../../../06-embedding/docs/04-chunking/02-strategies.md)） |
| 资料里没有也硬答 | 幻觉 | 明确允许回答「未提及」 |

---

## 下一步

- 把 RAG 当作上下文来源的完整工程：[RAG 作为上下文来源](../03-retrieval/01-rag-as-context.md)
- 为什么 system 里不放知识：[系统指令层：上下文里最稳定的一块](01-system-instructions.md)
- 各部分的拼装顺序和分隔符：[组织结构：分隔符、XML/Markdown 与顺序](06-structure.md)
- 上下文过长时的退化机制：[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)
