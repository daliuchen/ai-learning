# 检索内容：临时塞进窗口的"外挂知识"

> **一句话**：RAG 检索回来的 chunk 是上下文里**按需注入、用完即弃**的外挂知识，补的是模型参数里没有/会过期的事实；但放越多准确率反而越低（context rot），关键在"放多少、放哪里、怎么标引用"，不是"塞满"。

---

## 1. 检索内容在上下文里的角色

模型有两种知识来源：

| 来源 | 特点 | 适合 |
|------|------|------|
| **参数知识**（权重里） | 训练时学到、免费、即取即用 | 通用常识、语言能力、稳定事实 |
| **检索内容**（注入上下文） | 实时、可更新、占 token、可溯源 | 私有数据、最新信息、易变事实 |

检索内容的定位：**临时的、当轮相关的、用完即弃的**。它和 system prompt 正相反——system 是常驻常量，检索是每轮按 query 替换。

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

## 2. 检索内容 vs 参数知识：什么时候该检索

```text
问题答案稳定且通用（"快速排序的复杂度"）  → 信参数知识，不必检索
问题涉及私有/最新/易变（"我们公司Q3退款政策") → 必须检索，别信参数
```

参数知识有两个硬伤：**会过期**（训练截止后的事不知道）、**会幻觉**（编造看似合理的细节）。检索内容用真实文档把这两个洞补上，代价是占预算 + 需要工程（切块、向量化、召回、排序）。

---

## 3. 放多少：不是越多越好

直觉是"多召回点更保险"，但实测**检索内容过载会降准确率**：

| 放入 chunk 数 | 现象 |
|--------------|------|
| 太少（1–2） | 召回不全，漏掉关键信息 → 答不全 |
| 适中（3–6） | 信号集中，命中率最高 ✅ |
| 太多（15+） | 关键 chunk 被噪声淹没、"lost in the middle"、模型抓错段 → 准确率反降 |

这正是 **context rot**：上下文越长，模型对其中每个 token 的有效注意力越被稀释。塞 20 个 chunk 里只有 2 个相关，模型很可能被另外 18 个带偏。

```python
# ❌ 召回 top-20 全塞进去，赌模型自己挑
chunks = retriever.search(query, top_k=20)
context = "\n\n".join(c.text for c in chunks)

# ✅ 召回多、精排后只放少量高相关，并设 token 预算上限
candidates = retriever.search(query, top_k=20)
reranked = reranker.rerank(query, candidates)[:5]   # 重排后取前 5

budget, picked = 2000, []
for c in reranked:
    if (used := sum(len(x.text) for x in picked)) + len(c.text) > budget * 4:
        break
    picked.append(c)
context = "\n\n".join(f"[doc{i+1}] {c.text}" for i, c in enumerate(picked))
```

**召回阶段可以宽，注入阶段必须窄。** 中间用 rerank 把信噪比拉高。

---

## 4. 放哪里：位置影响命中

长上下文里存在"中间塌陷"（lost in the middle）——开头和结尾的信息最被关注，正中间最容易被忽略。

| 放置位置 | 适用 |
|---------|------|
| 检索内容放 **user 消息里、紧贴问题** | 最常见，相关性强、就近引用 ✅ |
| 关键 chunk 放**靠前或靠后**，别埋中间 | 规避 lost-in-the-middle |
| 检索内容**不要放 system** | 它易变，会打碎 system 的缓存（见 [01-system-instructions.md](01-system-instructions.md)） |

```text
推荐顺序（单轮）：
  system（稳定指令）
  → <context>检索资料</context>
  → 问题（紧跟在资料后，让模型"读完就答"）
```

---

## 5. 引用格式：让答案可溯源、可校验

给每个 chunk 编号/标来源，要求模型在答案里引用。好处：减少幻觉、便于核查、前端可做高亮跳转。

```python
context = "\n\n".join(
    f"[{i+1}] 来源：{c.source}\n{c.text}" for i, c in enumerate(picked)
)
system = """基于 <context> 回答。
- 每个论断后用 [编号] 标注依据。
- <context> 里没有的，明确说"资料中未提及"，不要编造。"""
# 期望输出： "Q3 起退款需在 7 天内申请 [2]，超时不予受理 [3]。"
```

> Anthropic 还提供原生 **Citations** 能力，模型可直接返回结构化引用区间，省去手工解析。

---

## 6. 常见坑

| 坑 | 后果 | 对策 |
|----|------|------|
| top_k 拉满当保险 | 准确率反降、成本飙升 | 召回宽 + rerank + 注入窄 |
| 检索内容写进 system | 缓存失效、稀释指令 | 放 user 消息 |
| 不标来源 | 无法校验、幻觉难发现 | 编号 + 强制引用 |
| chunk 切太大 | 一段里混多个主题，召回不精 | 合理切块（见 retrieval 章） |
| 资料里没有也硬答 | 幻觉 | 显式允许"未提及"兜底 |

---

## 7. 小结

- 检索内容是**按需注入、当轮相关、用完即弃**的外挂知识，补参数知识的过期与幻觉。
- 通用稳定信息信参数，私有/最新/易变信息必检索。
- **放多≠更好**：召回宽、精排、注入窄（通常 3–6 个 chunk），否则触发 context rot。
- 位置上紧贴问题、避开正中间，且不要污染 system 的缓存。
- 永远标来源、强制引用、允许"未提及"兜底。

---

## 下一步

- RAG 作为上下文的完整工程：[../03-retrieval/01-rag-as-context.md](../03-retrieval/01-rag-as-context.md)
- 为什么 system 不放知识：[01-system-instructions.md](01-system-instructions.md)
- 各部分的拼装顺序与分隔符：[06-structure.md](06-structure.md)
- 上下文过长的退化机制：[../01-foundations/03-context-rot.md](../01-foundations/03-context-rot.md)
