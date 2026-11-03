# CE 07-04：长上下文的注意力优化实操

> 模型在长上下文里的注意力分布不均匀，开头和结尾最受关注，中间容易被忽略（lost-in-the-middle）。模型改不了，能改的是内容放在哪、怎么排、在哪里重述。这篇只讲实操：关键信息放头尾，用结构化标记切分，把任务在末尾重述一遍，按相关性排列 chunk。

---

## 1. 先认清 lost-in-the-middle

有一个经典实验：把一条关键信息（needle）放到长上下文的不同位置，然后向模型提问。结果是一条 U 形曲线，放在开头或结尾时准确率高，放在中间时明显下降。

```
回答准确率（lost-in-the-middle 经验曲线）
高 │■                                  ■
   │ ■■                            ■■
   │   ■■■                     ■■■
低 │       ■■■■■■■■■■■■■■■
   └──────────────────────────────────
    开头         中间            结尾
```

原因在[长上下文模型现状](./01-long-context-models.md)里讲过：attention 和位置编码让首尾得到更稳定的注意力，越长越稀释。这个特性改不了，上下文工程做的很多事都是在绕开它。

---

## 2. 关键信息放头尾，别埋中间

这是最直接的一条：把模型最需要看到的内容，放在它最容易看到的位置，也就是开头和结尾。

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

正确写法里，关键规则说了两次，开头一次，结尾再呼应一次。这就是第 4 节要讲的重述。

---

## 3. 用结构化标记切分

长上下文是一长串连续的 token，模型容易分不清哪段是哪段。用清晰的分隔标记把各部分框起来，模型定位内容的能力会明显提高：

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

- Claude 对 XML 标签的响应特别好，官方也这样推荐。
- 给每段加上 `id` / `title`，既帮模型定位，也方便它引用来源。
- 标签本身就是锚点，比纯文本堆在一起更容易被注意力抓住。

---

## 4. 任务 / 指令重述在末尾

长文档场景下，指令要放在末尾，至少头尾各放一次。

如果把任务写在 300K 文档的最开头，模型读到末尾时，开头的指令已经隔得很远，注意力衰减了。把任务在最末尾重述一遍，紧挨着生成的位置，模型基本一定能看到。

```python
# ✅ 头部给背景，尾部紧贴生成位置重述真正的任务
prompt = f"""你将分析一份长合同，找出所有对乙方不利的条款。

=== 合同全文 ===
{very_long_contract}
=== 合同结束 ===

现在执行任务：逐条列出对乙方不利的条款，每条注明位置和不利原因。
只输出列表，不要寒暄。"""
```

几种位置的经验对比：

| 指令位置 | 短上下文（<8K） | 长上下文（>100K） |
|----------|----------------|------------------|
| 只在开头 | 通常够用 | 容易被遗忘，文档越长越明显 |
| 只在末尾 | 可以 | 好，紧贴生成位置（Anthropic 的建议） |
| 头尾各一次 | 略冗余但稳 | 最稳，推荐 |

> 两家官方的说法不完全一样：Anthropic 建议把长文档放在前面、问题和指令放在最后；OpenAI 的 GPT-4.1 提示指南建议指令在资料前后各放一次，只能放一次时放在资料上方更好。头尾各放一次，两边的建议都照顾到了。

---

## 5. Chunk 按相关性排序（reorder by relevance）

在 RAG 或多文档场景里，检索回来一批 chunk，怎么排进上下文影响很大。

朴素的做法是按检索分数从高到低排。这样最相关的内容全堆在开头，结尾留给低相关的，白白浪费了结尾也是高注意力区这一点。

实操上有两种策略：

- 首尾夹击：最相关的 chunk 放在开头和结尾，次相关的放中间，用上 U 形曲线的两个高点。
- 末尾置顶：末尾紧挨生成位置、注意力最强，干脆把最相关的放在最后，紧挨着问题。

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

哪种更好取决于任务和模型，建议用自己的数据做 A/B 测试，别只信一条规则。

---

## 6. 几条实证 tips 汇总

| Tip | 说明 |
|-----|------|
| 关键信息放头尾 | U 形曲线，中间是注意力洼地 |
| 指令头尾各重述一次 | 输入很长时，只放开头会被遗忘 |
| 任务紧贴生成位置（末尾） | 末尾注意力最强，离输出最近 |
| 用 XML 或分隔标记切分 | 给模型清晰的边界和锚点，便于定位与引用 |
| chunk 首尾夹击或末尾置顶 | 别把高相关的全堆在开头，结尾也是高注意力区 |
| 给每段加 id / title | 帮模型定位，也能让它标注来源 |
| 能少塞就少塞 | 最有效的注意力优化，是让上下文本身短而相关 |
| 用数据做 A/B 测试 | 排序策略因模型和任务而异，别只凭经验 |

表里最要紧的是倒数第二条。注意力优化能做到的上限，是不让上下文变得那么长。能用 RAG 筛出 30K 相关内容，就别硬塞 300K 再去跟 lost-in-the-middle 较劲（见[长上下文 vs RAG](./02-long-context-vs-rag.md)）。

---

## 7. 常见坑

| 坑 | 说明 |
|----|------|
| 关键约束埋在长文档中间 | 大概率被忽略，要放在头尾 |
| 指令只放在最开头 | 输入很长时读到末尾已经忘了，要在末尾重述 |
| chunk 只按分数降序排 | 高相关的全堆在开头，浪费了结尾的高注意力区 |
| 不加分隔标记 | 模型分不清段落边界，定位和引用都会变差 |
| 只信一条排序规则 | 不同模型和任务表现不同，要实测 |
| 用堆长度掩盖检索差 | 检索召回质量差，塞得再长也是噪声，先修检索 |

---

## 8. 下一步

- 回看长上下文模型与 attention 机制：[长上下文模型现状](./01-long-context-models.md)
- 长上下文和 RAG 的取舍：[有了长上下文还要不要 RAG](./02-long-context-vs-rag.md)
- 排好顺序后用缓存省钱：[Prompt Caching 与上下文复用](./03-prompt-caching.md)
- Context Rot 的成因：[Context Rot 与 Lost in the Middle](../01-foundations/03-context-rot.md)
- 上线后怎么观察长上下文的质量：[上下文可观测性](../08-production/01-observability.md)

## 参考资料

- Liu et al., "Lost in the Middle: How Language Models Use Long Contexts": https://arxiv.org/abs/2307.03172
- Anthropic Long context tips: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- OpenAI Prompting guide: https://developers.openai.com/api/docs/guides/prompt-engineering
