# CE 07-04：长上下文的注意力优化实操

> **一句话**：模型在长上下文里的注意力不是均匀的——开头和结尾最受关注，中间容易被忽略（lost-in-the-middle）。你没法改模型，但能改「往哪放、怎么排、在哪重述」。这篇全是实操：关键信息放头尾、用结构化标记、把任务重述在末尾、按相关性排 chunk。

---

## 1. 先认清 lost-in-the-middle

经典实验：把一条关键信息（needle）放进长上下文的不同位置，问模型问题。结果是一条 **U 形曲线**——放在开头或结尾，准确率高；放在中间，准确率明显掉。

```
回答准确率（lost-in-the-middle 经验曲线）
高 │■                                  ■
   │ ■■                            ■■
   │   ■■■                     ■■■
低 │       ■■■■■■■■■■■■■■■
   └──────────────────────────────────
    开头         中间            结尾
```

原因在 [01-long-context-models.md](./01-long-context-models.md) 讲过：attention + 位置编码让首尾获得更稳定的注意力，越长越稀释。你改不了模型这个特性，但**整个上下文工程就是在和它对抗**。

---

## 2. 关键信息放头尾，别埋中间

最直接的一条。把模型「最该看到」的东西放在它最容易看到的位置——开头和结尾。

```python
# ❌ 关键约束埋在长文档中间，模型大概率读不到
prompt = f"""{first_half_of_docs}
重要：回答必须用中文，且不得超过 100 字。   # ← 埋在中间，被忽略
{second_half_of_docs}
问题：{question}"""

# ✅ 关键约束放开头，任务和问题放结尾
prompt = f"""重要规则：回答必须用中文，且不得超过 100 字。

=== 参考资料 ===
{all_docs}
=== 资料结束 ===

请依据上述资料回答（记住：中文、100 字以内）。
问题：{question}"""
```

注意 ✅ 版把关键规则**说了两次**——开头一次、结尾呼应一次。这是下面第 4 点的「重述」。

---

## 3. 用结构化标记切分

长上下文是一坨连续 token，模型容易「分不清哪是哪」。用清晰的**分隔标记**把不同部分框起来，能显著提升模型的定位能力：

```python
# ✅ 用 XML 风格标签 / 明确分隔符，让模型清楚每段的边界和角色
prompt = f"""<instructions>
你是法律助手，只依据 <documents> 中的条款作答，每个结论标注来源编号。
</instructions>

<documents>
<doc id="1" title="服务协议">{doc1}</doc>
<doc id="2" title="隐私政策">{doc2}</doc>
</documents>

<question>{question}</question>

<reminder>只依据 documents 作答，标注来源 id。</reminder>"""
```

- Claude 对 **XML 标签**响应尤其好（官方推荐）。
- 给每段加 `id` / `title`，既帮模型定位，又方便它**引用来源**。
- 标记本身就是「锚点」，比纯文本堆叠更容易被注意力抓住。

---

## 4. 任务 / 指令重述在末尾

长文档场景的一条铁律：**指令放末尾，或至少头尾各放一次**。

原因：如果你把任务写在 300K 文档的最开头，模型读到末尾时，开头的指令已经离得很远、注意力衰减。把任务**重述在最末尾**（紧贴生成位置），它几乎一定看得到。

```python
# ✅ 头部给背景，尾部紧贴生成位置重述真正的任务
prompt = f"""你将分析一份长合同，找出所有对乙方不利的条款。

=== 合同全文 ===
{very_long_contract}
=== 合同结束 ===

现在执行任务：逐条列出对乙方不利的条款，每条注明位置和不利原因。
只输出列表，不要寒暄。"""
```

经验对比：

| 指令位置 | 短上下文（<8K） | 长上下文（>100K） |
|----------|----------------|------------------|
| 只在开头 | 通常够用 | **容易被遗忘**，尤其文档很长 |
| 只在末尾 | 可以 | 好，紧贴生成位置 |
| 头尾各一次 | 略冗余但稳 | **最稳**，强烈推荐 |

> OpenAI 和 Anthropic 的长上下文指南都建议：**长输入时把核心指令放在文档之后（末尾）**，效果优于只放开头。

---

## 5. Chunk 按相关性排序（reorder by relevance）

RAG / 多文档场景下，你检索回来一堆 chunk，**怎么排进上下文很重要**。

朴素做法是按检索分数从高到低排——但这会把最相关的全堆在开头，结尾留给低相关的，浪费了「结尾也是高注意力区」这个事实。

两种实操策略：

- **首尾夹击**：把最相关的 chunk 放**开头和结尾**，次相关的放中间。利用 U 形曲线的两个高点。
- **末尾置顶**：既然末尾紧贴生成、注意力最强，干脆把**最相关的放最后**（紧挨问题）。

```python
# ✅ 把检索 chunk 按相关性「首尾夹击」排列，避开中间低注意力区
def reorder_by_relevance(chunks):
    """chunks 已按相关性降序。重排成：高相关在两头，低相关在中间。"""
    chunks = sorted(chunks, key=lambda c: c.score, reverse=True)
    head, tail = [], []
    for i, c in enumerate(chunks):
        (head if i % 2 == 0 else tail).append(c)   # 0,2,4… 进头；1,3,5… 进尾
    return head + tail[::-1]   # tail 反转，让最相关的也贴近结尾

ordered = reorder_by_relevance(retrieved_chunks)
context = "\n\n".join(f"<doc id='{c.id}'>{c.text}</doc>" for c in ordered)
prompt = f"<documents>\n{context}\n</documents>\n\n问题：{question}\n请标注来源 id。"
```

哪种更好取决于任务和模型，**建议拿自己的数据 A/B 测**——别迷信单一规则。

---

## 6. 几条实证 tips 汇总

| Tip | 说明 |
|-----|------|
| 关键信息放头尾 | U 形曲线，中间是注意力洼地 |
| 指令头尾各重述一次 | 长输入下只放开头会被遗忘 |
| 任务紧贴生成位置（末尾） | 末尾注意力最强，离输出最近 |
| 用 XML / 分隔标记切分 | 给模型清晰的边界和锚点，便于定位与引用 |
| chunk 首尾夹击或末尾置顶 | 别把高相关全堆开头，结尾也是高注意力区 |
| 给每段加 id / title | 帮定位，且能让模型标注来源 |
| 能少塞就少塞 | 最强的注意力优化是「上下文本身就短而相关」 |
| 拿数据 A/B 测 | 排序策略因模型 / 任务而异，别只信经验 |

最后这条最重要：**注意力优化的天花板是「不让上下文变那么长」**。能用 RAG 筛出 30K 相关内容，就别硬塞 300K 再去和 lost-in-the-middle 搏斗（见 [02-long-context-vs-rag.md](./02-long-context-vs-rag.md)）。

---

## 7. 常见坑

| 坑 | 说明 |
|----|------|
| 关键约束埋长文档中间 | 大概率被忽略，放头尾 |
| 指令只放最开头 | 长输入下读到末尾已「忘了」，要在末尾重述 |
| chunk 只按分数降序排 | 高相关全堆开头，浪费了结尾高注意力区 |
| 不加分隔标记 | 模型分不清段落边界，定位和引用都差 |
| 迷信单一排序规则 | 不同模型 / 任务表现不同，要实测 |
| 用堆长度掩盖检索差 | 检索召回烂，塞再长也是噪声，先修检索 |

---

## 8. 下一步

- 📖 回看长上下文模型与 attention 机制 → [01-long-context-models.md](./01-long-context-models.md)
- 📖 长上下文 vs RAG 的取舍 → [02-long-context-vs-rag.md](./02-long-context-vs-rag.md)
- 📖 排好顺序后用缓存省钱 → [03-prompt-caching.md](./03-prompt-caching.md)
- 📖 Context Rot 的成因细讲 → [../01-foundations/03-context-rot.md](../01-foundations/03-context-rot.md)
- 📖 上线后怎么评测长上下文质量 → [../08-production/01-observability.md](../08-production/01-observability.md)

## 参考资料

- Liu et al., "Lost in the Middle: How Language Models Use Long Contexts": https://arxiv.org/abs/2307.03172
- Anthropic Long context tips: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/long-context-tips
- OpenAI Prompting guide: https://platform.openai.com/docs/guides/prompt-engineering
