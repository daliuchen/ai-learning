# PE Advanced 03：RAG Prompting 与引用约束

> 把检索结果塞进 prompt 只是 RAG 的第一步。好的 RAG prompt 还要做到四件事：模型只依据检索结果回答，标明引用来源，找不到就拒绝，不拿自己的知识去补全。这篇只讲 prompt 这一层。

---

## 1. prompt 在 RAG 流程里的位置

```
[用户问题]
   ↓
[Embedding + Vector Search] → 拿到 top-k 段落
   ↓
[构造 prompt：问题 + 检索结果 + 系统指令] ← 本篇关注
   ↓
[LLM 生成答案]
```

Prompt engineering 管的是第三步：怎么把检索结果交给模型。

---

## 2. 标准 RAG prompt 模板

```
你是企业知识助手。请**仅基于** <context> 中的信息回答用户问题。

<context>
[Source 1] {snippet_1}
[Source 2] {snippet_2}
[Source 3] {snippet_3}
</context>

<question>
{user_question}
</question>

规则：
1. 只用 <context> 里的信息。**不要**使用任何外部知识。
2. 答案末尾用 [Source N] 标注引用源。
3. 如果 <context> 不足以回答，回答："根据当前可用信息无法回答"。
4. 不要补全、不要猜测、不要扩展上下文里没有的内容。
```

四条规则各防一种问题，缺一不可：

| 规则 | 防止 |
|------|------|
| 1. 仅用 context | 幻觉 |
| 2. 引用 | 无法追溯 |
| 3. 没找到就拒绝 | 编造 |
| 4. 不扩展 | 过度推断 |

---

## 3. 改进版：每句都要有引用

```
回答时按以下格式：

每一句陈述后跟一个 [Source N] 引用。

示例：
"用户登录失败 [Source 1]。系统会自动锁定账号 30 分钟 [Source 2]。"

如果某条信息找不到引用源，删掉那条。
```

要求每句话都有出处，幻觉会少很多。

---

## 4. 多文档时怎么组织 context

```
<context>
<doc id="1" source="user-manual-v2.3.pdf" updated="2026-03-12">
{snippet}
</doc>

<doc id="2" source="faq-internal.md" updated="2025-11-08">
{snippet}
</doc>

<doc id="3" source="ticket-12345" updated="2026-05-01">
{snippet}
</doc>
</context>
```

把来源、更新日期这些元数据一起给模型，它引用时才能指得准。

---

## 5. 来源冲突时怎么选

多个来源说法矛盾时，prompt 要告诉模型怎么取舍：

```
冲突解决规则：
- 如果两个 source 矛盾，选 updated 日期最新的
- 内部文档 > 外部文档
- 官方 FAQ > 社区帖子
- 引用时标注"基于最新文档"
```

更保守的做法是：

```
如果 source 冲突，列出两种说法 + 各自 source，让用户自己判断。
```

---

## 6. 控制 context 大小

```
context 太短 → 信息不足
context 太长 → 模型 "lost in the middle"
```

我常用的参数：

| 维度 | 推荐 |
|------|------|
| chunk 大小 | 每段 200-500 字 |
| top-k | 3-10 段 |
| 总 context 长度 | 10k token 以内（即使模型支持 100k+） |
| 来源顺序 | 最相关的放最前或最后（模型对首尾的注意力更强） |

---

## 7. Re-rank 与多轮 RAG

### 7.1 Re-rank
向量检索取 top-20，用 cross-encoder 重新排序，再取前 5 放进 prompt，相关性会明显提高。

### 7.2 Multi-hop RAG
一次检索不够时，让模型迭代地发起查询：

```
[Q] 公司 Q3 营收同比？
   ↓
[Retrieve 1] 找到 Q3 营收数字
   ↓
[Q2 from model] 还需要去年 Q3 的数字
   ↓
[Retrieve 2] 找到去年 Q3 数字
   ↓
[Answer] 同比 X%
```

实现上就是 ReAct 循环加一个检索工具，见 [ReAct（Reasoning + Acting）](./01-react.md)。

### 7.3 HyDE（Hypothetical Document Embedding）
先让模型生成一个假设的答案，用这个答案而不是原问题去检索，相关性往往更好。

---

## 8. RAG 评测要多看几项

RAG 的评测集除了看答案对不对，还要看：

| 指标 | 含义 |
|------|------|
| Faithfulness | 答案是否忠实于 context，有没有幻觉 |
| Context recall | 检索到的 context 是否覆盖了标准答案需要的信息 |
| Context precision | 检索结果里相关内容的比例 |
| Answer relevance | 答案是否回答了问题本身 |
| Citation accuracy | 引用是否准确 |

Ragas 是专门做 RAG 评测的库。

---

## 9. context 里没有答案时，防止编造

RAG 最常见的失败是：context 里没有相关信息，模型就用训练数据把答案补上了。两个办法：

### 9.1 严格的拒绝规则

```
重要：如果 <context> 不包含回答问题所需信息，必须返回：
{
  "status": "insufficient_context",
  "reason": "<具体说明哪里信息缺失>"
}

不要：
- 用训练数据补全
- 推测 / 猜测
- 给"通用"答案
```

### 9.2 忠实度检查
答案出来之后，再调一次模型做检查：

```
检查下面答案是否完全基于 context（不含外部知识）：

<context>...</context>
<answer>...</answer>

返回 {"is_faithful": bool, "unsupported_claims": ["...", ...]}
```

---

## 10. 多语言 RAG

问题、答案、context 可能是不同语言：

```
重要：
- <context> 可能是英文，<question> 是中文
- 回答用 <question> 的语言
- 引用时保留原文 + 翻译
```

---

## 11. demo：基础 RAG prompt

```python
import anthropic
client = anthropic.Anthropic()


RAG_SYSTEM = """你是企业知识助手。仅基于 <context> 回答用户问题。

规则：
1. 只用 <context> 信息，不用外部知识
2. 每条陈述末尾加 [Source N] 引用
3. 信息不足时返回 "根据当前文档无法回答"
4. 不补全、不推测
"""


def build_prompt(question: str, sources: list[dict]) -> str:
    ctx_blocks = "\n\n".join(
        f"[Source {i+1}] (from {s['source']})\n{s['text']}"
        for i, s in enumerate(sources)
    )
    return f"""<context>
{ctx_blocks}
</context>

<question>
{question}
</question>"""


SOURCES = [
    {"source": "manual-v2.3", "text": "用户首次登录需要绑定手机号。绑定后无法更改。"},
    {"source": "faq", "text": "修改手机号需要联系客服并提供身份证明。"},
]

QUESTION = "我能改绑手机号吗？怎么改？"

resp = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=1024,
    system=RAG_SYSTEM,
    messages=[{"role": "user", "content": build_prompt(QUESTION, SOURCES)}],
)
print(resp.content[-1].text)
```

预期输出里会带 [Source 1] / [Source 2] 引用。

---

## 12. 常见坑

| 坑 | 排查 |
|----|------|
| 没写「仅基于 context」 | 模型拿训练数据来补 |
| 不要求引用 | 无法追溯，幻觉混进来也看不出 |
| context 太长 | 中间部分的信息被忽略 |
| 没处理信息不足的情况 | 模型硬编答案 |
| 没做忠实度检查 | 上线后用户投诉幻觉 |
| chunk 切得太碎 | 语义边界被切断 |
| 没考虑来源冲突 | 模型挑了过时的信息 |

---

## 13. 跨手册关联

- [LangChain 13：RAG 完整实战](../../../01-langchain/docs/01-langchain/13-rag.md)
- [Pydantic AI 实战 02：RAG 知识库问答 Agent](../../../02-pydantic-ai/docs/06-practice/02-project-rag.md)
- [MCP Practice 01：内部知识库 MCP Server](../../../03-mcp/docs/07-practice/01-project-internal-kb.md)

---

## 14. 下一步

- [多模态 Prompting（图片 / 文档 / 视频）](./04-multimodal.md)
- [Meta-Prompting，用 LLM 写和优化 Prompt](./05-meta-prompting.md)
- [Prompt Injection 防御](./06-injection-defense.md)

## 参考资料

- Ragas: https://docs.ragas.io
- LangChain RAG: https://python.langchain.com/docs/tutorials/rag/
- "HyDE" (Gao et al. 2022): https://arxiv.org/abs/2212.10496
